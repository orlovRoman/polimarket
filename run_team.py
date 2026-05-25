import os
import sys
import threading
from datetime import datetime

# Добавляем корень проекта в путь поиска модулей
sys.path.append(os.getcwd())

from config import logger, LOCK_FILE, LOCK_TIMEOUT_SEC, SCAN_LIMIT_DEFAULT
from agents.shared.adapters.polymarket import PolymarketAdapter
from agents.shared.python.db import (
    save_market, init_db, get_last_analyzed_price,
    mark_market_analyzed, cleanup_stale_signals, save_price_point,
    get_price_history, get_markets_on_cooldown
)
from agents.polymarket_mispricing_agent.src.agent import ScoutAgent
from agents.polymarket_swing_agent.src.agent import SwingAgent
from agents.polymarket_insider_agent.src.agent import ShadowAgent
from agents.orchestrator.src.agent import NexusAgent
from agents.shared.python.market_selector import MarketSelector
from core.workflow import run_screening, run_agent_evaluation, process_consensus

# Глобальная блокировка
_scan_lock = threading.Lock()

def acquire_process_lock() -> bool:
    import time
    os.makedirs("vault", exist_ok=True)
    
    def _write_lock():
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, 'w') as f:
            f.write(f"{time.time()},{os.getpid()}")
            
    try:
        _write_lock()
        return True
    except FileExistsError:
        pass
        
    try:
        with open(LOCK_FILE, "r") as f:
            data = f.read().strip().split(",")
            if len(data) == 2:
                lock_time = float(data[0])
                lock_pid = int(data[1])
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
                        logger.info(f"[Lock] Обнаружен устаревший замок от мертвого процесса {lock_pid}. Сбрасываем.")
                    else:
                        logger.info(f"[Lock] Сканирование заблокировано процессом PID {lock_pid} (активен {elapsed:.0f} сек).")
                        return False
                else:
                    logger.info(f"[Lock] Обнаружен зависший замок (прошло {elapsed:.0f} сек). Сбрасываем.")
    except Exception as e:
        logger.error(f"[Lock] Ошибка чтения замка: {e}. Сбрасываем.")
        
    try:
        os.remove(LOCK_FILE)
        _write_lock()
        return True
    except Exception as e:
        logger.error(f"[Lock] Не удалось создать файл блокировки: {e}")
        return False

def release_process_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
            logger.info("[Lock] Межпроцессный замок успешно снят.")
    except Exception as e:
        logger.error(f"[Lock] Ошибка удаления файла блокировки: {e}")

def _send_correlation_alerts(summary_callback):
    from agents.shared.python.db import get_new_correlations, mark_correlations_notified
    correlations = get_new_correlations()
    for c in correlations:
        text = f"🔗 <b>НАЙДЕНА КОРРЕЛЯЦИЯ:</b>\n<i>{c['reasoning']}</i>\n\nРынки:\n- <a href='{c['market1_url']}'>{c['market1_title']}</a>\n- <a href='{c['market2_url']}'>{c['market2_title']}</a>"
        summary_callback(text)
        mark_correlations_notified(c['id'])

def run_team_discussion(log_callback=None, summary_callback=None, category=None, market_id=None, state_callback=None):
    if not _scan_lock.acquire(blocking=False):
        logger.warning("Сканирование уже выполняется (другой поток). Пропускаем.")
        return 0
        
    if not acquire_process_lock():
        _scan_lock.release()
        return 0
    
    try:
        return _run_team_discussion_inner(log_callback, summary_callback, category, market_id, state_callback)
    finally:
        release_process_lock()
        _scan_lock.release()

def _run_team_discussion_inner(log_callback=None, summary_callback=None, category=None, market_id=None, state_callback=None):
    # Фоновая отправка в Telegram
    def send_telegram_alert(text: str):
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
        try:
            import requests
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=5)
        except Exception as e:
            logger.error(f"Ошибка отправки в Telegram: {e}")

    if summary_callback is None:
        summary_callback = send_telegram_alert

    def log(msg):
        logger.info(msg)
        if log_callback:
            try: log_callback(msg)
            except Exception: pass

    init_db()
    cleanup_stale_signals()

    from config import GOOGLE_API_KEY as key
    if not key:
        log("ОШИБКА: GOOGLE_API_KEY не установлен.")
        return 0

    adapter = PolymarketAdapter()
    from agents.shared.python.db import get_memory
    scan_limit = get_memory("scan_limit")
    if scan_limit is None: scan_limit = SCAN_LIMIT_DEFAULT

    state = {
        "category": category or "Авто-микс",
        "stage": "Инициализация",
        "total_markets": 0,
        "current_market_index": 0,
        "current_market_title": "Инициализация...",
        "current_market_url": "",
        "scout_status": "⏳ Ожидает",
        "swing_status": "⏳ Ожидает",
        "shadow_status": "⏳ Ожидает",
        "ideas_found": 0
    }
    
    def update_state(**kwargs):
        state.update(kwargs)
        if state_callback:
            try: state_callback(state)
            except Exception: pass

    update_state()
    log("Инициализация агентов (Gemini)...")
    scout = ScoutAgent(api_key=key)
    swing = SwingAgent(api_key=key)
    shadow = ShadowAgent(api_key=key)
    nexus = NexusAgent(api_key=key)

    # 1. Скрининг
    screened_market_ids = run_screening(adapter, nexus, category, market_id, summary_callback)

    # 2. Отбор
    cat_msg = f" в категории '{category}'" if category else " (авто-микс)"
    if market_id: cat_msg = f" (точечный анализ {market_id})"
    log(f"\n--- 1. Поиск новых рынков{cat_msg} ---")
    update_state(stage="Отбор рынков")
    
    markets = []
    if market_id:
        try:
            m = adapter.get_market(market_id)
            if m: markets.append(m)
        except Exception as e:
            log(f"  Ошибка загрузки рынка {market_id}: {e}")
    elif screened_market_ids and not category:
        raw_markets = []
        for mid in screened_market_ids[:scan_limit * 2]:
            try:
                m = adapter.get_market(mid)
                if m: raw_markets.append(m)
            except Exception: continue
        selector = MarketSelector(adapter)
        markets = selector._filter(raw_markets)[:scan_limit]
    else:
        selector = MarketSelector(adapter)
        markets = selector.select(total_limit=scan_limit, category=category)
        if not category:
            log(f"  Категория ротации: {selector.get_auto_category()}")
            
    log(f"  Отобрано рынков: {len(markets)}")
    for m in markets: save_market(m)

    # 3. Обсуждение
    log(f"\n--- 2. Обсуждение идей (SCOUT + SWING + SHADOW) ---")
    update_state(total_markets=len(markets), stage="Обсуждение (SCOUT + SWING + SHADOW)")
    
    for i, m in enumerate(markets, 1):
        try:
            update_state(
                current_market_index=i, current_market_title=m.title, current_market_url=m.url,
                scout_status="⏳ Ожидает", swing_status="⏳ Ожидает", shadow_status="⏳ Ожидает"
            )
            
            last_price = get_last_analyzed_price(m.id)
            if last_price is not None and not market_id:
                if abs(last_price - m.price) >= 0.03: log(f"\n[РЫНОК]: {m.title} (Цена: {last_price} -> {m.price})")
                else: log(f"\n[РЫНОК]: {m.title} (Кулдаун истек)")
            else:
                log(f"\n[РЫНОК]: {m.title} (Новый/Точечный)")
                
            save_price_point(m.id, m.price)
            
            # Параллельный парсинг и оценка
            signal, swing_signal = run_agent_evaluation(m, scout, swing, update_state)
            
            opinion_shadow = None
            if signal or swing_signal:
                active_signal = swing_signal if swing_signal else signal
                if signal:
                    log(f"  SCOUT: Edge: {signal.edge:.2f}")
                    update_state(scout_status=f"🟢 Edge ({signal.edge:.2f})")
                else: update_state(scout_status="⚪️ Нет фундамента")
                    
                if swing_signal:
                    log(f"  SWING: Хайп найден!")
                    update_state(swing_status=f"🚀 Ждет памп")
                else: update_state(swing_status="⚪️ Нет хайпа")
                    
                update_state(shadow_status="🔄 Проверяет ордербук...")
                
                orderbook = None
                if m.tokens:
                    try:
                        orderbook = adapter.get_orderbook(m.tokens[0])
                    except Exception: pass
                price_hist = get_price_history(m.id, hours=24)
                
                log("  SHADOW проверяет...")
                from agents.shared.python.db import add_discussion_message
                opinion_shadow = shadow.analyze_idea(m, active_signal.details, orderbook=orderbook, price_history=price_hist)
                status_sh = "✅ Согласен" if (opinion_shadow and opinion_shadow.agree) else "❌ Против"
                update_state(shadow_status=f"{status_sh} (Увер: {opinion_shadow.confidence if opinion_shadow else 0})")
                
                if opinion_shadow:
                    add_discussion_message(m.id, opinion_shadow.agent_name, opinion_shadow.opinion, opinion_shadow.confidence, opinion_shadow.agree)
            
            process_consensus(m, signal, swing_signal, opinion_shadow, state, update_state, summary_callback)
            mark_market_analyzed(m.id, m.price)
            
        except Exception as e:
            log(f"[ОШИБКА] Рынок {m.title}: {e}. Пропускаем.")
            
    update_state(stage="Завершено")
    log("\n✅ ПРОЦЕСС ЗАВЕРШЕН")
    return len(markets)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", type=str, help="Принудительно сканировать категорию")
    parser.add_argument("--market_id", type=str, help="Принудительно сканировать один рынок")
    args = parser.parse_args()
    
    run_team_discussion(category=args.category, market_id=args.market_id)