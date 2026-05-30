"""Тест: get_core_engine() не создаёт два экземпляра при параллельных вызовах."""
import threading
import pytest

def test_get_core_engine_is_singleton(monkeypatch):
    """CoreEngine создаётся ровно один раз даже при concurrent вызовах."""
    import telegram.bot as bot_module
    bot_module._core_engine = None  # сброс синглтона

    from unittest.mock import MagicMock, patch
    instances = []

    class FakeCoreEngine:
        def __init__(self):
            instances.append(self)

    with patch("core.engine.CoreEngine", FakeCoreEngine):
        threads = [threading.Thread(target=bot_module.get_core_engine) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()

    # Без Lock могут создаться 2+ экземпляра
    # С Lock — ровно 1
    assert len(instances) == 1, f"Создано {len(instances)} экземпляров CoreEngine вместо 1"
