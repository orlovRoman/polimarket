# agents/shared/python/penny_settings_db.py
"""
CRUD для настроек Penny-стратегии.
Таблицы: penny_settings, penny_settings_audit, penny_runtime_state
Зависит от: agents/shared/python/db.py (get_connection)
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from agents.shared.python.db import get_connection

logger = logging.getLogger("NexusPolyBot.PennySettingsDB")

PENNY_DEFAULTS: dict[str, str] = {
    "wallet_address": "",
    "trading_mode": "paper",
    "live_trading_enabled": "0",
    "bet_size_usdc": "1.0",
    "max_bet_size_usdc": "5.0",
    "max_open_positions": "10",
    "daily_budget_usdc": "20.0",
    "min_probability": "0.01",
    "max_probability": "0.09",
    "min_confidence_score": "0.5",
    "min_volume_24h": "50.0",
    "min_hours_to_close": "2.0",
    "max_hours_to_close": "168.0",
    "auto_buy_enabled": "0",
    "kill_switch": "0",
    "require_preflight_for_autobuy": "1",
    "preflight_min_usdc_buffer": "5.0"
}

@dataclass(frozen=True)
class PennyStocksConfig:
    wallet_address: str
    trading_mode: str
    live_trading_enabled: bool
    bet_size_usdc: float
    max_bet_size_usdc: float
    max_open_positions: int
    daily_budget_usdc: float
    min_probability: float
    max_probability: float
    min_confidence_score: float
    min_volume_24h: float
    min_hours_to_close: float
    max_hours_to_close: float
    auto_buy_enabled: bool
    kill_switch: bool
    require_preflight_for_autobuy: bool
    preflight_min_usdc_buffer: float
    updated_at: str
    is_fallback: bool = False
    validation_error: str | None = None


def init_penny_settings_table(conn=None) -> None:
    """
    Создаёт таблицы penny_settings, penny_settings_audit, penny_runtime_state если не существуют.
    Вызывается из init_db() в db.py.
    """
    if conn is None:
        with get_connection() as conn_obj:
            _init_penny_settings_table_impl(conn_obj)
    else:
        _init_penny_settings_table_impl(conn)

def _init_penny_settings_table_impl(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS penny_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS penny_settings_audit (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            changed_at TEXT DEFAULT (datetime('now', 'localtime')),
            changed_by TEXT DEFAULT 'system',
            key        TEXT NOT NULL,
            old_value  TEXT,
            new_value  TEXT,
            source     TEXT DEFAULT 'ui'
        )
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS penny_runtime_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    
    # Записываем дефолты
    for k, v in PENNY_DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO penny_settings (key, value) VALUES (?, ?)",
            (k, v),
        )
        
    # Записываем временную отметку обновления если её нет
    conn.execute(
        "INSERT OR IGNORE INTO penny_runtime_state (key, value) VALUES ('updated_at', ?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)
    )

def get_penny_settings_raw() -> dict[str, str]:
    """Возвращает сырые строковые настройки из БД."""
    with get_connection() as conn:
        rows = conn.execute("SELECT key, value FROM penny_settings").fetchall()
        raw = {r["key"]: r["value"] for r in rows}
        
    # Дополняем отсутствующие дефолтами
    for k, v in PENNY_DEFAULTS.items():
        if k not in raw:
            raw[k] = v
    return raw

def get_penny_stocks_config() -> PennyStocksConfig:
    """Читает текущую конфигурацию с приведением типов и валидацией инвариантов."""
    raw = get_penny_settings_raw()
    
    with get_connection() as conn:
        row_state = conn.execute("SELECT value FROM penny_runtime_state WHERE key = 'updated_at'").fetchone()
        updated_at = row_state["value"] if row_state else datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        wallet_address = raw.get("wallet_address", "").strip()
        trading_mode = raw.get("trading_mode", "paper").strip()
        if trading_mode not in ("paper", "live"):
            trading_mode = "paper"
            
        live_trading_enabled = raw.get("live_trading_enabled", "0").strip() == "1"
        bet_size_usdc = float(raw.get("bet_size_usdc", "1.0"))
        max_bet_size_usdc = float(raw.get("max_bet_size_usdc", "5.0"))
        max_open_positions = int(raw.get("max_open_positions", "10"))
        daily_budget_usdc = float(raw.get("daily_budget_usdc", "20.0"))
        min_probability = float(raw.get("min_probability", "0.01"))
        max_probability = float(raw.get("max_probability", "0.09"))
        min_confidence_score = float(raw.get("min_confidence_score", "0.5"))
        min_volume_24h = float(raw.get("min_volume_24h", "50.0"))
        min_hours_to_close = float(raw.get("min_hours_to_close", "2.0"))
        max_hours_to_close = float(raw.get("max_hours_to_close", "168.0"))
        auto_buy_enabled = raw.get("auto_buy_enabled", "0").strip() == "1"
        kill_switch = raw.get("kill_switch", "0").strip() == "1"
        require_preflight_for_autobuy = raw.get("require_preflight_for_autobuy", "1").strip() == "1"
        preflight_min_usdc_buffer = float(raw.get("preflight_min_usdc_buffer", "5.0"))

        # Проверка инвариантов бизнес-логики
        if not (0.0 < bet_size_usdc <= max_bet_size_usdc <= daily_budget_usdc):
            raise ValueError("Размеры ставок нарушают инвариант: 0 < bet_size <= max_bet_size <= daily_budget")
        if not (1 <= max_open_positions <= 50):
            raise ValueError("Лимит открытых позиций должен быть между 1 и 50")
        if not (0.0 <= min_probability < max_probability < 1.0):
            raise ValueError("Диапазон вероятностей нарушает инвариант: 0 <= min_prob < max_prob < 1.0")
        if not (0.0 <= min_confidence_score <= 1.0):
            raise ValueError("Минимальный confidence должен быть между 0.0 и 1.0")
        if not (0.0 <= min_hours_to_close < max_hours_to_close):
            raise ValueError("Лимит часов до закрытия нарушает инвариант: 0 <= min_hours < max_hours")
            
        return PennyStocksConfig(
            wallet_address=wallet_address,
            trading_mode=trading_mode,
            live_trading_enabled=live_trading_enabled,
            bet_size_usdc=bet_size_usdc,
            max_bet_size_usdc=max_bet_size_usdc,
            max_open_positions=max_open_positions,
            daily_budget_usdc=daily_budget_usdc,
            min_probability=min_probability,
            max_probability=max_probability,
            min_confidence_score=min_confidence_score,
            min_volume_24h=min_volume_24h,
            min_hours_to_close=min_hours_to_close,
            max_hours_to_close=max_hours_to_close,
            auto_buy_enabled=auto_buy_enabled,
            kill_switch=kill_switch,
            require_preflight_for_autobuy=require_preflight_for_autobuy,
            preflight_min_usdc_buffer=preflight_min_usdc_buffer,
            updated_at=updated_at,
            is_fallback=False,
            validation_error=None
        )
    except Exception as e:
        logger.warning(f"Ошибка валидации penny_settings, используем безопасный fallback: {e}")
        # Безопасный fallback конфиг
        return PennyStocksConfig(
            wallet_address="",
            trading_mode="paper",
            live_trading_enabled=False,
            bet_size_usdc=1.0,
            max_bet_size_usdc=5.0,
            max_open_positions=10,
            daily_budget_usdc=20.0,
            min_probability=0.01,
            max_probability=0.09,
            min_confidence_score=0.5,
            min_volume_24h=50.0,
            min_hours_to_close=2.0,
            max_hours_to_close=168.0,
            auto_buy_enabled=False,
            kill_switch=True, # Включаем kill_switch по умолчанию для безопасности
            require_preflight_for_autobuy=True,
            preflight_min_usdc_buffer=5.0,
            updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            is_fallback=True,
            validation_error=str(e)
        )


def update_penny_stocks_config(updates: dict, changed_by: str = 'ui', source: str = 'ui') -> dict:
    """
    Обновляет настройки с аудитом.
    Проверяет ключи по whitelist.
    """
    ALLOWED_KEYS = set(PENNY_DEFAULTS.keys())
    updated_keys = []
    
    with get_connection() as conn:
        # Читаем старые значения для аудита
        rows = conn.execute("SELECT key, value FROM penny_settings").fetchall()
        old_raw = {r["key"]: r["value"] for r in rows}
        
        for k, v in updates.items():
            if k not in ALLOWED_KEYS:
                continue
                
            v_str = str(v).strip()
            old_val = old_raw.get(k, PENNY_DEFAULTS.get(k))
            
            if old_val != v_str:
                # Обновляем настройку
                conn.execute(
                    "INSERT OR REPLACE INTO penny_settings (key, value) VALUES (?, ?)",
                    (k, v_str)
                )
                # Пишем аудит
                conn.execute("""
                    INSERT INTO penny_settings_audit (changed_by, key, old_value, new_value, source)
                    VALUES (?, ?, ?, ?, ?)
                """, (changed_by, k, old_val, v_str, source))
                
                updated_keys.append(k)
        
        if updated_keys:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT OR REPLACE INTO penny_runtime_state (key, value) VALUES ('updated_at', ?)",
                (now_str,)
            )
            
    # Получаем новый конфиг
    new_cfg = get_penny_stocks_config()
    return {
        "updated_keys": updated_keys,
        "config": new_cfg
    }

def get_penny_runtime_state() -> dict[str, str]:
    """Читает оперативное состояние стратегии (последний preflight, деривация ключей, etc.)."""
    with get_connection() as conn:
        rows = conn.execute("SELECT key, value FROM penny_runtime_state").fetchall()
        return {r["key"]: r["value"] for r in rows}

