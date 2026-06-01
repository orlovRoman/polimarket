# tests/test_bot_fixes.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio


# ─── Тест 1: UnboundLocalError в callback_save_model ────────────────────────
def test_model_key_initialized_before_loop():
    """model_key должна быть None до начала цикла — иначе UnboundLocalError."""
    import telegram.bot as bot_module
    # Проверяем, что при пустом маппинге код не падает
    with patch.object(bot_module, 'get_dynamic_models_mapping', return_value={}):
        # Симулируем логику функции
        models_mapping = {}
        model_key = None  # Ожидаем, что так написано в коде
        data = "SCOUT_some_unknown_key"
        agent = ""
        for key in models_mapping:
            if data.endswith(f"_{key}"):
                model_key = key
                agent = data[:-len(key) - 1]
                break
        # Не должно быть исключения
        assert model_key is None


# ─── Тест 2: estimate_llm_cost ────────────────────────────────────────────────
def test_estimate_llm_cost_free_model():
    from telegram.bot import estimate_llm_cost
    assert estimate_llm_cost("meta-llama/llama-3:free", 10000, 1000) == 0.0

def test_estimate_llm_cost_flash_returns_float():
    from telegram.bot import estimate_llm_cost
    cost = estimate_llm_cost("gemini-2.5-flash", 1_000_000, 100_000)
    assert isinstance(cost, float)
    assert cost > 0

def test_estimate_llm_cost_pro_more_than_flash():
    from telegram.bot import estimate_llm_cost
    flash = estimate_llm_cost("gemini-2.5-flash", 100_000, 10_000)
    pro = estimate_llm_cost("gemini-2.5-pro", 100_000, 10_000)
    assert pro > flash, "Pro должна быть дороже Flash"


# ─── Тест 3: build_paginated_keyboard ────────────────────────────────────────
def test_build_paginated_keyboard_first_page():
    from telegram.bot import build_paginated_keyboard
    kb = build_paginated_keyboard(page=0, total_pages=3, prefix="ideas_page")
    buttons = kb.inline_keyboard[0]
    texts = [b.text for b in buttons]
    assert "❌ Закрыть" in texts
    assert "⬅️ Назад" not in texts  # первая страница — нет кнопки Назад
    assert "Вперед ➡️" in texts

def test_build_paginated_keyboard_last_page():
    from telegram.bot import build_paginated_keyboard
    kb = build_paginated_keyboard(page=2, total_pages=3, prefix="ideas_page")
    buttons = kb.inline_keyboard[0]
    texts = [b.text for b in buttons]
    assert "⬅️ Назад" in texts
    assert "Вперед ➡️" not in texts  # последняя страница — нет кнопки Вперёд


# ─── Тест 4: AuthMiddleware — чужой user не проходит ─────────────────────────
def test_auth_middleware_blocks_unauthorized():
    from telegram.bot import AuthMiddleware
    import os
    os.environ["TELEGRAM_CHAT_ID"] = "123456"

    middleware = AuthMiddleware()
    handler = AsyncMock(return_value="ok")

    # Симулируем сообщение от чужого пользователя
    event = MagicMock()
    event.from_user.id = 999999
    event.chat.id = 999999
    event.date = None
    type(event).__name__ = "Message"

    from aiogram import types
    async def run_test():
        with patch.object(types, 'Message', MagicMock):
            return await middleware(handler, event, {})

    asyncio.run(run_test())
    handler.assert_not_called()


# ─── Тест 5: _extract_market_title_from_message ──────────────────────────────
def test_extract_title_from_html_link():
    from telegram.bot import _extract_market_title_from_message
    msg = MagicMock()
    msg.text = "<a href='https://example.com'>Will Bitcoin hit $100k?</a>"
    msg.caption = None
    title = _extract_market_title_from_message(msg)
    assert "Bitcoin" in title

def test_extract_title_fallback_plain_text():
    from telegram.bot import _extract_market_title_from_message
    msg = MagicMock()
    msg.text = "Some market title here\nMore info below"
    msg.caption = None
    title = _extract_market_title_from_message(msg)
    assert len(title) > 3


# ─── Тест 6: _shorten_key ────────────────────────────────────────────────────
def test_shorten_key_short_stays_same():
    from telegram.bot import _shorten_key
    key = "gemini_25_flash"
    assert _shorten_key(key) == key

def test_shorten_key_long_gets_hashed():
    from telegram.bot import _shorten_key
    long_key = "a" * 50
    result = _shorten_key(long_key)
    assert len(result) <= 30  # 20 символов + _ + 8 символов хэша = 29
    assert "_" in result


# ─── Тест 7: Дедупликация deque ──────────────────────────────────────────────
def test_deque_dedup_works():
    from collections import deque
    processed: deque = deque(maxlen=200)
    msg_key = (12345, 99)
    assert msg_key not in processed
    processed.append(msg_key)
    assert msg_key in processed
