import os
import sys
import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

# Добавляем корень проекта в путь
sys.path.append(os.getcwd())

from run_team import run_team_discussion
from telegram.bot import dp, bot

# Создаем папку для логов ДО настройки логирования
os.makedirs("logs", exist_ok=True)

# Настройка логирования
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
    logger.info(">>> Запуск планового сканирования рынков...")
    try:
        # Запускаем в отдельном потоке, чтобы не блокировать бота
        await asyncio.to_thread(run_team_discussion)
        logger.info("<<< Сканирование завершено успешно.")
    except Exception as e:
        logger.error(f"Ошибка при выполнении сканирования: {e}", exc_info=True)

async def start_system():
    load_dotenv()
    os.makedirs("logs", exist_ok=True)
    
    # 1. Настраиваем планировщик
    scheduler = AsyncIOScheduler()
    scheduler.add_job(scheduled_job, 'interval', minutes=30)
    scheduler.add_job(scheduled_job) # Первый запуск сразу
    
    logger.info("Планировщик настроен (интервал 30 мин).")
    scheduler.start()

    # 2. Запускаем Telegram-бота
    logger.info("🤖 Бот NEXUS запускается...")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Критическая ошибка бота: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(start_system())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Система остановлена.")
