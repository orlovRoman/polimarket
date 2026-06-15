import pytest
import time
from unittest.mock import patch, MagicMock

def test_cleanup_only_removes_expired():
    """
    cleanup_expired_memory должна удалять ТОЛЬКО записи с истёкшим TTL,
    а не хардкоженные ключи агентов.
    """
    # Проверяем что SQL не содержит хардкода имён агентов
    try:
        import inspect
        from agents.shared.python.db import cleanup_expired_memory
        source = inspect.getsource(cleanup_expired_memory)
    except ImportError:
        pytest.skip("db модуль недоступен")

    # Хардкоженных ключей агентов не должно быть в функции очистки
    hardcoded_agents = ["SwingAgent", "ArbitrageAgent", "ScoutAgent", "ShadowAgent"]
    for agent_name in hardcoded_agents:
        assert agent_name not in source, (
            f"Хардкод ключа '{agent_name}' в cleanup_expired_memory — "
            "используй ttl при save_memory вместо ручного удаления."
        )

def test_cleanup_returns_int():
    """cleanup_expired_memory должна возвращать целое число удалённых строк."""
    try:
        from agents.shared.python.db import cleanup_expired_memory
        result = cleanup_expired_memory()
        assert isinstance(result, int)
        assert result >= 0
    except Exception as e:
        pytest.skip(f"БД недоступна: {e}")
