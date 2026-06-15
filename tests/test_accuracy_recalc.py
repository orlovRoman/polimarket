import pytest
from unittest.mock import patch, MagicMock

@pytest.mark.asyncio
async def test_accuracy_recalc_does_not_raise_on_empty_db():
    """
    Если в БД нет эпизодов — scheduled_agent_accuracy_recalc
    должна завершиться без исключений (не ронять scheduler).
    """
    empty_stats = {"total": 0, "correct": 0, "accuracy": 0.0}

    try:
        from main import scheduled_agent_accuracy_recalc
    except ImportError:
        pytest.skip("main.py недоступен")

    with patch(
        "agents.shared.python.db.get_agent_accuracy",
        return_value=empty_stats
    ), patch(
        "agents.shared.python.db.save_memory"
    ) as mock_save:
        await scheduled_agent_accuracy_recalc()
        # При total=0 save_memory не должен вызываться
        mock_save.assert_not_called()

@pytest.mark.asyncio
async def test_accuracy_recalc_saves_for_each_agent():
    """
    Если есть данные — save_memory вызывается для каждого агента
    с правильными ключами.
    """
    non_empty_stats = {"total": 10, "correct": 7, "accuracy": 0.7}

    try:
        from main import scheduled_agent_accuracy_recalc
    except ImportError:
        pytest.skip("main.py недоступен")

    saved_keys = []

    def mock_save(key, value, **kwargs):
        saved_keys.append(key)

    with patch("agents.shared.python.db.get_agent_accuracy", return_value=non_empty_stats), \
         patch("agents.shared.python.db.save_memory", side_effect=mock_save):
        await scheduled_agent_accuracy_recalc()

    # Каждый из 3 агентов → 2 ключа (accuracy_pct + evaluated_total) = 6 вызовов
    assert len(saved_keys) == 6, f"Ожидали 6 вызовов save_memory, получили {len(saved_keys)}"

    for agent in ["scout", "swing", "shadow"]:
        assert f"{agent}_accuracy_pct" in saved_keys
        assert f"{agent}_evaluated_total" in saved_keys
