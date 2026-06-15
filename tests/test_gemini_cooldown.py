import pytest
import asyncio
import time
from unittest.mock import patch

from agents.shared.utils.gemini_client import (
    _is_on_cooldown,
    _set_provider_cooldown,
    _provider_cooldowns,
    _cooldown_lock,
)


def test_cooldown_set_and_check():
    """Базовый случай: выставить cooldown и проверить."""
    provider = "test_provider_cooldown"
    _provider_cooldowns.pop(provider, None)  # чистим

    assert not _is_on_cooldown(provider)
    _set_provider_cooldown(provider, seconds=10.0)
    assert _is_on_cooldown(provider)


def test_cooldown_expired():
    """После истечения cooldown провайдер снова доступен."""
    import time
    provider = "test_provider_expired"
    _provider_cooldowns[provider] = time.monotonic() - 1  # уже истёк
    assert not _is_on_cooldown(provider)


def test_cooldown_not_reduced():
    """Повторный вызов _set_provider_cooldown не уменьшает дедлайн."""
    import time
    provider = "test_provider_no_reduce"
    _provider_cooldowns.pop(provider, None)

    _set_provider_cooldown(provider, seconds=60.0)
    deadline_before = _provider_cooldowns[provider]

    _set_provider_cooldown(provider, seconds=5.0)  # меньше текущего
    deadline_after = _provider_cooldowns[provider]

    assert deadline_after == deadline_before, (
        "Дедлайн не должен уменьшаться при повторном вызове с меньшим значением"
    )


def test_concurrent_cooldown_no_race():
    """
    Параллельный доступ из нескольких потоков не вызывает KeyError / гонку.
    """
    import threading
    provider = "test_concurrent"
    _provider_cooldowns.pop(provider, None)
    errors = []

    def worker():
        try:
            _set_provider_cooldown(provider, seconds=30.0)
            _ = _is_on_cooldown(provider)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert not errors, f"Race condition ошибки: {errors}"
