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

# Стратегия скана
SCAN_LIMIT_DEFAULT = int(os.getenv("SCAN_LIMIT", "30"))
MIN_EDGE_DEFAULT = float(os.getenv("MIN_EDGE", "0.05"))
