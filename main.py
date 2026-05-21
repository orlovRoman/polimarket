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
from agents.shared.python.db import save_memory, cleanup_stale_signals, cleanup_expired_memory, cleanup_chat_history, cleanup_old_price_history

# Создаем директорию для логов, если она еще не существует
os.makedirs("logs", exist_ok=True)

# Глобальная настройка системы логирования
# Логи выводятся в файл и в консоль для удобства мониторинга
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/main.log"),
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
        # 0. Очистка устаревших сигналов (2025, истёкшие)
        stale_count = cleanup_stale_signals()
        if stale_count > 0:
            logger.info(f"Очищено устаревших сигналов: {stale_count}")

        # 1. Очистка через NexusAgent (проверка по close_time)
        from agents.orchestrator.src.agent import NexusAgent
        nexus = NexusAgent()
        cleanup_res = nexus.cleanup_expired_signals()
        logger.info(f"Очистка: {cleanup_res}")

        # 2. Запускаем основное обсуждение
        await asyncio.to_thread(run_team_discussion)
        
        # 3. Фиксируем время последнего успешного сканирования
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
    
    logger.info("Планировщик настроен (интервал 5 мин).")
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
