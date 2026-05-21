"""
Единый конфигурационный модуль проекта.
Все пути и настройки вычисляются здесь и импортируются остальными модулями.
Переопределяются через переменные окружения (.env).
"""
import os
from pathlib import Path

# Корень проекта — директория, где лежит этот файл
PROJECT_ROOT = Path(__file__).parent

# Путь к vault (хранилище БД + Obsidian заметки)
VAULT_PATH = Path(os.getenv("VAULT_PATH", str(PROJECT_ROOT / "vault")))

# Путь к SQLite базе данных
DB_PATH = Path(os.getenv("DB_PATH", str(VAULT_PATH / "database.sqlite")))

# Дефолтные настройки сканирования
DEFAULT_SCAN_LIMIT = int(os.getenv("DEFAULT_SCAN_LIMIT", "10"))
MIN_EDGE_THRESHOLD = float(os.getenv("MIN_EDGE_THRESHOLD", "0.10"))

# Настройки памяти
MEMORY_FACTS_LIMIT = int(os.getenv("MEMORY_FACTS_LIMIT", "30"))  # Макс. фактов в системный промпт
CHAT_HISTORY_LIMIT = int(os.getenv("CHAT_HISTORY_LIMIT", "20"))  # Макс. сообщений в истории чата
STALE_MESSAGE_SECONDS = int(os.getenv("STALE_MESSAGE_SECONDS", "30"))  # Порог устаревших сообщений

# Стратегия отбора рынков
MARKET_COOLDOWN_HOURS = int(os.getenv("MARKET_COOLDOWN_HOURS", "4"))
MARKET_OFFSET_MAX = int(os.getenv("MARKET_OFFSET_MAX", "200"))
PRICE_RANGE_MIN = float(os.getenv("PRICE_RANGE_MIN", "0.10"))
PRICE_RANGE_MAX = float(os.getenv("PRICE_RANGE_MAX", "0.90"))
DEFAULT_MIN_EDGE = float(os.getenv("DEFAULT_MIN_EDGE", "0.10"))

# Категории для ротации при автоскане (culture=0 результатов на API)
SCAN_CATEGORIES = ["politics", "crypto", "sports", "science", "business"]

