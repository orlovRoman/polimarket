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
from agents.polymarket_swing_agent.src.agent import SwingAgent
from agents.polymarket_insider_agent.src.agent import ShadowAgent
from agents.orchestrator.src.agent import NexusAgent
from agents.shared.utils.web_search import fetch_rss_news, fetch_reddit_news
from agents.shared.utils.database import DatabaseManager
from agents.shared.python.market_selector import MarketSelector

# Интервал между скринингами (секунды). 30 мин = 1800 сек
SCREENING_INTERVAL_SEC = 1800

# Глобальная блокировка для предотвращения одновременного запуска планового скана и ручного /scan
_scan_lock = threading.Lock()

LOCK_FILE = os.path.join("vault", "scan.lock")
LOCK_TIMEOUT_SEC = 600  # 10 минут макс на один скан

def acquire_process_lock() -> bool:
    """Пытается захватить межпроцессный замок (File Lock)."""
    import time
    os.makedirs("vault", exist_ok=True)
    
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                data = f.read().strip().split(",")
                if len(data) == 2:
                    lock_time = float(data[0])
                    lock_pid = int(data[1])
                    
                    # Проверяем, жив ли процесс
                    process_alive = False
                    if sys.platform != "win32":
                        try:
                            os.kill(lock_pid, 0)
                            process_alive = True
                        except OSError:
                            pass
                    
                    elapsed = time.time() - lock_time
                    if elapsed < LOCK_TIMEOUT_SEC:
                        if sys.platform != "win32" and not process_alive:
                            print(f"[Lock] Обнаружен устаревший замок от мертвого процесса {lock_pid}. Сбрасываем.")
                        else:
                            print(f"[Lock] Сканирование заблокировано процессом PID {lock_pid} (активен {elapsed:.0f} сек).")
                            return False
                    else:
                        print(f"[Lock] Обнаружен зависший замок (прошло {elapsed:.0f} сек). Сбрасываем.")
        except Exception as e:
            print(f"[Lock] Ошибка чтения замка: {e}. Сбрасываем.")
            
    try:
        with open(LOCK_FILE, "w") as f:
            f.write(f"{time.time()},{os.getpid()}")
        return True
    except Exception as e:
        print(f"[Lock] Не удалось создать файл блокировки: {e}")
        return False

def release_process_lock():
    """Освобождает межпроцессный замок."""
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
            print("[Lock] Межпроцессный замок успешно снят.")
    except Exception as e:
        print(f"[Lock] Ошибка удаления файла блокировки: {e}")

def run_team_discussion(log_callback=None, summary_callback=None, category=None, market_id=None, state_callback=None):
    """
    Координирует обсуждение рынков командой AI-агентов.
    Включает двухстадийный pipeline: SCREENER (NEXUS) → SCOUT → SHADOW → HERALD.
    Возвращает количество обработанных рынков.
    """
    # 1. Проверяем внутрипроцессную блокировку
    if not _scan_lock.acquire(blocking=False):
        print("Сканирование уже выполняется (другой поток). Пропускаем.")
        return 0
        
    # 2. Проверяем межпроцессную блокировку
    if not acquire_process_lock():
        _scan_lock.release()
        return 0
    
    try:
        return _run_team_discussion_inner(log_callback, summary_callback, category, market_id, state_callback)
    finally:
        release_process_lock()
        _scan_lock.release()

def _run_team_discussion_inner(log_callback=None, summary_callback=None, category=None, market_id=None, state_callback=None):
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

    state = {
        "category": category if category else "Авто-микс",
        "total_markets": 0,
        "current_market_index": 0,
        "current_market_title": "Инициализация...",
        "current_market_url": "",
        "scout_status": "⏳ Ожидает",
        "swing_status": "⏳ Ожидает",
        "shadow_status": "⏳ Ожидает",
        "herald_status": "⏳ Ожидает",
        "ideas_found": 0,
        "stage": "Скрининг NEXUS"
    }

    def update_state(**kwargs):
        state.update(kwargs)
        if state_callback:
            try:
                state_callback(state.copy())
            except Exception as e:
                print(f"Ошибка state_callback: {e}")

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
    swing = SwingAgent(api_key=key)
    shadow = ShadowAgent(api_key=key)

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
    update_state(stage="Отбор рынков")
    
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
    log(f"\n--- 2. Обсуждение идей (SCOUT + SWING + SHADOW) ---")
    
    update_state(total_markets=len(markets), stage="Обсуждение (SCOUT + SWING + SHADOW)")
    
    # Получаем список рынков, которые были проанализированы за последние N часов (кулдаун)
    from config import MARKET_COOLDOWN_HOURS
    cooldown_markets = get_markets_on_cooldown(MARKET_COOLDOWN_HOURS)
    
    new_markets_found = False
    for i, m in enumerate(markets, 1):
        update_state(
            current_market_index=i,
            current_market_title=m.title,
            current_market_url=m.url,
            scout_status="⏳ Ожидает",
            swing_status="⏳ Ожидает",
            shadow_status="⏳ Ожидает"
        )
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
                update_state(scout_status="⚪️ Пропущен (Кулдаун)", swing_status="⚪️ Пропущен (Кулдаун)")
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
        
        # Централизованный сбор новостей
        log("  Скачиваем новости (RSS + Reddit)...")
        news_titles = fetch_rss_news(m.title)
        reddit_posts = fetch_reddit_news(m.title)
        
        # ЭТАП 1: SCOUT ищет математическую недооценку (Edge), а SWING_TRADER - хайп
        log("  SCOUT и SWING оценивают...")
        update_state(scout_status="🔄 Считает вероятности...", swing_status="🔄 Оценивает хайп...")
        
        signal = scout.estimate_market(m, news_titles, reddit_posts)
        swing_signal = swing.estimate_market(m, news_titles, reddit_posts)
        
        opinion_shadow = None

        if signal or swing_signal:
            active_signal = swing_signal if swing_signal else signal
            
            if signal:
                log(f"  SCOUT: Нашел недооценку! Ожидаемый Edge: {signal.edge:.2f}")
                update_state(scout_status=f"🟢 Нашел Edge ({signal.edge:.2f})")
            else:
                update_state(scout_status="⚪️ Нет фундамента")
                
            if swing_signal:
                log(f"  SWING: Нашел хайп-потенциал!")
                update_state(swing_status=f"🚀 Ждет памп")
            else:
                update_state(swing_status="⚪️ Нет хайпа")
                
            update_state(shadow_status="🔄 Проверяет ордербук...")
            
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
            opinion_shadow = shadow.analyze_idea(m, active_signal.details, orderbook=orderbook, price_history=price_hist)
            status_sh = "✅ Согласен" if (opinion_shadow and opinion_shadow.agree) else "❌ Против"
            update_state(shadow_status=f"{status_sh} (Увер: {opinion_shadow.confidence if opinion_shadow else 0})", herald_status="🔄 Ищет новости...")
            
            # ЭТАП 3: HERALD ищет новости и проверяет, не завершилось ли событие досрочно
            log("  HERALD проверяет...")
            opinion_herald = herald.analyze_idea(m, active_signal.details)
            status_he = "✅ Согласен" if (opinion_herald and opinion_herald.agree) else "❌ Против"
            update_state(herald_status=f"{status_he} (Увер: {opinion_herald.confidence if opinion_herald else 0})")
            
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
                if signal: save_signal(signal)
                if swing_signal: save_signal(swing_signal)
                update_state(ideas_found=state["ideas_found"] + 1)
            else:
                log("  --- Консенсус не достигнут. Идея отклонена экспертами.")
        else:
            log("  SCOUT и SWING: Идей не найдено.")
            update_state(scout_status="⚪️ Идея не найдена", swing_status="⚪️ Идея не найдена")
            
        # Формируем и отправляем краткое резюме для Telegram-интерфейса
        if summary_callback:
            summary_text = f"🗣 <b>Обсуждение рынка:</b>\n<a href='{m.url}'>{m.title}</a>\n\n"
            if signal:
                summary_text += f"<b>SCOUT</b> 🟢 {signal.summary}\n\n"
            else:
                summary_text += f"<b>SCOUT</b> ⚪️ Идея не найдена.\n\n"
                
            if swing_signal:
                summary_text += f"<b>SWING</b> 🚀 Ждет памп\n\n"
            else:
                summary_text += f"<b>SWING</b> ⚪️ Нет хайпа.\n\n"
            
            if opinion_shadow:
                status = "✅ СОГЛАСЕН" if opinion_shadow.agree else "❌ ПРОТИВ"
                summary_text += f"<b>SHADOW</b> {status} (Увер: {opinion_shadow.confidence})\n<i>{opinion_shadow.opinion}</i>\n\n"
            
            if (signal or swing_signal) and opinion_shadow and opinion_shadow.agree and opinion_shadow.confidence > 0.6:
                summary_text += "✨ <b>ИТОГ: Консенсус достигнут! Идея сохранена.</b>"
            elif (signal or swing_signal):
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