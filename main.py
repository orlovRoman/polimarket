import os
import sys
import asyncio
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

# Добавляем корень проекта в путь поиска модулей для корректного импорта внутренних пакетов
sys.path.append(os.getcwd())

from core.engine import CoreEngine
from telegram.bot import dp, bot
from agents.shared.python.db import save_memory, cleanup_expired_memory, cleanup_chat_history, cleanup_old_price_history
import uvicorn
from core.api import app as fastapi_app
from config import logger  # Единый логгер из config.py — не дублируем basicConfig

engine = CoreEngine()

async def scheduled_job():
    """Периодическая задача: запуск анализа рынков движком."""
    logger.info(">>> Запуск планового сканирования рынков...")
    try:
        from agents.orchestrator.src.agent import NexusAgent
        nexus = NexusAgent()
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

async def scheduled_signal_evaluation():
    logger.info(">>> Оценка точности сигналов...")
    try:
        from services.signal_evaluator import evaluate_closed_signals
        stats = await asyncio.to_thread(evaluate_closed_signals)
        logger.info(f"<<< Оценка завершена: {stats}")
    except Exception as e:
        logger.error(f"Ошибка при оценке сигналов: {e}", exc_info=True)

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
    config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def start_system():
    load_dotenv()
    os.makedirs("logs", exist_ok=True)
    
    scheduler = AsyncIOScheduler()
    scheduler.add_job(scheduled_job, 'interval', minutes=5)
    scheduler.add_job(scheduled_job)  # немедленный запуск при старте
    scheduler.add_job(scheduled_memory_archive, 'interval', hours=24)
    scheduler.add_job(scheduled_trend_hunting, 'interval', hours=2)
    scheduler.add_job(scheduled_signal_evaluation, 'interval', hours=6)
    scheduler.add_job(scheduled_cross_arbitrage_scan, 'interval', hours=4)  # кросс-арбитраж каждые 4 ч

    logger.info("Планировщик настроен.")
    scheduler.start()

    logger.info("Запуск FastAPI...")
    api_task = asyncio.create_task(start_fastapi())

    logger.info("🤖 Бот NEXUS запускается...")
    try:
        from telegram.bot import set_commands
        await set_commands(bot)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Критическая ошибка бота: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    import sys
    try:
        asyncio.run(start_system())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Система остановлена пользователем.")
