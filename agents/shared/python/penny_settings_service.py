# agents/shared/python/penny_settings_service.py
"""
Бизнес-логика и сервисные функции для управления настройками Penny Stocks.
Разделяет веб-роуты от прямого доступа к базе данных.
"""
import logging
import os
from datetime import datetime
from dataclasses import asdict
from agents.shared.python.db import get_connection
from agents.shared.python.penny_settings_db import (
    get_penny_stocks_config,
    update_penny_stocks_config,
    PENNY_DEFAULTS,
    PennyStocksConfig
)
from agents.shared.python.wallet.factory import get_wallet_provider

logger = logging.getLogger("NexusPolyBot.PennySettingsService")

def load_penny_config() -> dict:
    """Загружает конфигурацию и метаданные режима работы."""
    try:
        import config
        app_mode = getattr(config, "APP_MODE", "paper")
    except ImportError:
        app_mode = os.getenv("APP_MODE", "paper")

    cfg = get_penny_stocks_config()
    provider = get_wallet_provider()
    
    # Провайдер может быть paper или live
    is_mock = not provider.is_live()
    provider_mode = "live" if provider.is_live() else "paper"

    from agents.shared.python.penny_settings_db import get_penny_runtime_state

    return {
        "ok": True,
        "config": asdict(cfg),
        "meta": {
            "app_mode": app_mode,
            "provider_mode": provider_mode,
            "is_mock": is_mock,
            "live_capable": (app_mode == "live")
        },
        "runtime": get_penny_runtime_state()
    }


def validate_auto_buy_transition(old_cfg: PennyStocksConfig, updates: dict, app_mode: str) -> list[str]:
    """Возвращает список ошибок. Пустой список = переход допустим."""
    errors = []
    
    # Проверяем, включается ли автопокупка
    new_auto_buy = updates.get("auto_buy_enabled")
    is_enabling_auto_buy = new_auto_buy is not None and (str(new_auto_buy).strip() == "1" or new_auto_buy is True)

    if is_enabling_auto_buy:
        new_kill = updates.get("kill_switch")
        if new_kill is None:
            kill_active = old_cfg.kill_switch
        else:
            kill_active = str(new_kill).strip() == "1" or new_kill is True

        if kill_active:
            errors.append("kill_switch активен — нельзя включить auto_buy")
            
        wallet = updates.get("wallet_address", old_cfg.wallet_address)
        if not wallet or not wallet.strip():
            errors.append("Включение автопокупки требует заполненного wallet_address")
            
        trading_mode = updates.get("trading_mode", old_cfg.trading_mode)
        if trading_mode == "live" and app_mode != "live":
            errors.append("APP_MODE=paper не позволяет торговать в live")
            
    return errors



def save_penny_config(updates: dict, changed_by: str = 'ui', source: str = 'ui') -> dict:
    """
    Выполняет бизнес-валидацию переходов параметров и сохраняет настройки.
    """
    try:
        import config
        app_mode = getattr(config, "APP_MODE", "paper")
    except ImportError:
        app_mode = os.getenv("APP_MODE", "paper")

    # 1. Читаем текущий конфиг
    current_cfg = get_penny_stocks_config()

    # Сливаем входящие изменения с текущим конфигом для проверки бизнес-валидации
    merged_updates = {}
    for k in PENNY_FIELD_NAMES:
        if k in updates:
            merged_updates[k] = updates[k]
        else:
            # Превращаем bool обратно в 1/0 для совместимости с БД
            val = getattr(current_cfg, k)
            if isinstance(val, bool):
                merged_updates[k] = "1" if val else "0"
            else:
                merged_updates[k] = str(val)

    # 2. Бизнес-валидация
    # Запрет включения live режима торговли или live-исполнения, если бэкенд в paper режиме
    if app_mode != "live":
        if merged_updates.get("trading_mode") == "live":
            raise ValueError("Нельзя включить trading_mode=live, пока бэкенд запущен в режиме paper (APP_MODE=paper)")
        if merged_updates.get("live_trading_enabled") == "1":
            raise ValueError("Нельзя включить live_trading_enabled=1, пока бэкенд запущен в режиме paper (APP_MODE=paper)")

    # Валидация перехода автопокупки
    transition_errors = validate_auto_buy_transition(current_cfg, updates, app_mode)
    if transition_errors:
        raise ValueError("; ".join(transition_errors))

    # Запрет включения auto-buy, если адрес кошелька пуст
    wallet = merged_updates.get("wallet_address", "").strip()
    if merged_updates.get("auto_buy_enabled") == "1" and not wallet:
        raise ValueError("Включение автопокупки (auto_buy_enabled=1) требует заполненного wallet_address")

    # 3. Сохраняем в БД
    result = update_penny_stocks_config(updates, changed_by=changed_by, source=source)
    
    # Сбрасываем синглтон-провайдер кошелька, чтобы настройки live_trading_enabled применились немедленно
    from agents.shared.python.wallet.factory import reset_wallet_provider
    reset_wallet_provider()
    
    return {
        "ok": True,
        "updated_keys": result["updated_keys"],
        "config": asdict(result["config"])

    }

def run_penny_preflight() -> dict:
    """
    Выполняет комплексную диагностику готовности стратегии к торговле.
    Записывает результаты в penny_runtime_state.
    """
    cfg = get_penny_stocks_config()
    provider = get_wallet_provider()
    
    checks = []
    errors = []
    warnings = []
    
    # 1. Проверка кошелька
    wallet_present = bool(cfg.wallet_address)
    checks.append({
        "name": "wallet_address_present",
        "status": "pass" if wallet_present else "fail",
        "message": "wallet_address заполнен" if wallet_present else "wallet_address отсутствует"
    })
    if not wallet_present:
        errors.append("wallet_address не заполнен в настройках.")

    # 2. Проверка соответствия режимов
    try:
        import config
        app_mode = getattr(config, "APP_MODE", "paper")
    except ImportError:
        app_mode = os.getenv("APP_MODE", "paper")

    mode_match = True
    if cfg.trading_mode == "live" and app_mode != "live":
        mode_match = False
        errors.append("Стратегия настроена в LIVE, но бэкенд работает в PAPER (APP_MODE=paper).")
        
    checks.append({
        "name": "app_mode_live",
        "status": "pass" if mode_match else "fail",
        "message": f"Совместимость режимов: APP_MODE={app_mode}, trading_mode={cfg.trading_mode}"
    })

    # Проверка готовности live-провайдера
    if provider.is_live():
        try:
            # Вызов приватного метода для проверки возможности создания клиента
            if hasattr(provider, "_get_client"):
                provider._get_client()
        except NotImplementedError as nie:
            checks.append({
                "name": "live_provider_ready",
                "status": "fail",
                "message": f"LivePolymarketProvider не готов: {nie}"
            })
            errors.append(f"LivePolymarketProvider не готов к работе: {nie}")
            
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO penny_runtime_state (key, value) VALUES ('last_preflight_at', ?)", (now_str,))
                conn.execute("INSERT OR REPLACE INTO penny_runtime_state (key, value) VALUES ('last_preflight_ok', '0')", ())
                conn.execute("INSERT OR REPLACE INTO penny_runtime_state (key, value) VALUES ('last_preflight_summary', 'Preflight failed')", ())
            
            return {
                "ok": False,
                "summary": "Preflight failed",
                "provider_mode": "live",
                "is_mock": False,
                "checks": checks,
                "warnings": warnings,
                "errors": errors
            }
        except Exception as e:
            checks.append({
                "name": "live_provider_ready",
                "status": "fail",
                "message": f"Ошибка инициализации Live-клиента: {e}"
            })
            errors.append(f"Ошибка инициализации Live-клиента: {e}")

    # 3. Проверка баланса и allowance
    balance_info = None

    try:
        balance_info = provider.preflight_check()
        balance_ok = True
        
        # Минимальный буфер
        min_required = cfg.daily_budget_usdc + cfg.preflight_min_usdc_buffer
        if balance_info.usdc_balance < min_required:
            balance_ok = False
            errors.append(
                f"Недостаточно баланса: {balance_info.usdc_balance:.2f} USDC "
                f"< требуемого минимума {min_required:.2f} USDC (бюджет + буфер)"
            )
            
        checks.append({
            "name": "balance_budget",
            "status": "pass" if balance_ok else "fail",
            "details": {
                "balance": balance_info.usdc_balance,
                "budget": cfg.daily_budget_usdc,
                "buffer": cfg.preflight_min_usdc_buffer
            },
            "message": f"Баланс: {balance_info.usdc_balance:.2f} USDC (требуется {min_required:.2f} USDC)"
        })
        
        allowance_ok = balance_info.allowance_ok
        checks.append({
            "name": "allowance",
            "status": "pass" if allowance_ok else "fail",
            "mock": balance_info.is_mock,
            "message": "Approvals для Polymarket CLOB в порядке" if allowance_ok else "Отсутствуют approvals для Polymarket CLOB"
        })
        if not allowance_ok:
            errors.append("Отсутствуют approvals на смарт-контракт Polymarket CLOB.")
            
    except Exception as e:
        checks.append({
            "name": "balance_budget",
            "status": "fail",
            "message": f"Не удалось проверить баланс: {e}"
        })
        errors.append(f"Ошибка проверки баланса через CLOB API: {e}")

    # 4. Проверка API ключей
    try:
        creds = provider.get_credentials()
        checks.append({
            "name": "credentials_status",
            "status": "pass",
            "mock": creds.is_mock,
            "message": "Креды API сгенерированы успешно"
        })
    except Exception as e:
        checks.append({
            "name": "credentials_status",
            "status": "fail",
            "message": f"Ошибка ключей API: {e}"
        })
        errors.append(f"Ошибка генерации API ключей: {e}")

    # Добавляем предупреждение при paper-режиме
    if not provider.is_live():
        warnings.append("Используется симуляция (Paper Provider). Реальные средства не проверялись.")

    ok = len(errors) == 0
    summary = "Preflight passed" if ok else "Preflight failed"

    # Записываем результат в runtime state
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO penny_runtime_state (key, value) VALUES ('last_preflight_at', ?)", (now_str,))
        conn.execute("INSERT OR REPLACE INTO penny_runtime_state (key, value) VALUES ('last_preflight_ok', ?)", ("1" if ok else "0",))
        conn.execute("INSERT OR REPLACE INTO penny_runtime_state (key, value) VALUES ('last_preflight_summary', ?)", (summary,))

    return {
        "ok": ok,
        "summary": summary,
        "provider_mode": "live" if provider.is_live() else "paper",
        "is_mock": not provider.is_live(),
        "checks": checks,
        "warnings": warnings,
        "errors": errors
    }

def rederive_penny_credentials() -> dict:
    """Выполняет перевыпуск/деривацию API ключей."""
    provider = get_wallet_provider()
    try:
        creds = provider.get_credentials()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO penny_runtime_state (key, value) VALUES ('last_credentials_derived_at', ?)", (now_str,))
        
        return {
            "ok": True,
            "is_mock": creds.is_mock,
            "provider_mode": creds.provider_mode,
            "derived_at": creds.derived_at.strftime("%Y-%m-%d %H:%M:%S"),
            "message": "Simulated in paper mode" if creds.is_mock else "API credentials successfully re-derived"
        }
    except Exception as e:
        logger.error(f"Failed to re-derive credentials: {e}")
        return {
            "ok": False,
            "error": str(e)
        }

PENNY_FIELD_NAMES: list[str] = [
    "wallet_address", "trading_mode", "live_trading_enabled", "bet_size_usdc",
    "max_bet_size_usdc", "max_open_positions", "daily_budget_usdc",
    "min_probability", "max_probability", "min_confidence_score",
    "min_volume_24h", "min_hours_to_close", "max_hours_to_close",
    "auto_buy_enabled", "kill_switch", "require_preflight_for_autobuy",
    "preflight_min_usdc_buffer"
]

def reset_penny_config_to_defaults() -> dict:
    """Сбрасывает конфигурацию Penny Stocks к дефолтным значениям."""
    return save_penny_config(PENNY_DEFAULTS, changed_by='system', source='reset')
