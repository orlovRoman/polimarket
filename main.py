import os
import sys
import time
import socket
import signal
import asyncio
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

# Добавляем корень проекта в путь поиска модулей для корректного импорта внутренних пакетов
sys.path.append(os.getcwd())

from core.engine import CoreEngine, NoMarketsFoundError
from telegram.bot import dp, bot, init_nexus_agent, get_nexus_agent, AUTHORIZED_CHAT_ID
from agents.shared.python.db import save_memory, cleanup_expired_memory, cleanup_chat_history, cleanup_old_price_history, cleanup_old_episodes
import uvicorn
from core.api import app as fastapi_app
from config import logger, TELEGRAM_NOTIFICATIONS_ENABLED  # Единый логгер и флаг уведомлений из config.py

engine = CoreEngine()

async def scheduled_job():
    """Периодическая задача: запуск анализа рынков движком."""
    from agents.shared.python.db import get_memory
    if not get_memory("strategy_scout_enabled", True):
        return
    logger.info(">>> Запуск планового сканирования рынков...")
    try:
        # Используем единственный экземпляр NexusAgent (Option A+)
        nexus = get_nexus_agent()
        cleanup_res = nexus.cleanup_expired_signals()
        logger.info(f"Очистка: {cleanup_res}")

        from telegram.bot import _scan_lock
        if _scan_lock.locked():
            logger.info("Плановое сканирование пропущено: telegram-инициированный скан активен")
            return
            
        async with _scan_lock:
            processed_count = await asyncio.to_thread(engine.run_team_discussion)
        
        if processed_count and processed_count > 0:
            from datetime import timezone
            save_memory("last_scan_time", datetime.now(timezone.utc).isoformat())
        
        expired = cleanup_expired_memory()
        if expired > 0:
            logger.info(f"Очищено истёкших записей памяти: {expired}")
        save_memory("last_cleanup_expired", str(expired), category="operational")
        
        old_prices = cleanup_old_price_history(days=7)
        if old_prices > 0:
            logger.info(f"Очищено старых записей истории цен: {old_prices}")
        save_memory("last_cleanup_prices", str(old_prices), category="operational")

        old_episodes = cleanup_old_episodes(days=90)
        if old_episodes > 0:
            logger.info(f"Очищено старых эпизодов агентов: {old_episodes}")
        save_memory("last_cleanup_episodes", str(old_episodes), category="operational")

        logger.info("<<< Сканирование завершено успешно.")
    except asyncio.CancelledError:
        logger.info("<<< Сканирование прервано: получен сигнал завершения (CancelledError).")
        raise
    except NoMarketsFoundError as e:
        logger.info(f"<<< Сканирование завершено: {e}")
    except RuntimeError as e:
        if "Сканирование уже выполняется" in str(e):
            logger.info(f"<<< Сканирование пропущено: {e}")
        else:
            logger.error(f"Ошибка при выполнении сканирования: {e}", exc_info=True)
    except Exception as e:
        if e.__class__.__name__ == "LLMUnavailableError":
            logger.error(f"<<< Сканирование пропущено (LLM недоступен): {e}")
        else:
            logger.error(f"Ошибка при выполнении сканирования: {e}", exc_info=True)

async def scheduled_memory_archive():
    from agents.shared.python.db import get_memory
    if not get_memory("process_memory_archive_enabled", True):
        return
    logger.info(">>> Запуск процесса автоархивации памяти и GC (Memory GC)...")
    try:
        from agents.orchestrator.scripts.memory_archiver import main as run_archiver
        await asyncio.to_thread(run_archiver)
        logger.info("<<< Автоархивация памяти и GC завершены.")
    except asyncio.CancelledError:
        logger.info("<<< Процесс автоархивации памяти отменен.")
        raise
    except Exception as e:
        logger.error(f"Ошибка при архивации памяти: {e}", exc_info=True)

async def scheduled_trend_hunting():
    from agents.shared.python.db import get_memory
    enabled = get_memory("trend_hunter_enabled", True)
    if not enabled:
        logger.info("Проактивный Trend Hunter отключен пользователем в настройках.")
        return

    logger.info(">>> Запуск проактивного Trend Hunter...")
    try:
        from services.trend_hunter import run_trend_hunter
        await asyncio.to_thread(run_trend_hunter)
        logger.info("<<< Работа Trend Hunter завершена успешно.")
    except asyncio.CancelledError:
        logger.info("<<< Работа Trend Hunter отменена.")
        raise
    except Exception as e:
        logger.error(f"Ошибка при работе Trend Hunter: {e}", exc_info=True)





async def scheduled_wallet_recalculation():
    """Периодический пересчет win_rate кошельков."""
    from agents.shared.python.db import get_memory
    if not get_memory("process_wallet_recalculation_enabled", True):
        return
    logger.info(">>> Запуск периодического пересчета win_rate крупных кошельков...")
    try:
        from services.wallet_tracker import recalculate_win_rates
        await asyncio.to_thread(recalculate_win_rates)
        logger.info("<<< Пересчет win_rate кошельков завершен успешно.")
    except asyncio.CancelledError:
        logger.info("<<< Пересчет win_rate кошельков отменен.")
        raise
    except Exception as e:
        logger.error(f"Ошибка при пересчете win_rate кошельков: {e}", exc_info=True)


async def scheduled_insiders_recalculation():
    """Периодический пересчет статуса инсайдеров."""
    from agents.shared.python.db import get_memory
    if not get_memory("process_clusters_insiders_enabled", True):
        return
    logger.info(">>> Запуск периодического пересчета статуса инсайдеров...")
    try:
        from core.insider_filter import recalculate_all_insiders
        await asyncio.to_thread(recalculate_all_insiders)
        logger.info("<<< Пересчет статуса инсайдеров завершен успешно.")
    except asyncio.CancelledError:
        logger.info("<<< Пересчет статуса инсайдеров отменен.")
        raise
    except Exception as e:
        logger.error(f"Ошибка при пересчете статуса инсайдеров: {e}", exc_info=True)

async def scheduled_cluster_update():
    """Периодическое обновление кластеров кошельков (раз в час)."""
    from agents.shared.python.db import get_memory
    if not get_memory("process_clusters_insiders_enabled", True):
        return
    logger.info(">>> Запуск периодического обновления кластеров кошельков (Strategy 01)...")
    try:
        from core.strategy01_worker import update_wallet_clusters
        await update_wallet_clusters()
        logger.info("<<< Обновление кластеров кошельков завершено.")
    except asyncio.CancelledError:
        logger.info("<<< Обновление кластеров кошельков отменено.")
        raise
    except Exception as e:
        logger.error(f"Ошибка при обновлении кластеров кошельков: {e}", exc_info=True)

async def scheduled_audit_resolutions():
    """Периодический аудит исходов рынков."""
    from agents.shared.python.db import get_memory
    if not get_memory("process_audit_resolutions_enabled", True):
        return
    logger.info(">>> Запуск аудита резолюций...")
    try:
        from core.eval.outcome_tracker import OutcomeTracker
        tracker = OutcomeTracker()
        res = await tracker.audit_existing_resolutions(100)
        logger.info(f"<<< Аудит завершен: {res}")
    except asyncio.CancelledError:
        logger.info("<<< Аудит резолюций отменен.")
        raise
    except Exception as e:
        logger.error(f"Ошибка при аудите резолюций: {e}", exc_info=True)


async def job_onchain_alerts():
    """Фоновый скан и отправка ончейн-всплесков объёма + тихий сбор Whale сигналов."""
    logger.info(">>> Запуск сканирования ончейн-всплесков объёма...")
    try:
        from services.onchain_trend_alert import (
            scan_volume_spikes, build_spike_message, 
            scan_large_single_bets, scan_wallet_series
        )
        from agents.shared.python.db import mark_alert_sent
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        
        # 1. Сканируем всплески объема (с алертом в TG)
        spikes = await asyncio.to_thread(scan_volume_spikes)
        for spike in spikes:
            msg = build_spike_message(spike)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Проанализировать рынок", callback_data=f"analyze_mkt_{spike['market_id']}")]
            ])
            # Отправляем сообщение в авторизованный чат
            await bot.send_message(
                AUTHORIZED_CHAT_ID, 
                msg, 
                parse_mode="HTML", 
                disable_web_page_preview=True,
                reply_markup=keyboard
            )
            logger.info(f"Отправлен ончейн-алерт по рынку: {spike['title']}")
            
        # 2. Сканируем крупные одиночные ставки (тихая запись в БД)
        await asyncio.to_thread(scan_large_single_bets)
        
        # 3. Сканируем серии сделок одного кошелька (тихая запись в БД)
        await asyncio.to_thread(scan_wallet_series)
        
        logger.info("<<< Сканирование ончейн-всплесков и Whale-сигналов завершено.")
    except asyncio.CancelledError:
        logger.info("<<< Сканирование ончейн-всплесков отменено.")
        raise
    except Exception as e:
        logger.error(f"Ошибка при сканировании ончейн-всплесков: {e}", exc_info=True)

async def start_fastapi():
    """Запуск FastAPI сервера в фоне (через asyncio)"""
    config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=8000, log_level="info", handle_signals=False)
    server = uvicorn.Server(config)
    await server.serve()

async def start_dashboard():
    """Запуск aiohttp сервера дашборда в фоне"""
    from web.dashboard import create_dashboard_app
    from aiohttp import web
    import os
    app = create_dashboard_app()
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("DASHBOARD_PORT", "8050"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info(f"📊 Дашборд запущен на http://localhost:{port}")
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        logger.info("Остановка дашборда...")
        await runner.cleanup()

import socket

_lock_socket = None

def ensure_single_instance():
    """Гарантирует, что запущена только одна копия бота через привязку к уникальному порту.
    Это надежнее файловых локов (особенно на сетевых дисках CIFS/SMB)."""
    global _lock_socket
    _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Позволяем быстрому повторному привязыванию порта при перезапуске
    _lock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    for attempt in range(5):
        try:
            # Пытаемся занять фиксированный локальный порт эксклюзивно
            _lock_socket.bind(("127.0.0.1", 61234))
            _lock_socket.listen(0)
            logger.info("🔒 Успешно захвачен порт 61234. Запуск разрешен.")
            return
        except OSError:
            if attempt < 4:
                time.sleep(1)
            else:
                logger.error("[КРИТИЧЕСКАЯ ОШИБКА] Другая копия бота уже работает! Экстренное завершение...")
                sys.exit(1)

async def scheduled_daily_evaluation():
    try:
        from agents.shared.python.db import get_memory
        if not get_memory("process_evaluation_enabled", True):
            return
        logger.info(">>> Запуск ежедневного оценивания стратегий (Evaluation Engine)...")
        from core.eval.evaluation_engine import EvaluationEngine
        engine = EvaluationEngine()
        await engine.run_full_evaluation(period_days=30)
        logger.info("<<< Ежедневное оценивание стратегий завершено успешно.")
    except Exception as e:
        logger.error(f"Ошибка во время ежедневного оценивания: {e}", exc_info=True)

async def scheduled_calibration():
    try:
        logger.info(">>> Запуск автоматической калибровки агентов (NEXUS)...")
        from agents.orchestrator.scripts.calibrate import run_calibration
        report, has_updates = await run_calibration(trigger_type="scheduled")
        if has_updates:
            from telegram.bot import bot
            from config import AUTHORIZED_CHAT_ID
            import asyncio
            msg = f"🔍 <b>Калибровка завершена</b>\n\nNEXUS предложил новые обновления промптов.\nЗайдите в Дашборд -> Calibration (NEXUS), чтобы рассмотреть их."
            await asyncio.to_thread(bot.send_message, chat_id=AUTHORIZED_CHAT_ID, text=msg, parse_mode="HTML")
        logger.info("<<< Автоматическая калибровка агентов завершена.")
    except Exception as e:
        logger.error(f"Ошибка во время автоматической калибровки: {e}", exc_info=True)


async def scheduled_weekly_evaluation():
    try:
        from agents.shared.python.db import get_memory
        if not get_memory("process_evaluation_enabled", True):
            return
        logger.info(">>> Запуск еженедельного глубокого оценивания стратегий (Evaluation Engine)...")
        from core.eval.evaluation_engine import EvaluationEngine
        engine = EvaluationEngine()
        await engine.run_full_evaluation(period_days=90)
        logger.info("<<< Еженедельное глубокое оценивание стратегий завершено успешно.")
    except Exception as e:
        logger.error(f"Ошибка во время еженедельного оценивания: {e}", exc_info=True)

async def scheduled_penny_discovery():
    try:
        from agents.shared.python.db import get_memory
        if not get_memory("strategy_penny_stocks_enabled", True):
            return
        logger.info(">>> Запуск автоматического поиска Penny Stocks...")
        from agents.shared.python.market_selector import MarketSelector
        from core.singleton import get_core_engine
        from agents.shared.python.db import (
            add_penny_stock_to_monitoring,
            get_active_penny_stocks
        )
        from core.workflow import run_agent_evaluation
        from agents.shared.python.penny_settings_db import get_penny_stocks_config
        from agents.shared.python.penny_execution_service import (
            should_skip_penny_scan,
            passes_penny_filters,
            passes_signal_filters,
            execute_penny_trade
        )
        import asyncio
        
        cfg = get_penny_stocks_config()
        if should_skip_penny_scan(cfg):
            logger.info("Сканирование Penny Stocks пропущено (Kill Switch вкл.)")
            return
            
        engine = get_core_engine()
        selector = MarketSelector(engine.adapter)
        markets = selector.select(total_limit=10, category="penny_stocks")
        if not markets:
            logger.info("Penny Stocks не обнаружены.")
            return

        active_ids = {m["market_id"] for m in get_active_penny_stocks()}
        
        preflight_cache = {}
        new_discovered = 0
        
        from agents.shared.python.db import get_notification_settings
        notify_penny = get_notification_settings().get("notify_penny_stocks", True)

        for m in markets:
            if m.id in active_ids:
                continue
            
            if not passes_penny_filters(m, cfg):
                continue
            
            logger.info(f"Обнаружен новый Penny Stock: {m.title} ({m.price})")
            
            pred_out = None
            edge_val = None
            conf_val = None
            
            try:
                price_hist = []
                try:
                    from agents.shared.python.db import get_price_history
                    price_hist = get_price_history(m.id, hours=24)
                except Exception:
                    pass
                
                if hasattr(engine, '_fetch_pre_orderbook'):
                    pre_orderbook = engine._fetch_pre_orderbook(m)
                else:
                    pre_orderbook = None
                
                def dummy_update(**kwargs):
                    """Empty callback for scanning."""
                    pass
                
                signal, swing_signal, _ = await run_agent_evaluation(
                    m, engine.scout, engine.swing, dummy_update,
                    adapter=engine.adapter, trigger_type="scheduled",
                    price_history=price_hist, pre_orderbook=pre_orderbook,
                    scan_category="penny_stocks"
                )
                
                active_sig = signal or swing_signal
                if active_sig:
                    pred_out = active_sig.target_outcome
                    edge_val = active_sig.edge
                    conf_val = active_sig.confidence
                    logger.info(f"Penny Stock {m.title} проанализирован: {pred_out} (edge: {edge_val})")
            except Exception as exc:
                logger.error(f"Ошибка анализа Penny Stock {m.id}: {exc}", exc_info=True)
            
            # Добавляем в мониторинг сразу со всеми полученными прогнозами
            close_time_str = None
            if m.close_time:
                close_time_str = m.close_time.strftime("%Y-%m-%d %H:%M:%S")

            add_penny_stock_to_monitoring(
                market_id=m.id,
                title=m.title,
                url=m.url,
                initial_price=m.price,
                predicted_outcome=pred_out,
                edge=edge_val,
                confidence=conf_val,
                close_time=close_time_str
            )
            
            # Автопокупка при соответствии фильтрам
            if pred_out:
                effective_price = m.price if pred_out == "YES" else (1.0 - m.price)
                sig_dict = {
                    "target_outcome": pred_out,
                    "probability": m.price,
                    "confidence": conf_val,
                    "price": effective_price
                }
                if passes_signal_filters(sig_dict, cfg):
                    if cfg.auto_buy_enabled:
                        execute_penny_trade(m.id, sig_dict, cfg, preflight_cache=preflight_cache)

            
            new_discovered += 1
            
            if notify_penny:
                price_cents = int(round(m.price * 100))
                msg = (
                    f"🪙 <b>Найден новый Penny Stock!</b>\n\n"
                    f"📍 <b>{m.title}</b>\n"
                    f"📈 Текущая цена: <b>{price_cents}¢</b>\n"
                    f"🔗 <a href='{m.url}'>Открыть рынок</a>"
                )
                await bot.send_message(AUTHORIZED_CHAT_ID, msg, parse_mode="HTML", disable_web_page_preview=True)
                await asyncio.sleep(1)
            
        logger.info(f"<<< Поиск Penny Stocks завершен. Добавлено {new_discovered} новых рынков.")
    except Exception as e:
        logger.error(f"Ошибка при автоматическом поиске Penny Stocks: {e}", exc_info=True)

async def scheduled_penny_monitor():
    try:
        from agents.shared.python.db import get_memory
        if not get_memory("strategy_penny_stocks_enabled", True):
            return
        logger.info(">>> Запуск мониторинга Penny Stocks...")
        from agents.shared.python.penny_execution_service import monitor_active_penny_stocks
        from core.singleton import get_core_engine
        engine = get_core_engine()
        await monitor_active_penny_stocks(bot, AUTHORIZED_CHAT_ID, engine)
        logger.info("<<< Мониторинг Penny Stocks завершен.")
    except Exception as e:
        logger.error(f"Ошибка при автоматическом мониторинге Penny Stocks: {e}", exc_info=True)


async def scheduled_data_api_sync():
    try:
        from agents.shared.python.db import get_memory
        if not get_memory("system_active", default=True):
            return  # система на паузе — не синхронизируем
            
        from services.data_api_syncer import sync_trades_from_data_api
        import asyncio
        await asyncio.to_thread(sync_trades_from_data_api, 500)
    except Exception as e:
        logger.error(f"Ошибка при фоновой синхронизации сделок с Data API: {e}", exc_info=True)

async def scheduled_whale_discovery():
    try:
        from agents.shared.python.db import get_memory
        if not get_memory("strategy_whale_enabled", True):
            return
        logger.info(">>> Запуск автоматического поиска китов (Whale Following)...")
        from services.onchain_trend_alert import scan_volume_spikes, scan_large_single_bets, scan_wallet_series
        import asyncio
        from agents.shared.python.db import get_connection
        
        # Проверка живости источника данных (Telegram Listener)
        with get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT count(*) FROM trader_transactions WHERE timestamp >= datetime('now', '-24 hours')")
            recent_tx_count = c.fetchone()[0]
            
        system_started_at = get_memory("system_started_at", None)
        if recent_tx_count == 0 and system_started_at:
            import datetime
            try:
                started = datetime.datetime.fromisoformat(system_started_at)
                uptime_hours = (datetime.datetime.utcnow() - started).total_seconds() / 3600
                if uptime_hours > 24:
                    logger.warning("⚠️ Источник данных о китах (Telegram Listener) не получил ни одной сделки за 24ч аптайма. Поиск китов пропущен.")
                    return
            except Exception:
                pass
            
        await asyncio.to_thread(scan_volume_spikes)
        await asyncio.to_thread(scan_large_single_bets)
        await asyncio.to_thread(scan_wallet_series)
        
        logger.info("<<< Поиск китов (Whale Following) завершен.")
    except Exception as e:
        logger.error(f"Ошибка при автоматическом поиске китов: {e}", exc_info=True)

async def scheduled_agent_accuracy_recalc():
    try:
        logger.info(">>> Запуск периодического пересчета точности агентов...")
        from agents.shared.python.db import get_agent_accuracy, save_memory
        for agent_name in ["SCOUT", "SWING", "SHADOW"]:
            stats = get_agent_accuracy(agent_name)
            if stats['total'] > 0:
                save_memory(
                    f"{agent_name.lower()}_accuracy_pct",
                    round(stats['accuracy'] * 100, 1),
                    category='fact',
                    priority=8
                )
                save_memory(
                    f"{agent_name.lower()}_evaluated_total",
                    stats['total'],
                    category='fact',
                    priority=8
                )
        logger.info("<<< Пересчет точности завершен.")
    except Exception as e:
        logger.error(f"Ошибка в scheduled_agent_accuracy_recalc: {e}", exc_info=True)

async def scheduled_whale_monitor():
    try:
        from agents.shared.python.db import get_memory
        if not get_memory("strategy_whale_enabled", True):
            return
        logger.info(">>> Запуск мониторинга Whale Following...")
        from agents.shared.python.db import (
            get_active_whale_stocks,
            update_whale_stock_price,
            mark_whale_spike_sent,
            resolve_whale_stock
        )
        from services.outcome_tracker import _fetch_resolution
        from core.singleton import get_core_engine
        import asyncio
        
        active_stocks = get_active_whale_stocks()
        if not active_stocks:
            logger.info("Нет активных Whale Stocks для мониторинга.")
            return
            
        engine = get_core_engine()
        
        for stock in active_stocks:
            m_id = stock["market_id"]
            
            from datetime import datetime, timezone
            try:
                market_obj = engine.adapter.get_market(m_id)
            except Exception:
                market_obj = None
                
            if market_obj:
                current_price = market_obj.price
                volume_2h = getattr(market_obj, 'volume', 0.0)
                update_whale_stock_price(m_id, current_price, volume_2h)
                
                init_price = stock["initial_price"]
                price_growth = 0.0
                if init_price > 0:
                    price_growth = (current_price - init_price) / init_price
                    
                if not stock["spike_alert_sent"] and price_growth >= 1.0:
                    mark_whale_spike_sent(m_id)
                    msg = (
                        f"⚡️ <b>РЕЗКИЙ ВСПЛЕСК на Whale Following!</b>\n\n"
                        f"📍 <b>{stock['title']}</b>\n"
                        f"📈 Цена: {int(round(init_price*100))}¢ -> <b>{int(round(current_price*100))}¢</b> (рост на {price_growth*100:.0f}%!)\n"
                        f"🔗 <a href='{stock['url']}'>Открыть рынок</a>"
                    )
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔍 Проанализировать рынок", callback_data=f"analyze_mkt_{m_id}")]
                    ])
                    await bot.send_message(
                        AUTHORIZED_CHAT_ID, 
                        msg, 
                        parse_mode="HTML", 
                        disable_web_page_preview=True,
                        reply_markup=keyboard
                    )
                    await asyncio.sleep(1)
            
            close_time_passed = False
            resolution_result = None
            if market_obj:
                if market_obj.close_time:
                    close_time_passed = market_obj.close_time < datetime.now(timezone.utc)
            else:
                try:
                    res = await asyncio.to_thread(_fetch_resolution, m_id)
                    if res in ("YES", "NO"):
                        resolution_result = res
                        close_time_passed = True
                except Exception:
                    pass

            if close_time_passed:
                if not resolution_result:
                    resolution_result = await asyncio.to_thread(_fetch_resolution, m_id)
                if resolution_result in ("YES", "NO"):
                    resolve_whale_stock(m_id, resolution_result)
                    pred = stock["predicted_outcome"]
                    result_str = "УСПЕШНО 🎉" if pred and pred.upper() == resolution_result else "НЕ СОВПАЛО ❌"
                    msg = (
                        f"🔔 <b>Закрытие рынка Whale Following!</b>\n\n"
                        f"📍 <b>{stock['title']}</b>\n"
                        f"🎯 Прогноз бота: <b>{pred}</b>\n"
                        f"✅ Исход Polymarket: <b>{resolution_result}</b>\n"
                        f"🏆 Результат: <b>{result_str}</b>\n"
                        f"🔗 <a href='{stock['url']}'>Открыть рынок</a>"
                    )
                    await bot.send_message(AUTHORIZED_CHAT_ID, msg, parse_mode="HTML", disable_web_page_preview=True)
                    await asyncio.sleep(1)
                    
        logger.info("<<< Мониторинг Whale Following завершен.")
    except Exception as e:
        logger.error(f"Ошибка в мониторинге Whale Following: {e}", exc_info=True)

# Атом 3: Джоба синхронизации портфелей китов
async def scheduled_whale_portfolios_sync():
    """
    Фоновая джоба: раз в 2 часа качает открытые позиции всех known_whales.
    """
    try:
        from agents.shared.python.db import get_memory
        if not get_memory("strategy_whale_enabled", True):
            return
        from agents.shared.python.whale_portfolio_service import run_portfolio_sync_job
        await run_portfolio_sync_job()
    except asyncio.CancelledError:
        logger.info("<<< Синхронизация портфелей китов отменена.")
        raise
    except Exception as e:
        logger.error(f"Ошибка синхронизации портфелей китов: {e}", exc_info=True)

async def scheduled_favourite_compounding():

    """Сканирует рынки на Favourite Compounding и проверяет Exit-сигналы каждые 15 минут."""
    from agents.shared.python.db import get_memory
    if not get_memory("strategy_favourite_compounding_enabled", True):
        return

    from telegram.bot import _favourite_compound_lock, _scan_lock, _penny_scan_lock
    if _favourite_compound_lock.locked() or _scan_lock.locked() or _penny_scan_lock.locked():
        logger.info("Favourite Compounding: плановое сканирование пропущено, так как активен другой скан.")
        return

    async with _favourite_compound_lock:
        logger.info(">>> Favourite Compounding: запуск скана...")
        try:
            from core.singleton import get_core_engine
            from services.favourite_compounder import run_favourite_scan
            from services.notifications import send_compound_alert, send_compound_exit_alert
            from agents.shared.python.db import (
                get_compound_settings, upsert_compound_opportunity, mark_compound_alerted,
                get_active_compound_opportunities, get_connection
            )
            import asyncio

            cfg = get_compound_settings()
            if not cfg.get("enabled", 1):
                logger.info("Favourite Compounding отключён в настройках.")
                return

            engine = get_core_engine()

            # 1. Мониторинг BOUGHT-позиций для профи-продажи (Exit-сигнал, расширение C)
            bought_opps = await asyncio.to_thread(get_active_compound_opportunities)
            bought_opps = [o for o in bought_opps if o["status"] == "BOUGHT"]
            
            for opp in bought_opps:
                try:
                    market_obj = await asyncio.to_thread(engine.adapter.get_market, opp["market_id"])
                    if market_obj:
                        opp_outcome = opp.get("outcome", "YES")
                        price_yes = float(market_obj.price)
                        current_fav_price = price_yes if opp_outcome == "YES" else (1.0 - price_yes)
                        if current_fav_price >= 0.995:
                            await send_compound_exit_alert(bot, AUTHORIZED_CHAT_ID, opp, current_fav_price)
                            # Меняем статус на ALERTED_EXIT, чтобы не дублировать алерты
                            def mark_exit_alerted():
                                with get_connection() as conn:
                                    conn.execute("UPDATE compound_opportunities SET status='ALERTED_EXIT' WHERE id=?", (opp["id"],))
                            await asyncio.to_thread(mark_exit_alerted)
                except Exception as exc:
                    logger.error(f"Ошибка при мониторинге BOUGHT-позиции {opp['id']}: {exc}")

            # 2. Сканирование новых возможностей
            from datetime import timezone
            compact_markets = await asyncio.to_thread(engine.adapter.list_all_markets_compact)
            
            candidates_ids = []
            now = datetime.now(timezone.utc)
            for cm in compact_markets:
                try:
                    price_yes = float(cm["p"])
                    fav_price = price_yes if price_yes >= 0.5 else (1.0 - price_yes)
                    volume = float(cm["vol"])
                    end_raw = cm["end"]
                    if not end_raw:
                        continue
                    close_time = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
                    hours_left = (close_time - now).total_seconds() / 3600
                    
                    if (
                        fav_price >= cfg["min_price"]
                        and volume >= cfg["min_volume"]
                        and 0 < hours_left <= cfg["max_hours"]
                    ):
                        candidates_ids.append(cm["id"])
                except Exception:
                    continue
            
            markets = []
            for m_id in candidates_ids:
                try:
                    m_obj = await asyncio.to_thread(engine.adapter.get_market, m_id)
                    if m_obj:
                        markets.append(m_obj)
                except Exception as exc:
                    logger.warning(f"Ошибка загрузки рынка {m_id} для Favourite Compounding: {exc}")

            opps = await asyncio.to_thread(run_favourite_scan, markets)
            sent = 0
            for opp in opps:
                is_new = await asyncio.to_thread(
                    upsert_compound_opportunity, {
                        "id": opp.opp_id,
                        "market_id": opp.market_id,
                        "title": opp.title,
                        "url": opp.url,
                        "price": opp.price,
                        "volume_usd": opp.volume_usd,
                        "close_time": opp.close_time.isoformat(),
                        "hours_left": opp.hours_left,
                        "spread_pct": opp.spread_pct,
                        "roi_net_pct": opp.roi_net_pct,
                        "confidence": opp.confidence,
                        "obviousness_reason": opp.obviousness_reason,
                        "outcome": opp.outcome,
                    }
                )
                if is_new:
                    await send_compound_alert(bot, AUTHORIZED_CHAT_ID, opp)
                    await asyncio.to_thread(mark_compound_alerted, opp.opp_id)
                    sent += 1
                    await asyncio.sleep(1)  # rate limit

            logger.info(f"<<< Favourite Compounding: отправлено {sent} алертов из {len(opps)} найденных.")
        except asyncio.CancelledError:
            logger.info("<<< Favourite Compounding: отменён.")
            raise
        except Exception as e:
            logger.error(f"Ошибка Favourite Compounding: {e}", exc_info=True)

_resolution_running = False

async def scheduled_outcome_tracker():
    global _resolution_running
    if _resolution_running:
        logger.warning("Outcome tracker cycle is already running, skipping this interval")
        return
    _resolution_running = True
    try:
        # ПРИМЕЧАНИЕ о двойном outcome tracker:
        # 1. run_resolution_cycle (services/outcome_tracker.py) - legacy-трекер, выполняющий
        #    также Favourite Compounding резолюцию, очистку старых эпизодов и отправку сводок в Telegram.
        # 2. tracker.run_cycle (core/eval/outcome_tracker.py) - новый трекер для калибровки и расчета ECE.
        # Они работают на одной таблице 'signals'. Первый разрешает PENDING сигналы, переводя их в RESOLVED.
        # Второй запускается сразу же, видит 0 PENDING сигналов (поскольку они уже разрешены) и
        # завершает работу без лишней нагрузки и без конфликта данных.
        # Благодаря фиксу Бага 9, оба трекера теперь рассчитывают и пишут calibration_error (ECE)
        # абсолютно консистентно, предотвращая затирание данных NULL-значениями.
        from services.outcome_tracker import run_resolution_cycle
        await asyncio.to_thread(run_resolution_cycle)
        
        from core.eval.outcome_tracker import OutcomeTracker
        tracker = OutcomeTracker()
        await asyncio.to_thread(tracker.run_cycle)
    except Exception as e:
        logger.exception(f"Error in scheduled outcome tracker: {e}")
    finally:
        _resolution_running = False

async def scheduled_signal_resolution():
    logger.info(">>> Авторезолюция PENDING-сигналов по расписанию...")
    try:
        from services.signal_resolver import resolve_pending_signals
        n_resolved = await asyncio.to_thread(resolve_pending_signals)
        logger.info(f"<<< Авторезолюция: закрыто {n_resolved} сигналов.")
    except Exception as e:
        logger.error(f"Ошибка в авторезолюции по расписанию: {e}", exc_info=True)

async def scheduled_resolution_fetcher():
    try:
        from services.resolution_fetcher import ResolutionFetcher
        fetcher = ResolutionFetcher()
        n_resolved = await fetcher.fetch_pending_resolutions()
        if n_resolved > 0:
            logger.info(f"[ResolutionFetcher] Обновлено {n_resolved} сигналов.")
    except Exception as e:
        logger.error(f"Ошибка в scheduled_resolution_fetcher: {e}", exc_info=True)

async def start_system():
    load_dotenv()
    from config import startup_check
    startup_check()
    os.makedirs("logs", exist_ok=True)
    
    # Жесткая блокировка повторных запусков
    ensure_single_instance()

    # Запуск фоновой индексации Obsidian Vault для RAG
    try:
        from agents.shared.utils.obsidian_adapter import ObsidianAdapter
        adapter = ObsidianAdapter()
        logger.info("🤖 Запуск фоновой индексации Obsidian Vault...")
        loop = asyncio.get_running_loop()
        loop.create_task(asyncio.to_thread(adapter.reindex_all_files))
    except Exception as e:
        logger.error(f"Ошибка при запуске фоновой индексации: {e}")
    
    # Объявляем переменные задач для graceful shutdown
    polling_task = None
    api_task = None
    watchlist_task = None
    dashboard_task = None
    
    # Обработчики завершения процесса (асинхронный graceful shutdown)
    loop = asyncio.get_running_loop()

    scheduler = AsyncIOScheduler()

    _shutdown_done = False

    def _request_shutdown(*args):
        """Синхронный обработчик сигнала завершения. Отменяет фоновые задачи."""
        nonlocal _shutdown_done
        if _shutdown_done:
            return
        _shutdown_done = True
        logger.info("🚨 Получен сигнал завершения, запускаем отмену фоновых задач...")
        import config
        config.shutdown_requested = True
        for task in [polling_task, api_task, watchlist_task, dashboard_task]:
            if task and not task.done():
                task.cancel()
    
    # Авто-запуск сканирования ОТКЛЮЧЁН — управление через /monitor в Telegram
    # scheduler.add_job(scheduled_job, 'interval', minutes=5)
    
    # Автоматическая резолюция сигналов и рынков теперь централизована в Outcome Tracker (run_resolution_cycle)
    # Старые джобы scheduled_resolution и scheduled_eval_resolutions отключены во избежание дублирования и гонок.


    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(
        scheduled_daily_evaluation,
        trigger=CronTrigger(hour=3, minute=0),
        id="daily_evaluation",
        replace_existing=True,
        misfire_grace_time=3600
    )
    
    scheduler.add_job(
        scheduled_whale_portfolios_sync,
        trigger="interval",
        hours=2,
        id="whale_portfolios_sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        scheduled_weekly_evaluation,
        trigger=CronTrigger(day_of_week="mon", hour=4, minute=0),
        id="weekly_deep_evaluation",
        replace_existing=True,
        misfire_grace_time=3600
    )
    
    scheduler.add_job(scheduled_memory_archive, 'interval', hours=24)
    scheduler.add_job(scheduled_trend_hunting, 'interval', hours=2)
    # scheduler.add_job(scheduled_cross_arbitrage_scan, 'interval', hours=4)  # кросс-арбитраж отключен/перенесен в конец
    # scheduler.add_job(scheduled_synthetic_corridors, 'interval', minutes=15) # синтетические коридоры каждые 15 м
    # scheduler.add_job(scheduled_temporal_corridors, 'interval', minutes=30) # временные коридоры каждые 30 м
    
    # Регулярно стягиваем свежие сделки с Data API для Whale Discovery (каждые 2 минуты)
    scheduler.add_job(scheduled_data_api_sync, 'interval', minutes=2)
    
    scheduler.add_job(scheduled_wallet_recalculation, 'interval', hours=4) # пересчет win_rate кошельков каждые 4 часа
    scheduler.add_job(scheduled_insiders_recalculation, 'interval', hours=1) # пересчет инсайдеров каждый час
    scheduler.add_job(scheduled_cluster_update, 'interval', hours=1) # пересчет кластеров каждый час
    scheduler.add_job(scheduled_audit_resolutions, 'interval', hours=24) # кросс-чек исходов раз в сутки
    scheduler.add_job(job_onchain_alerts, 'interval', minutes=30) # ончейн-алерты всплесков объема каждые 30 минут



    scheduler.add_job(
        scheduled_penny_discovery,
        trigger="interval",
        hours=1,
        id="penny_discovery_job",
        replace_existing=True,
        misfire_grace_time=3600
    )

    scheduler.add_job(
        scheduled_penny_monitor,
        trigger="interval",
        minutes=15,
        id="penny_monitor_job",
        replace_existing=True,
        misfire_grace_time=600
    )

    scheduler.add_job(
        scheduled_whale_discovery,
        trigger="interval",
        minutes=30,
        id="whale_discovery_job",
        replace_existing=True,
        misfire_grace_time=1800
    )

    scheduler.add_job(
        scheduled_whale_monitor,
        trigger="interval",
        minutes=10,
        id="whale_monitor_job",
        replace_existing=True,
        misfire_grace_time=600
    )


    scheduler.add_job(
        scheduled_agent_accuracy_recalc,
        trigger="interval",
        hours=1,
        id="agent_accuracy_recalc_job",
        replace_existing=True,
        misfire_grace_time=600
    )

    # Outcome Tracker — авторезолюция сигналов и автокалибровка параметров
    from core.config_provider import ConfigProvider
    outcome_tracker_interval = int(ConfigProvider.get_sync("eval.outcome_tracker_interval_hours", default=6))
    scheduler.add_job(
        scheduled_outcome_tracker,
        trigger="interval",
        hours=outcome_tracker_interval,
        id="outcome_tracker",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # ВНИМАНИЕ: Джобы resolution_fetcher и signal_resolution_job удалены из планировщика.
    # Вся авторезолюция сигналов и рынков теперь централизована в scheduled_outcome_tracker
    # во избежание Race Condition и затирания реального PnL прокси-значениями.

    scheduler.add_job(
        scheduled_favourite_compounding,
        trigger="interval",
        minutes=15,
        id="favourite_compounding",
        replace_existing=True,
        misfire_grace_time=600,
    )

    scheduler.add_job(
        scheduled_calibration,
        trigger="interval",
        hours=24,
        id="nexus_calibration_job",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    if TELEGRAM_NOTIFICATIONS_ENABLED:
        logger.info("🤖 Бот NEXUS запускается...")
        try:
            # Option A+: явная асинхронная инициализация NexusAgent ДО начала polling и планировщика
            await init_nexus_agent()
            from telegram.bot import set_commands
            await set_commands(bot)
            
            # Запускаем фоновый мониторинг watchlist-рынков
            from services.watchlist_monitor import run_watchlist_monitor
            watchlist_task = asyncio.create_task(
                run_watchlist_monitor(bot, AUTHORIZED_CHAT_ID)
            )
            logger.info("✅ Watchlist-монитор запущен.")
        except Exception as e:
            logger.error(f"Критическая ошибка инициализации бота/агента: {e}")
            sys.exit(1)
    else:
        logger.info("ℹ️ Телеграм-интеграция отключена. Запуск в автономном режиме без бота и уведомлений.")
        try:
            await init_nexus_agent()
        except Exception as e:
            logger.warning(f"Ошибка при инициализации NexusAgent (автономный режим): {e}")

    logger.info("Планировщик настроен.")
    from agents.shared.python.db import is_system_paused
    paused = is_system_paused()
    
    if paused:
        logger.info("Система находится на паузе, планировщик стартует в приостановленном состоянии.")
        
    scheduler.start(paused=paused)

    # Передаём scheduler в bot.py для управления авто-расписанием через /monitor
    from telegram.bot import set_scheduler as bot_set_scheduler
    from web.dashboard import set_scheduler as web_set_scheduler
    bot_set_scheduler(scheduler)
    web_set_scheduler(scheduler)

    logger.info("Запуск FastAPI...")
    api_task = asyncio.create_task(start_fastapi())

    logger.info("Запуск дашборда...")
    dashboard_task = asyncio.create_task(start_dashboard())

    if TELEGRAM_NOTIFICATIONS_ENABLED:
        polling_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    else:
        polling_task = None
    
    # Устанавливаем обработчики сигналов в самом конце, чтобы переопределить обработчики Playwright/Uvicorn
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: _request_shutdown())
        except NotImplementedError:
            # Fallback for systems where add_signal_handler is not fully supported
            signal.signal(sig, _request_shutdown)

    try:
        import config
        while not getattr(config, "shutdown_requested", False):
            await asyncio.sleep(1)
    except Exception as e:
        logger.error(f"Критическая ошибка в главном цикле: {e}", exc_info=True)
    finally:
        logger.info("🔧 Graceful shutdown (finally): завершаем все фоновые задачи...")
        
        # 1. Останавливаем polling бота если он был запущен
        if TELEGRAM_NOTIFICATIONS_ENABLED:
            try:
                await dp.stop_polling()
            except Exception:
                pass
            
        # 2. Ждём текущие jobs планировщика max 15 сек
        try:
            await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    None, lambda: scheduler.shutdown(wait=True)
                ),
                timeout=15.0
            )
            logger.info("✅ Scheduler остановлен корректно")
        except asyncio.TimeoutError:
            logger.warning("⏱ Scheduler jobs не завершились за 15с — принудительная остановка")
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Ошибка при остановке планировщика: {e}")
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                pass

        # 3. Отменяем фоновые задачи
        active_bg_tasks = [t for t in [polling_task, api_task, watchlist_task, dashboard_task] if t is not None]
        for task in active_bg_tasks:
            if not task.done():
                task.cancel()
                
        # 4. Ожидаем завершения отменённых задач
        if active_bg_tasks:
            await asyncio.gather(*active_bg_tasks, return_exceptions=True)
        
        # 5. Закрываем сессию если включен Telegram
        if TELEGRAM_NOTIFICATIONS_ENABLED:
            try:
                await bot.session.close()
            except Exception:
                pass
            
        # 6. Освобождаем сокет-лок
        global _lock_socket
        if _lock_socket:
            try:
                _lock_socket.close()
            except Exception:
                pass
                
        logger.info("✅ Shutdown завершён.")

if __name__ == "__main__":
    import sys
    exit_code = 0
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start_system())
    except SystemExit as e:
        logger.info("Система остановлена программно.")
        if isinstance(e.code, int):
            exit_code = e.code
    except KeyboardInterrupt:
        logger.info("Система остановлена пользователем (Ctrl+C).")
    finally:
        logger.info(f"🛑 Завершение работы процесса бота с кодом {exit_code}...")
        sys.exit(exit_code)

