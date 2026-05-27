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
from telegram.bot import dp, bot, init_nexus_agent, get_nexus_agent
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
    except NoMarketsFoundError as e:
        logger.info(f"<<< Сканирование завершено: {e}")
    except RuntimeError as e:
        if "Сканирование уже выполняется" in str(e):
            logger.info(f"<<< Сканирование пропущено: {e}")
        else:
            logger.error(f"Ошибка при выполнении сканирования: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Ошибка при выполнении сканирования: {e}", exc_info=True)

async def scheduled_memory_archive():
    logger.info(">>> Запуск процесса автоархивации памяти и GC (Memory GC)...")
    try:
        from agents.orchestrator.scripts.memory_archiver import main as run_archiver
        await asyncio.to_thread(run_archiver)
        logger.info("<<< Автоархивация памяти и GC завершены.")
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
    except Exception as e:
        logger.error(f"Ошибка кросс-арбитражного скана: {e}", exc_info=True)

async def start_fastapi():
    """Запуск FastAPI сервера в фоне (через asyncio)"""
    config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=8000, log_level="info", install_signal_handlers=False)
    server = uvicorn.Server(config)
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
    
    # Обработчики завершения процесса (асинхронный graceful shutdown)
    loop = asyncio.get_running_loop()

    async def _shutdown():
        """Корректно завершает polling и закрывает соединения."""
        logger.info("🔧 Graceful shutdown: останавливаем polling...")
        await dp.stop_polling()
        try:
            await bot.session.close()
        except Exception:
            pass
        await asyncio.sleep(0.25)  # даём event loop завершить очередь задач
        logger.info("✅ Shutdown завершён.")

    def _request_shutdown():
        """Планирует асинхронный shutdown из синхронного обработчика сигнала."""
        logger.info("🚨 Получен сигнал завершения, запускаем shutdown...")
        loop.create_task(_shutdown())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_shutdown)
    
    scheduler = AsyncIOScheduler()
    scheduler.add_job(scheduled_job, 'interval', minutes=5)
    
    # Запускаем резолюцию рынков (и обновление episodes) каждые 6 часов
    from agents.shared.python.resolution import resolve_closed_markets
    
    def scheduled_resolution():
        try:
            logger.info(">>> Запуск автоматической резолюции закрытых рынков...")
            resolved = resolve_closed_markets()
            logger.info(f"<<< Резолюция завершена. Разрешено рынков: {resolved}")
        except Exception as e:
            logger.error(f"Ошибка при резолюции: {e}")

    scheduler.add_job(scheduled_resolution, 'interval', hours=6)
    
    scheduler.add_job(scheduled_job)  # немедленный запуск при старте
    scheduler.add_job(scheduled_memory_archive, 'interval', hours=24)
    scheduler.add_job(scheduled_trend_hunting, 'interval', hours=2)
    scheduler.add_job(scheduled_cross_arbitrage_scan, 'interval', hours=4)  # кросс-арбитраж каждые 4 ч

    logger.info("🤖 Бот NEXUS запускается...")
    try:
        # Option A+: явная асинхронная инициализация NexusAgent ДО начала polling и планировщика
        await init_nexus_agent()
        from telegram.bot import set_commands
        await set_commands(bot)
    except Exception as e:
        logger.error(f"Критическая ошибка инициализации бота/агента: {e}")
        sys.exit(1)

    logger.info("Планировщик настроен.")
    scheduler.start()

    logger.info("Запуск FastAPI...")
    api_task = asyncio.create_task(start_fastapi())

    polling_task = asyncio.create_task(dp.start_polling(bot))
    try:
        await asyncio.wait(
            [polling_task, api_task],
            return_when=asyncio.FIRST_COMPLETED
        )
    except Exception as e:
        logger.error(f"Критическая ошибка в главном цикле: {e}")
    finally:
        logger.info("🔧 Graceful shutdown: останавливаем все фоновые задачи...")
        await dp.stop_polling()
        polling_task.cancel()
        api_task.cancel()
        await asyncio.gather(polling_task, api_task, return_exceptions=True)
        try:
            await bot.session.close()
        except Exception:
            pass
        logger.info("✅ Shutdown завершён.")

if __name__ == "__main__":
    import sys
    try:
        asyncio.run(start_system())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Система остановлена пользователем.")
