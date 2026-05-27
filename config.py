"""
Единый конфигурационный модуль проекта.
Все пути и настройки вычисляются здесь и импортируются остальными модулями.
Переопределяются через переменные окружения (.env).
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Корень проекта — директория, где лежит этот файл
PROJECT_ROOT = Path(__file__).parent

# Загружаем переменные окружения из .env
load_dotenv(PROJECT_ROOT / ".env")

# Путь к vault (хранилище БД + Obsidian заметки)
VAULT_PATH = Path(os.getenv("VAULT_PATH", str(PROJECT_ROOT / "vault")))

# Путь к SQLite базе данных
DB_PATH = Path(os.getenv("DB_PATH", str(VAULT_PATH / "database.sqlite")))

# Настройки памяти
MEMORY_FACTS_LIMIT = int(os.getenv("MEMORY_FACTS_LIMIT", "30"))  # Макс. фактов в системный промпт

# Стратегия отбора рынков
MARKET_COOLDOWN_HOURS = int(os.getenv("MARKET_COOLDOWN_HOURS", "4"))
MARKET_OFFSET_MAX = int(os.getenv("MARKET_OFFSET_MAX", "200"))
PRICE_RANGE_MIN = float(os.getenv("PRICE_RANGE_MIN", "0.10"))
PRICE_RANGE_MAX = float(os.getenv("PRICE_RANGE_MAX", "0.90"))

# Категории для ротации при автоскане (culture=0 результатов на API)
SCAN_CATEGORIES = ["politics", "crypto", "sports", "science", "business"]

# Настройки Telegram Userbot для Telethon
TG_API_ID = os.getenv("TG_API_ID", "")
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_PHONE = os.getenv("TG_PHONE", "")

# Ключи бота и чата для уведомлений
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Настройки для второй группы (Event-driven)
TELEGRAM_GROUP2_SOURCE = os.getenv("TELEGRAM_GROUP2_SOURCE", "group2_source") # Канал, который слушает Telethon
TELEGRAM_GROUP2_TARGET_ID = os.getenv("TELEGRAM_GROUP2_TARGET_ID", "") # Чат, куда бот отправляет аналитику

# Стратегия скана
SCAN_LIMIT_DEFAULT = int(os.getenv("SCAN_LIMIT", "30"))
MIN_EDGE_DEFAULT = float(os.getenv("MIN_EDGE", "0.05"))
WHALE_ALERT_MIN_USD = float(os.getenv("WHALE_ALERT_MIN_USD", "10000"))  # Мин. сумма для whale-алерта

# Настройки кросс-платформенного арбитража
ARB_POLY_LIMIT = int(os.getenv("ARB_POLY_LIMIT", "100"))
ARB_KALSHI_LIMIT = int(os.getenv("ARB_KALSHI_LIMIT", "100"))
ARB_MIN_MATCH_SCORE = float(os.getenv("ARB_MIN_MATCH_SCORE", "0.50"))
ARB_MIN_SPREAD_ALERT = float(os.getenv("ARB_MIN_SPREAD_ALERT", "5.0"))
ARB_MAX_DAYS_DIFF = int(os.getenv("ARB_MAX_DAYS_DIFF", "30"))

# Интеграции
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# Настройки блокировки
LOCK_FILE = str(PROJECT_ROOT / "vault" / "scan.lock")
LOCK_TIMEOUT_SEC = 600
SCREENING_INTERVAL_SEC = 1800

# Логирование
import logging
from logging.handlers import RotatingFileHandler

LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)
LOG_PATH = LOGS_DIR / "main.log"

def setup_logger(name="NexusPolyBot"):
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    if not log.handlers:
        file_handler = RotatingFileHandler(LOG_PATH, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        log.addHandler(file_handler)
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        log.addHandler(console_handler)
    return log

logger = setup_logger()
