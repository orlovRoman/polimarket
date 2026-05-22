import os
import sys
import threading
from datetime import datetime
from dotenv import load_dotenv

# Добавляем корень проекта в путь поиска модулей
sys.path.append(os.getcwd())

from agents.shared.adapters.polymarket import PolymarketAdapter
from agents.shared.python.db import (
    save_market, init_db, save_signal, get_last_analyzed_price,
    mark_market_analyzed, cleanup_stale_signals, save_price_point,
    get_price_history, get_new_correlations, mark_correlations_notified,
    get_markets_on_cooldown, add_discussion_message
)
from agents.shared.python.db import get_memory, save_memory
from agents.polymarket_mispricing_agent.src.agent import ScoutAgent
from agents.polymarket_insider_agent.src.agent import ShadowAgent
from agents.polymarket_news_agent.src.agent import HeraldAgent
from agents.orchestrator.src.agent import NexusAgent
from agents.shared.utils.database import DatabaseManager
from agents.shared.python.market_selector import MarketSelector

# Интервал между скринингами (секунды). 30 мин = 1800 сек
SCREENING_INTERVAL_SEC = 1800

# Глобальная блокировка для предотвращения одновременного запуска планового скана и ручного /scan
_scan_lock = threading.Lock()

def run_team_discussion(log_callback=None, summary_callback=None, category=None, market_id=None):
    """
    Координирует обсуждение рынков командой AI-агентов.
    Включает двухстадийный pipeline: SCREENER (NEXUS) → SCOUT → SHADOW → HERALD.
    Возвращает количество обработанных рынков.
    """
    if not _scan_lock.acquire(blocking=False):
        print("Сканирование уже выполняется (другой поток). Пропускаем.")
        return 0
    
    try:
        return _run_team_discussion_inner(log_callback, summary_callback, category, market_id)
    finally:
        _scan_lock.release()

def _run_team_discussion_inner(log_callback=None, summary_callback=None, category=None, market_id=None):
    """Внутренняя реализация обсуждения (защищена _scan_lock)."""
    def log(msg):
        try:
            print(msg)
        except UnicodeEncodeError:
            try:
                enc = sys.stdout.encoding or 'utf-8'
                print(str(msg).encode(enc, errors='replace').decode(enc))
            except Exception:
                print(str(msg).encode('ascii', errors='replace').decode('ascii'))
        if log_callback:
            try:
                log_callback(msg)
            except Exception as e:
                print(f"Ошибка в log_callback: {e}")

    # Функция фоновой отправки оповещений в Telegram (если нет интерактивного callback)
    def send_telegram_alert(text: str):
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            import requests
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"Ошибка фонового оповещения Telegram: {e}")

    if not summary_callback:
        summary_callback = send_telegram_alert

    # Загружаем настройки и инициализируем базу данных
    load_dotenv()
    init_db()
    
    # Получаем лимит сканирования из БД (Layer 1 memory)
    db = DatabaseManager()
    scan_limit = int(db.get_memory("scan_limit") or 10)  # Дефолт: 10 рынков за цикл
    log(f"Параметры сессии: Лимит запросов (рынков) = {scan_limit}")

    # Очищаем устаревшие сигналы перед новым сканом
    stale = cleanup_stale_signals()
    if stale > 0:
        log(f"Очищено устаревших сигналов: {stale}")

    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        log("Критическая ошибка: GOOGLE_API_KEY не найден в .env!")
        return

    # Инициализируем адаптер платформы и агентов
    adapter = PolymarketAdapter()
    scout = ScoutAgent(api_key=key)
    shadow = ShadowAgent(api_key=key)
    herald = HeraldAgent(api_key=key)

    # ===================================================================
    # СТАДИЯ 0: СКРИНИНГ (NEXUS) — каждые 30 минут
    # Загружает ВСЕ рынки, отбирает Top-30, ищет корреляции
    # ===================================================================
    screened_market_ids = None
    
    if not category and not market_id:  # Скрининг только в режиме "авто-микс" и без точечного market_id
        last_screen_raw = get_memory("last_screen_time")
        now = datetime.utcnow()
        needs_screening = True
        
        if last_screen_raw:
            try:
                last_screen = datetime.fromisoformat(last_screen_raw)
                elapsed = (now - last_screen).total_seconds()
                if elapsed < SCREENING_INTERVAL_SEC:
                    needs_screening = False
                    log(f"Скрининг не требуется (прошло {elapsed/60:.0f} мин из {SCREENING_INTERVAL_SEC/60:.0f})")
                    screened_market_ids = get_memory("screened_market_ids")
            except (ValueError, TypeError):
                pass
        
        if needs_screening:
            log("--- 0. NEXUS скринирует все рынки ---")
            try:
                all_compact = adapter.list_all_markets_compact()
                log(f"  Загружено {len(all_compact)} рынков для скрининга")
                
                nexus = NexusAgent(api_key=key)
                screen_result = nexus.screen_markets(all_compact, top_n=30)
                
                screened_market_ids = screen_result.get("top_candidates", [])
                correlations_count = len(screen_result.get("correlations", []))
                
                # Кешируем результат
                save_memory("screened_market_ids", screened_market_ids, category='cache', ttl=SCREENING_INTERVAL_SEC)
                save_memory("last_screen_time", now.isoformat(), category='cache', ttl=SCREENING_INTERVAL_SEC)
                
                log(f"  NEXUS отобрал {len(screened_market_ids)} кандидатов, найдено {correlations_count} корреляций")
                
                # Алерт о новых корреляциях
                if correlations_count > 0 and summary_callback:
                    _send_correlation_alerts(summary_callback)
                    
            except Exception as e:
                log(f"  Ошибка скрининга: {e}. Используем стандартный отбор.")
                screened_market_ids = None
    
    # ===================================================================
    # СТАДИЯ 1: ОТБОР РЫНКОВ
    # ===================================================================
    cat_msg = f" в категории '{category}'" if category else " (авто-микс)"
    if market_id:
        cat_msg = f" (точечный горячий анализ {market_id})"
    log(f"\n--- 1. Поиск новых рынков{cat_msg} ---")
    
    if market_id:
        log(f"  Загружаем конкретный рынок по запросу: {market_id}")
        markets = []
        try:
            m = adapter.get_market(market_id)
            if m:
                markets.append(m)
        except Exception as e:
            log(f"  Ошибка загрузки рынка {market_id}: {e}")
    elif screened_market_ids and not category:
        # Используем отфильтрованные NEXUS'ом рынки
        log(f"  Используем {len(screened_market_ids)} рынков от NEXUS SCREENER")
        markets = []
        for mid in screened_market_ids[:scan_limit]:
            try:
                m = adapter.get_market(mid)
                if m:
                    markets.append(m)
            except Exception:
                continue
        log(f"  Загружено полных данных: {len(markets)} рынков")
    else:
        # Стандартный путь: MarketSelector
        selector = MarketSelector(adapter)
        markets = selector.select(total_limit=scan_limit, category=category)
        
        if not category:
            auto_cat = selector.get_auto_category()
            log(f"  Категория ротации: {auto_cat}")
    
    log(f"  Отобрано рынков после фильтрации: {len(markets)}")

    for m in markets:
        save_market(m)  # Сохраняем/обновляем данные о рынке в БД

    # ===================================================================
    # СТАДИЯ 2: ОБСУЖДЕНИЕ (SCOUT → SHADOW → HERALD)
    # ===================================================================
    log(f"\n--- 2. Обсуждение идей (SCOUT + SHADOW + HERALD) ---")
    log(f"Всего рынков для проверки: {len(markets)}")
    
    # Получаем список рынков, которые были проанализированы за последние N часов (кулдаун)
    from config import MARKET_COOLDOWN_HOURS
    cooldown_markets = get_markets_on_cooldown(MARKET_COOLDOWN_HOURS)
    
    new_markets_found = False
    for m in markets:
        # Проверяем, анализировали ли мы этот рынок ранее при такой же цене (если это не точечный анализ по market_id)
        last_price = get_last_analyzed_price(m.id)
        
        if last_price is not None and not market_id:
            price_diff = abs(last_price - m.price)
            is_cooldown = m.id in cooldown_markets
            
            # Гибридный триггер: анализируем, если цена изменилась >= 3% ИЛИ прошло >= 6 часов (не в кулдауне)
            if price_diff < 0.03 and is_cooldown:
                log(f"\n[РЫНОК]: {m.title} (Цена {m.price} стабильна и рынок на 6-часовом кулдауне, пропускаем)")
                # Всё равно записываем price point для истории
                save_price_point(m.id, m.price)
                continue
            elif not is_cooldown:
                log(f"\n[РЫНОК]: {m.title} (Истек 6-часовой кулдаун, пересматриваем)")
            else:
                log(f"\n[РЫНОК]: {m.title} (Цена изменилась: {last_price} -> {m.price}, пересматриваем)")
        else:
            if market_id:
                log(f"\n[РЫНОК]: {m.title} (Точечный принудительный анализ)")
            else:
                log(f"\n[РЫНОК]: {m.title} (Новый рынок в системе)")
            
        new_markets_found = True
        
        # Записываем текущую цену в историю
        save_price_point(m.id, m.price)
        
        # ЭТАП 1: SCOUT ищет математическую недооценку (Edge)
        log("  SCOUT оценивает...")
        signal = scout.estimate_market(m)
        
        opinion_shadow = None
        opinion_herald = None

        if signal:
            log(f"  SCOUT: Нашел недооценку! Ожидаемый Edge: {signal.edge:.2f}")
            
            # Получаем ордербук для SHADOW (если есть token_id)
            orderbook = None
            if m.tokens:
                try:
                    orderbook = adapter.get_orderbook(m.tokens[0])
                    if orderbook:
                        log(f"  Ордербук загружен: спред={orderbook.get('spread')}, bid_depth=${orderbook.get('bid_depth_5', 0):,.0f}")
                except Exception as e:
                    log(f"  Ордербук недоступен: {e}")
            
            # Получаем историю цен для SHADOW
            price_hist = get_price_history(m.id, hours=24)
            
            # ЭТАП 2: SHADOW анализирует ордербук и ликвидность
            log("  SHADOW проверяет...")
            opinion_shadow = shadow.analyze_idea(m, signal.details, orderbook=orderbook, price_history=price_hist)
            
            # ЭТАП 3: HERALD ищет новости и проверяет, не завершилось ли событие досрочно
            log("  HERALD проверяет...")
            opinion_herald = herald.analyze_idea(m, signal.details)
            
            # Сохраняем мнения всех агентов в базу данных для истории
            for op in [opinion_shadow, opinion_herald]:
                if op:
                    add_discussion_message(m.id, op.agent_name, op.opinion, op.confidence, op.agree)
                    status = "✅ СОГЛАСЕН" if op.agree else "❌ НЕ СОГЛАСЕН"
                    log(f"  {op.agent_name}: {status} (Уверенность: {op.confidence})")
                    log(f"  Мнение {op.agent_name}: {(op.opinion or '')[:100]}...")

            # ЛОГИКА КОНСЕНСУСА:
            # Идея принимается только если оба эксперта (Shadow и Herald) согласны
            # и их уверенность в своем решении выше порога (0.6)
            if opinion_shadow and opinion_herald and \
               opinion_shadow.agree and opinion_herald.agree and \
               opinion_shadow.confidence > 0.6 and opinion_herald.confidence > 0.6:
                
                log("  !!! ИДЕЯ ПОДТВЕРЖДЕНА КОНСЕНСУСОМ. Генерируем сигнал.")
                save_signal(signal)
            else:
                log("  --- Консенсус не достигнут. Идея отклонена экспертами.")
        else:
            log("  SCOUT: Математическое преимущество не обнаружено.")
            
        # Формируем и отправляем краткое резюме для Telegram-интерфейса
        if summary_callback:
            summary_text = f"🗣 <b>Обсуждение рынка:</b>\n<a href='{m.url}'>{m.title}</a>\n\n"
            if signal:
                summary_text += f"<b>SCOUT</b> 🟢 Нашел потенциал (Edge: {signal.edge:.2f})\n\n"
            else:
                summary_text += f"<b>SCOUT</b> ⚪️ Идея не найдена.\n\n"
            
            if opinion_shadow:
                status = "✅ СОГЛАСЕН" if opinion_shadow.agree else "❌ ПРОТИВ"
                summary_text += f"<b>SHADOW</b> {status} (Увер: {opinion_shadow.confidence})\n<i>{opinion_shadow.opinion}</i>\n\n"
            
            if opinion_herald:
                status = "✅ СОГЛАСЕН" if opinion_herald.agree else "❌ ПРОТИВ"
                summary_text += f"<b>HERALD</b> {status} (Увер: {opinion_herald.confidence})\n<i>{opinion_herald.opinion}</i>\n\n"
            
            if signal and opinion_shadow and opinion_herald and \
               opinion_shadow.agree and opinion_herald.agree and \
               opinion_shadow.confidence > 0.6 and opinion_herald.confidence > 0.6:
                summary_text += "✨ <b>ИТОГ: Консенсус достигнут! Идея сохранена.</b>"
            elif signal:
                summary_text += "🛑 <b>ИТОГ: Консенсус не достигнут.</b>"
            else:
                summary_text += "🛑 <b>ИТОГ: Нет предмета для обсуждения.</b>"
                
            summary_callback(summary_text)
            
        # Отмечаем рынок как проанализированный с текущей ценой
        mark_market_analyzed(m.id, m.price)

    if not new_markets_found:
        log("\nНет рынков для обсуждения (цены не изменились).")
    
    # Возвращаем количество фактически обработанных рынков
    return 1 if new_markets_found else 0


def _send_correlation_alerts(summary_callback):
    """Отправляет алерты о новых корреляциях в Telegram."""
    try:
        new_corrs = get_new_correlations()
        if not new_corrs:
            return
        
        type_icons = {
            'causal': '🔄 ПРИЧИННАЯ',
            'inverse': '↕️ ОБРАТНАЯ',
            'arbitrage': '⚡ АРБИТРАЖ',
            'thematic': '🔗 ТЕМАТИЧЕСКАЯ'
        }
        
        alert_text = f"🔗 <b>Обнаружено {len(new_corrs)} корреляций между рынками:</b>\n\n"
        
        for i, c in enumerate(new_corrs[:5], 1):  # Макс 5 корреляций за алерт
            corr_type = type_icons.get(c['correlation_type'], c['correlation_type'])
            alert_text += (
                f"<b>{i}. {corr_type}</b> ({c['confidence']:.0%})\n"
                f"  📍 {c['title_a']}\n"
                f"  📍 {c['title_b']}\n"
                f"  → <i>{c['description']}</i>\n\n"
            )
        
        summary_callback(alert_text)
        
        # Помечаем как отправленные
        mark_correlations_notified([c['id'] for c in new_corrs[:5]])
        
    except Exception as e:
        print(f"Ошибка отправки корреляций: {e}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Запуск обсуждения рынков командой агентов")
    parser.add_argument("--market_id", type=str, help="ID конкретного рынка для точечного горячего анализа")
    parser.add_argument("--category", type=str, help="Категория для сканирования")
    args = parser.parse_args()
    
    run_team_discussion(category=args.category, market_id=args.market_id)