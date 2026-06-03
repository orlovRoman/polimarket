import os
import sys
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
from agents.shared.python.db import save_memory, cleanup_expired_memory, cleanup_chat_history, cleanup_old_price_history
import uvicorn
from core.api import app as fastapi_app
from config import logger  # Единый логгер из config.py — не дублируем basicConfig

engine = CoreEngine()

async def scheduled_job():
    """Периодическая задача: запуск анализа рынков движком."""
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
            save_memory("last_scan_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        
        expired = cleanup_expired_memory()
        if expired > 0:
            logger.info(f"Очищено истёкших записей памяти: {expired}")
        
        old_prices = cleanup_old_price_history(days=7)
        if old_prices > 0:
            logger.info(f"Очищено старых записей истории цен: {old_prices}")

        logger.info("<<< Сканирование завершено успешно.")
    except asyncio.CancelledError:
        logger.info("<<< Сканирование прервано: получен сигнал завершения (CancelledError).")
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
    logger.info(">>> Запуск процесса автоархивации памяти и GC (Memory GC)...")
    try:
        from agents.orchestrator.scripts.memory_archiver import main as run_archiver
        await asyncio.to_thread(run_archiver)
        logger.info("<<< Автоархивация памяти и GC завершены.")
    except asyncio.CancelledError:
        logger.info("<<< Процесс автоархивации памяти отменен.")
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
    except Exception as e:
        logger.error(f"Ошибка при работе Trend Hunter: {e}", exc_info=True)



async def scheduled_cross_arbitrage_scan():
    """Автоматический кросс-платформенный арбитражный скан (Polymarket ↔ Kalshi)."""
    logger.info(">>> Кросс-арбитраж: запуск скана...")
    try:
        from core.arbitrage_workflow import run_cross_platform_scan
        from services.notifications import send_cross_arbitrage_alerts
        import os
        api_key = os.getenv("GOOGLE_API_KEY")
        found = await asyncio.to_thread(
            run_cross_platform_scan,
            api_key=api_key,
            poly_limit=100,
            kalshi_limit=100,
        )
        if found:
            await asyncio.to_thread(send_cross_arbitrage_alerts)
        logger.info(f"<<< Кросс-арбитраж: найдено {len(found)} алертов")
    except asyncio.CancelledError:
        logger.info("<<< Кросс-арбитражный скан отменен.")
    except Exception as e:
        logger.error(f"Ошибка кросс-арбитражного скана: {e}", exc_info=True)

async def scheduled_synthetic_corridors():
    """Скан внутрирыночных синтетических коридоров (Polymarket)."""
    logger.info(">>> Синтетические коридоры: запуск скана...")
    try:
        from services.synthetic_corridor_scanner import run_synthetic_corridor_scan
        from services.notifications import send_synthetic_corridor_alerts
        from config import CORRIDOR_BUDGET_PER_TRADE
        found = await asyncio.to_thread(
            run_synthetic_corridor_scan,
            poly_limit=100,
            budget_per_trade=CORRIDOR_BUDGET_PER_TRADE,
        )
        if found:
            await asyncio.to_thread(send_synthetic_corridor_alerts)
        logger.info(f"<<< Синтетические коридоры: найдено {len(found)} алертов")
    except asyncio.CancelledError:
        logger.info("<<< Сканирование синтетических коридоров отменено.")
    except Exception as e:
        logger.error(f"Ошибка сканирования синтетических коридоров: {e}", exc_info=True)

async def scheduled_temporal_corridors():
    """Скан временных коридоров (Temporal Arbitrage)."""
    logger.info(">>> Временные коридоры: запуск скана...")
    try:
        from services.temporal_corridor_scanner import run_temporal_corridor_scan
        from services.notifications import send_temporal_corridor_alerts
        from config import CORRIDOR_BUDGET_PER_TRADE
        found = await asyncio.to_thread(
            run_temporal_corridor_scan,
            poly_limit=100,
            budget=CORRIDOR_BUDGET_PER_TRADE,
        )
        if found:
            await asyncio.to_thread(send_temporal_corridor_alerts)
        logger.info(f"<<< Временные коридоры: найдено {len(found)} алертов")
    except asyncio.CancelledError:
        logger.info("<<< Сканирование временных коридоров отменено.")
    except Exception as e:
        logger.error(f"Ошибка сканирования временных коридоров: {e}", exc_info=True)

async def scheduled_wallet_recalculation():
    """Периодический пересчет win_rate кошельков."""
    logger.info(">>> Запуск периодического пересчета win_rate крупных кошельков...")
    try:
        from services.wallet_tracker import recalculate_win_rates
        await asyncio.to_thread(recalculate_win_rates)
        logger.info("<<< Пересчет win_rate кошельков завершен успешно.")
    except asyncio.CancelledError:
        logger.info("<<< Пересчет win_rate кошельков отменен.")
    except Exception as e:
        logger.error(f"Ошибка при пересчете win_rate кошельков: {e}", exc_info=True)

async def job_onchain_alerts():
    """Фоновый скан и отправка ончейн-всплесков объёма."""
    logger.info(">>> Запуск сканирования ончейн-всплесков объёма...")
    try:
        from services.onchain_trend_alert import scan_volume_spikes, build_spike_message
        from agents.shared.python.db import mark_alert_sent
        
        spikes = await asyncio.to_thread(scan_volume_spikes)
        for spike in spikes:
            msg = build_spike_message(spike)
            # Отправляем сообщение в авторизованный чат
            await bot.send_message(AUTHORIZED_CHAT_ID, msg, parse_mode="HTML", disable_web_page_preview=True)
            mark_alert_sent(f"onchain_spike_{spike['market_id']}", "onchain_spike")
            logger.info(f"Отправлен ончейн-алерт по рынку: {spike['title']}")
        logger.info("<<< Сканирование ончейн-всплесков завершено.")
    except asyncio.CancelledError:
        logger.info("<<< Сканирование ончейн-всплесков отменено.")
    except Exception as e:
        logger.error(f"Ошибка при сканировании ончейн-всплесков: {e}", exc_info=True)

async def start_fastapi():
    """Запуск FastAPI сервера в фоне (через asyncio)"""
    config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # отключаем перехват сигналов uvicorn'ом
    await server.serve()

import socket

_lock_socket = None

def ensure_single_instance():
    """Гарантирует, что запущена только одна копия бота через привязку к уникальному порту.
    Это надежнее файловых локов (особенно на сетевых дисках CIFS/SMB)."""
    global _lock_socket
    _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Позволяем быстрому повторному привязыванию порта
    _lock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # SO_REUSEPORT может отсутствовать в Windows, проверяем наличие
    if hasattr(socket, "SO_REUSEPORT"):
        _lock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    try:
        # Пытаемся занять фиксированный локальный порт
        _lock_socket.bind(("127.0.0.1", 61234))
        # Захватываем порт эксклюзивно
        _lock_socket.listen(0)
    except OSError:
        logger.error("[КРИТИЧЕСКАЯ ОШИБКА] Другая копия бота уже работает! Экстренное завершение...")
        sys.exit(1)

async def start_system():
    load_dotenv()
    from config import startup_check
    startup_check()
    os.makedirs("logs", exist_ok=True)
    
    # Жесткая блокировка повторных запусков
    ensure_single_instance()
    
    # Объявляем переменные задач для graceful shutdown
    polling_task = None
    api_task = None
    watchlist_task = None
    
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
        for task in [polling_task, api_task, watchlist_task]:
            if task and not task.done():
                task.cancel()
    
    # Авто-запуск сканирования ОТКЛЮЧЁН — управление через /monitor в Telegram
    # scheduler.add_job(scheduled_job, 'interval', minutes=5)
    
    # Запускаем резолюцию рынков (и обновление episodes) каждые 6 часов
    from agents.shared.python.resolution import resolve_closed_markets
    
    def scheduled_resolution():
        try:
            logger.info(">>> Запуск автоматической резолюции закрытых рынков...")
            resolved = resolve_closed_markets()
            logger.info(f"<<< Резолюция завершена. Разрешено рынков: {resolved}")
        except Exception as e:
            logger.error(f"Ошибка при резолюции: {e}")

    scheduler.add_job(scheduled_resolution, 'interval', hours=2)

    async def scheduled_eval_resolutions():
        try:
            logger.info(">>> Запуск автоматической резолюции сигналов в Evaluation Engine...")
            from services.resolution_fetcher import ResolutionFetcher
            fetcher = ResolutionFetcher()
            resolved = await fetcher.fetch_pending_resolutions()
            logger.info(f"<<< Автоматическая резолюция сигналов завершена. Обновлено сигналов: {resolved}")
        except Exception as e:
            logger.error(f"Ошибка при резолюции сигналов в Evaluation Engine: {e}", exc_info=True)

    scheduler.add_job(scheduled_eval_resolutions, 'interval', hours=1, id="eval_resolutions_job", replace_existing=True)
    
    scheduler.add_job(scheduled_memory_archive, 'interval', hours=24)
    scheduler.add_job(scheduled_trend_hunting, 'interval', hours=2)
    scheduler.add_job(scheduled_cross_arbitrage_scan, 'interval', hours=4)  # кросс-арбитраж каждые 4 ч
    scheduler.add_job(scheduled_synthetic_corridors, 'interval', minutes=15) # синтетические коридоры каждые 15 м
    scheduler.add_job(scheduled_temporal_corridors, 'interval', minutes=30) # временные коридоры каждые 30 м
    scheduler.add_job(scheduled_wallet_recalculation, 'cron', hour=3) # пересчет win_rate кошельков раз в сутки в 3:00 ночи
    scheduler.add_job(job_onchain_alerts, 'interval', minutes=30) # ончейн-алерты всплесков объема каждые 30 минут

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

    logger.info("Планировщик настроен.")
    scheduler.start()

    # Передаём scheduler в bot.py для управления авто-расписанием через /monitor
    from telegram.bot import set_scheduler
    set_scheduler(scheduler)

    logger.info("Запуск FastAPI...")
    api_task = asyncio.create_task(start_fastapi())

    polling_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    
    # Устанавливаем обработчики сигналов в самом конце, чтобы переопределить обработчики Playwright/Uvicorn
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: _request_shutdown())
        except NotImplementedError:
            # Fallback for systems where add_signal_handler is not fully supported
            signal.signal(sig, _request_shutdown)

    try:
        done, pending = await asyncio.wait(
            [polling_task, api_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            try:
                exc = task.exception()
                if exc:
                    logger.error(f"Задача завершилась с ошибкой: {exc}", exc_info=exc)
                else:
                    logger.info("Задача успешно завершилась.")
            except asyncio.CancelledError:
                logger.info("Задача была отменена.")
    except Exception as e:
        logger.error(f"Критическая ошибка в главном цикле: {e}", exc_info=True)
    finally:
        logger.info("🔧 Graceful shutdown (finally): завершаем все фоновые задачи...")
        
        # 1. Останавливаем polling бота
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
        for task in [polling_task, api_task, watchlist_task]:
            if task and not task.done():
                task.cancel()
                
        # 4. Ожидаем завершения отменённых задач
        await asyncio.gather(polling_task, api_task, watchlist_task, return_exceptions=True)
        
        # 5. Закрываем сессию
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

