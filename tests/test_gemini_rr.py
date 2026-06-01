# tests/test_gemini_rr.py
import pytest
from unittest.mock import patch, MagicMock

def test_gemini_rr_rotates_model_on_each_call():
    """Каждый успешный вызов должен двигать gem_rr_index вперёд."""
    memory = {"gem_rr_index": 0}

    def fake_get_memory(key, default=None):
        return memory.get(key, default)
    def fake_save_memory(key, val):
        memory[key] = val

    mock_send = MagicMock(return_value=({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}, 10, 5))
    patched_config = {
        "gemini": {
            "keys": ["fake_key"],
            "models": ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"],
            "send_func": mock_send
        },
        "openrouter": {"keys": [], "models": [], "send_func": lambda *args: None},
        "cerebras": {"keys": [], "models": [], "send_func": lambda *args: None}
    }

    with patch("agents.shared.python.db.get_memory", fake_get_memory), \
         patch("agents.shared.python.db.save_memory", fake_save_memory), \
         patch("agents.shared.utils.gemini_client.PROVIDERS_CONFIG", patched_config):

        from agents.shared.utils.gemini_client import generate_content_with_fallback
        payload = {"contents": [{"role": "user", "parts": [{"text": "test"}]}]}

        generate_content_with_fallback("fake_key", payload, agent_name="TEST")
        assert memory["gem_rr_index"] == 1  # сдвинулся на 1

        generate_content_with_fallback("fake_key", payload, agent_name="TEST")
        assert memory["gem_rr_index"] == 2  # сдвинулся на 2


def test_gemini_rr_wraps_around():
    """При достижении конца списка индекс должен обнуляться."""
    memory = {"gem_rr_index": 2}  # последний индекс для 3 моделей

    def fake_get_memory(key, default=None):
        return memory.get(key, default)
    def fake_save_memory(key, val):
        memory[key] = val

    mock_send = MagicMock(return_value=({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}, 10, 5))
    patched_config = {
        "gemini": {
            "keys": ["fake_key"],
            "models": ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"],
            "send_func": mock_send
        },
        "openrouter": {"keys": [], "models": [], "send_func": lambda *args: None},
        "cerebras": {"keys": [], "models": [], "send_func": lambda *args: None}
    }

    with patch("agents.shared.python.db.get_memory", fake_get_memory), \
         patch("agents.shared.python.db.save_memory", fake_save_memory), \
         patch("agents.shared.utils.gemini_client.PROVIDERS_CONFIG", patched_config):

        from agents.shared.utils.gemini_client import generate_content_with_fallback
        payload = {"contents": [{"role": "user", "parts": [{"text": "test"}]}]}

        generate_content_with_fallback("fake_key", payload, agent_name="TEST")
        assert memory["gem_rr_index"] == 0  # обнулился


def test_gemini_keys_rr_rotates_keys_on_each_call():
    """Каждый успешный вызов Gemini должен двигать gem_key_rr_index вперёд."""
    memory = {"gem_key_rr_index": 0}

    def fake_get_memory(key, default=None):
        return memory.get(key, default)
    def fake_save_memory(key, val):
        memory[key] = val

    keys_called = []
    def mock_send(payload, model, key, timeout):
        keys_called.append(key)
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}, 10, 5

    patched_config = {
        "gemini": {
            "keys": [],  # Должно быть пустым, чтобы срабатывал fallback на env/dynamic keys
            "models": ["gemini-2.5-flash"],
            "send_func": mock_send
        },
        "openrouter": {"keys": [], "models": [], "send_func": lambda *args: None},
        "cerebras": {"keys": [], "models": [], "send_func": lambda *args: None}
    }

    def mock_getenv(name, default=""):
        mapping = {
            "GOOGLE_API_KEY": "key_pri",
            "GOOGLE_API_KEY_SECONDARY": "key_sec",
            "GOOGLE_API_KEY_THIRD": "key_third"
        }
        return mapping.get(name, default)

    with patch("agents.shared.python.db.get_memory", fake_get_memory), \
         patch("agents.shared.python.db.save_memory", fake_save_memory), \
         patch("agents.shared.utils.gemini_client.PROVIDERS_CONFIG", patched_config), \
         patch("os.getenv", side_effect=mock_getenv):

        from agents.shared.utils.gemini_client import generate_content_with_fallback
        payload = {"contents": [{"role": "user", "parts": [{"text": "test"}]}]}

        # Первый вызов: gem_key_rr_index = 0. Ожидаем ключи: ["key_pri", "key_sec", "key_third"]
        generate_content_with_fallback("key_pri", payload, agent_name="TEST")
        assert keys_called[-1] == "key_pri"
        assert memory["gem_key_rr_index"] == 1

        # Второй вызов: gem_key_rr_index = 1. Ожидаем ключи: ["key_sec", "key_third", "key_pri"]
        generate_content_with_fallback("key_pri", payload, agent_name="TEST")
        assert keys_called[-1] == "key_sec"
        assert memory["gem_key_rr_index"] == 2

        # Третий вызов: gem_key_rr_index = 2. Ожидаем ключи: ["key_third", "key_pri", "key_sec"]
        generate_content_with_fallback("key_pri", payload, agent_name="TEST")
        assert keys_called[-1] == "key_third"
        assert memory["gem_key_rr_index"] == 0
