# tests/test_penny_settings_service_validation.py
import pytest
from agents.shared.python.penny_settings_service import save_penny_config, get_penny_stocks_config

def test_save_config_validates_against_current_db_not_defaults(isolated_db):
    """Валидация инвариантов должна использовать текущий конфиг БД, не хардкод-дефолты."""
    # Устанавливаем max_bet_size = 2.0 в БД
    save_penny_config({"max_bet_size_usdc": "2.0", "bet_size_usdc": "1.0", "daily_budget_usdc": "20.0"})
    
    # Теперь пытаемся установить bet_size = 3.0
    # При хардкод-дефолтах max_bet=5.0 → 3.0 <= 5.0 → ошибки не будет (баг!)
    # При правильном смерже с БД max_bet=2.0 → 3.0 > 2.0 → ValueError
    with pytest.raises(ValueError):
        save_penny_config({"bet_size_usdc": "3.0"})


def test_save_config_partial_update_does_not_break_invariant(isolated_db):
    """Частичное обновление одного поля должно корректно проверять инвариант."""
    save_penny_config({
        "bet_size_usdc": "1.0",
        "max_bet_size_usdc": "3.0",
        "daily_budget_usdc": "20.0"
    })
    # Обновляем только daily_budget до значения меньше max_bet
    with pytest.raises(ValueError):
        save_penny_config({"daily_budget_usdc": "2.0"})


def test_import_time_not_in_function_body():
    """import time не должен быть внутри тела can_execute_penny_trade."""
    import inspect
    import agents.shared.python.penny_execution_service as svc
    src = inspect.getsource(svc.can_execute_penny_trade)
    assert "import time" not in src, "import time должен быть на уровне модуля"


def test_save_config_validation_all_invariants_pass(isolated_db):
    """Корректный набор значений должен сохраняться без ошибок."""
    result = save_penny_config({
        "bet_size_usdc": "1.5",
        "max_bet_size_usdc": "3.0",
        "daily_budget_usdc": "15.0",
        "max_open_positions": "10",
        "min_probability": "0.02",
        "max_probability": "0.08",
        "min_confidence_score": "0.6",
        "min_hours_to_close": "2.0",
        "max_hours_to_close": "120.0",
    })
    assert result["ok"] is True
    assert result["config"]["bet_size_usdc"] == pytest.approx(1.5)
