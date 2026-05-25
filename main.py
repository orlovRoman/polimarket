import os
import sys
import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

# Добавляем корень проекта в путь поиска модулей для корректного импорта внутренних пакетов
sys.path.append(os.getcwd())

from run_team import run_team_discussion
from telegram.bot import dp, bot
from agents.shared.python.db import save_memory, cleanup_expired_memory, cleanup_chat_history, cleanup_old_price_history



import logging
from logging.handlers import RotatingFileHandler

# Глобальная настройка системы логирования
# Логи выводятся в файл и в консоль для удобства мониторинга
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler("logs/main.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("NEXUS_SYSTEM")

async def scheduled_job():
    """
    Периодическая задача: запуск анализа рынков всей командой агентов.
    """
    logger.info(">>> Запуск планового сканирования рынков...")
    try:
        # 1. Очистка через NexusAgent (проверка по close_time)
        from agents.orchestrator.src.agent import NexusAgent
        nexus = NexusAgent()
        cleanup_res = nexus.cleanup_expired_signals()
        logger.info(f"Очистка: {cleanup_res}")

        # 2. Запускаем основное обсуждение
        processed_count = await asyncio.to_thread(run_team_discussion)
        
        # 3. Фиксируем время последнего успешного сканирования (только если рынки были обработаны)
        if processed_count and processed_count > 0:
            save_memory("last_scan_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        
        # 4. Чистим истёкшие записи из памяти (TTL)
        expired = cleanup_expired_memory()
        if expired > 0:
            logger.info(f"Очищено истёкших записей памяти: {expired}")
        
        # 5. Очистка старой истории цен (старше 7 дней)
        old_prices = cleanup_old_price_history(days=7)
        if old_prices > 0:
            logger.info(f"Очищено старых записей истории цен: {old_prices}")

        logger.info("<<< Сканирование завершено успешно.")
    except Exception as e:
        logger.error(f"Ошибка при выполнении сканирования: {e}", exc_info=True)

async def scheduled_memory_archive():
    """
    Периодическая задача: автобэкап БД, архивация памяти и сборка мусора (раз в 24 часа).
    """
    logger.info(">>> Запуск процесса автоархивации памяти и GC (Memory GC)...")
    try:
        from agents.orchestrator.scripts.memory_archiver import main as run_archiver
        # Запускаем в фоновом потоке, так как там есть вызовы Gemini API
        await asyncio.to_thread(run_archiver)
        logger.info("<<< Автоархивация памяти и GC завершены.")
    except Exception as e:
        logger.error(f"Ошибка при архивации памяти: {e}", exc_info=True)

async def scheduled_trend_hunting():
    """
    Периодическая задача: проактивный поиск трендов на основе новостей.
    Запускается только если Trend Hunter включен в настройках.
    """
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
    """Оценивает точность сигналов по закрытым рынкам — раз в 6 часов."""
    logger.info(">>> Оценка точности сигналов...")
    try:
        from services.signal_evaluator import evaluate_closed_signals
        stats = await asyncio.to_thread(evaluate_closed_signals)
        logger.info(f"<<< Оценка завершена: {stats}")
    except Exception as e:
        logger.error(f"Ошибка при оценке сигналов: {e}", exc_info=True)

async def start_system():
    """
    Основная точка входа в систему. Инициализирует окружение, 
    настраивает планировщик задач и запускает Telegram-бота.
    """
    # Загружаем переменные окружения из .env
    load_dotenv()
    os.makedirs("logs", exist_ok=True)
    
    # 1. Настройка и запуск планировщика задач (APScheduler)
    scheduler = AsyncIOScheduler()
    # Устанавливаем интервал сканирования - 5 минут
    scheduler.add_job(scheduled_job, 'interval', minutes=5)
    # Выполняем первый запуск немедленно при старте системы
    scheduler.add_job(scheduled_job) 
    
    # Автоархивация и сборка мусора (GC) — запускаем раз в 24 часа
    scheduler.add_job(scheduled_memory_archive, 'interval', hours=24)
    
    # Проактивный поиск трендов (Trend Hunter) — запускаем раз в 2 часа
    scheduler.add_job(scheduled_trend_hunting, 'interval', hours=2)

    # Оценка точности сигналов — раз в 6 часов
    scheduler.add_job(scheduled_signal_evaluation, 'interval', hours=6)

    logger.info("Планировщик настроен (интервал 5 мин, GC 24 ч, Trend Hunter 2 ч, Evaluator 6 ч).")
    scheduler.start()

    # 2. Инициализация и запуск Telegram-бота (Aiogram)
    logger.info("🤖 Бот NEXUS запускается...")
    try:
        # Запуск поллинга сообщений
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Критическая ошибка бота: {e}")
    finally:
        # Корректное закрытие сессии бота при выходе
        await bot.session.close()

if __name__ == "__main__":
    # Точка входа в скрипт
    try:
        asyncio.run(start_system())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Система остановлена пользователем.")
