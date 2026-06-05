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
load_dotenv(PROJECT_ROOT / ".env", override=True)

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
MIN_MARKET_VOLUME_USD = int(os.getenv("MIN_MARKET_VOLUME_USD", "5000"))

# Категории для ротации при автоскане (culture=0 результатов на API)
SCAN_CATEGORIES = os.getenv("SCAN_CATEGORIES", "politics,crypto,sports,science,business").split(",")

# Настройки Telegram Userbot для Telethon
TG_API_ID = os.getenv("TG_API_ID", "")
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_PHONE = os.getenv("TG_PHONE", "")

# Ключи бота и чата для уведомлений
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_BOT_ID = os.getenv("TELEGRAM_BOT_ID", "")

# Настройки для второй группы (Event-driven)
TELEGRAM_GROUP2_SOURCE = os.getenv("TELEGRAM_GROUP2_SOURCE", "group2_source") # Канал, который слушает Telethon
TELEGRAM_GROUP2_TARGET_ID = os.getenv("TELEGRAM_GROUP2_TARGET_ID", "") # Чат, куда бот отправляет аналитику

# Стратегия скана
SCAN_LIMIT_DEFAULT = int(os.getenv("SCAN_LIMIT", "30"))
MIN_EDGE_DEFAULT = float(os.getenv("MIN_EDGE", "0.05"))
WHALE_ALERT_MIN_USD = float(os.getenv("WHALE_ALERT_MIN_USD", "10000"))  # Мин. сумма для whale-алерта

# Настройки Whale Gate
WHALE_GATE_MIN_CONFIDENCE = float(os.getenv("WHALE_GATE_MIN_CONFIDENCE", "0.5"))
WHALE_GATE_MIN_COUNT = int(os.getenv("WHALE_GATE_MIN_COUNT", "2"))

# Настройки Swing On-chain Gatekeeper
SWING_MIN_VOLUME_USD = float(os.getenv("SWING_MIN_VOLUME_USD", "5000"))
SWING_MIN_WHALE_COUNT = int(os.getenv("SWING_MIN_WHALE_COUNT", "1"))
SWING_VOLUME_BY_TAG = {
    "politics": float(os.getenv("SWING_VOLUME_POLITICS", "20000")),
    "crypto":   float(os.getenv("SWING_VOLUME_CRYPTO",   "10000")),
    "sports":   float(os.getenv("SWING_VOLUME_SPORTS",   "2000")),
    "science":  float(os.getenv("SWING_VOLUME_SCIENCE",  "1500")),
}

# Настройки кросс-платформенного арбитража
ARB_POLY_LIMIT = int(os.getenv("ARB_POLY_LIMIT", "100"))
ARB_KALSHI_LIMIT = int(os.getenv("ARB_KALSHI_LIMIT", "100"))
ARB_MIN_MATCH_SCORE = float(os.getenv("ARB_MIN_MATCH_SCORE", "0.50"))
ARB_MIN_SPREAD_ALERT = float(os.getenv("ARB_MIN_SPREAD_ALERT", "5.0"))
ARB_MAX_DAYS_DIFF = int(os.getenv("ARB_MAX_DAYS_DIFF", "30"))
ARB_MIN_SPREAD_PCT = float(os.getenv("ARB_MIN_SPREAD_PCT", "2.0"))

# Максимальное число рынков, передаваемое в NEXUS скринер
MAX_SCREENING_MARKETS = int(os.getenv("MAX_SCREENING_MARKETS", "120"))

# Бюджет на один арбитражный трейд (USD)
CORRIDOR_BUDGET_PER_TRADE = float(os.getenv("CORRIDOR_BUDGET_PER_TRADE", "200.0"))

# Кэш Polymarket API
POLY_EVENTS_CACHE_TTL_SECONDS = int(os.getenv("POLY_EVENTS_CACHE_TTL", "300"))

# Интеграции
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_API_KEY_SECONDARY = os.getenv("GOOGLE_API_KEY_SECONDARY", "")
GOOGLE_API_KEY_THIRD = os.getenv("GOOGLE_API_KEY_THIRD", "")

# Настройки блокировки
LOCK_FILE = Path(PROJECT_ROOT / "vault" / "scan.lock")
LOCK_TIMEOUT_SEC = 600
SCREENING_INTERVAL_SEC = 1800

# Health Gate (Lazy)
_llm_health_gate = None

def get_llm_health_gate():
    global _llm_health_gate
    if _llm_health_gate is None:
        from core.guards import LLMHealthGate
        _llm_health_gate = LLMHealthGate()
    return _llm_health_gate

# Логирование
import logging
from logging.handlers import RotatingFileHandler

LOGS_DIR = PROJECT_ROOT / "logs"
LOG_PATH = LOGS_DIR / "main.log"
AGENT_REPORTS_PATH = LOGS_DIR / "agent_reports.log"

def setup_logger(name="NexusPolyBot"):
    log = logging.getLogger(name)
    if log.handlers:
        return log
        
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG)
    log.propagate = False
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Handler 1: основной лог (всё)
    file_handler = RotatingFileHandler(LOG_PATH, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)
    
    # Handler 2: консоль
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    log.addHandler(console_handler)
    
    return log

def get_report_logger():
    """Возвращает логгер для агентских отчётов, инициализируя его при первом вызове."""
    report_log = logging.getLogger("AgentReports")
    if not report_log.handlers:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        report_formatter = logging.Formatter('[%(asctime)s] [%(name)s]\n%(message)s\n' + '-'*60)
        report_handler = RotatingFileHandler(
            AGENT_REPORTS_PATH, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
        )
        report_handler.setFormatter(report_formatter)
        report_handler.setLevel(logging.INFO)
        report_log.addHandler(report_handler)
        report_log.setLevel(logging.INFO)
        report_log.propagate = False
    return report_log

_logger_instance = None

# PEP 562 lazy loading for llm_health_gate and logger
def __getattr__(name):
    global _logger_instance
    if name == "llm_health_gate":
        return get_llm_health_gate()
    if name == "logger":
        if _logger_instance is None:
            _logger_instance = setup_logger()
        return _logger_instance
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def startup_check():
    """
    Валидация окружения перед стартом приложения.
    """
    missing = []
    if not GOOGLE_API_KEY:
        missing.append("GOOGLE_API_KEY")
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if not TG_API_ID or not TG_API_HASH:
        missing.append("TG_API_ID / TG_API_HASH (нужны для Telethon userbot)")
        
    if missing:
        raise RuntimeError(f"Отсутствуют обязательные переменные окружения: {', '.join(missing)}")
        
    # Test ping Google API Keys
    import requests
    api_keys_to_check = [("GOOGLE_API_KEY", GOOGLE_API_KEY)]
    for name in sorted(os.environ.keys()):
        if name.startswith("GOOGLE_API_KEY_"):
            val = os.getenv(name, "")
            if val and val.strip():
                if name != "GOOGLE_API_KEY":
                    api_keys_to_check.append((name, val))

    for key_name, key_val in api_keys_to_check:
        if not key_val:
            continue
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key_val}"
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
        except Exception as e:
            if key_name == "GOOGLE_API_KEY":
                raise RuntimeError(f"Первичный GOOGLE_API_KEY недействителен, истек или недоступен: {e}")
            else:
                logger.warning(f"⚠️ {key_name} недействителен или недоступен: {e}")
        
    # Убеждаемся, что системные папки существуют
    VAULT_PATH.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

shutdown_requested = False

__all__ = [
    "PROJECT_ROOT", "VAULT_PATH", "DB_PATH", "MEMORY_FACTS_LIMIT",
    "MARKET_COOLDOWN_HOURS", "MARKET_OFFSET_MAX", "PRICE_RANGE_MIN", "PRICE_RANGE_MAX",
    "MIN_MARKET_VOLUME_USD", "SCAN_CATEGORIES", "TG_API_ID", "TG_API_HASH", "TG_PHONE",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_BOT_ID",
    "TELEGRAM_GROUP2_SOURCE", "TELEGRAM_GROUP2_TARGET_ID",
    "SCAN_LIMIT_DEFAULT", "MIN_EDGE_DEFAULT", "WHALE_ALERT_MIN_USD",
    "WHALE_GATE_MIN_CONFIDENCE", "WHALE_GATE_MIN_COUNT",
    "SWING_MIN_VOLUME_USD", "SWING_MIN_WHALE_COUNT", "SWING_VOLUME_BY_TAG",
    "ARB_POLY_LIMIT", "ARB_KALSHI_LIMIT", "ARB_MIN_MATCH_SCORE",
    "ARB_MIN_SPREAD_ALERT", "ARB_MAX_DAYS_DIFF", "ARB_MIN_SPREAD_PCT", "MAX_SCREENING_MARKETS",
    "CORRIDOR_BUDGET_PER_TRADE", "POLY_EVENTS_CACHE_TTL_SECONDS",
    "GOOGLE_API_KEY", "GOOGLE_API_KEY_SECONDARY", "GOOGLE_API_KEY_THIRD",
    "LOCK_FILE", "LOCK_TIMEOUT_SEC", "SCREENING_INTERVAL_SEC",
    "get_llm_health_gate", "setup_logger", "startup_check", "shutdown_requested"
]
