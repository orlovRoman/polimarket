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
from agents.shared.python.db import save_memory

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
    Выполняется в отдельном потоке, чтобы не блокировать событийный цикл Telegram-бота.
    """
    logger.info(">>> Запуск планового сканирования рынков...")
    try:
        # Запускаем обсуждение команды в отдельном потоке
        await asyncio.to_thread(run_team_discussion)
        
        # Фиксируем время последнего успешного сканирования в локальной памяти
        save_memory("last_scan_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        
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
