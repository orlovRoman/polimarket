import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ── Тест #1: Favourite compound lock блокирует сканирование ─────────────────
@pytest.mark.asyncio
async def test_scan_blocked_while_compound_running():
    """
    Если _favourite_compound_lock занят — callback_scan_handler должен
    вернуть предупреждение, а не запустить второй скан параллельно.
    """
    from telegram.bot import _favourite_compound_lock, callback_scan_handler

    callback = AsyncMock()
    callback.data = "scan_politics"
    callback.message = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()

    # Имитируем занятый compound lock
    async with _favourite_compound_lock:
        await callback_scan_handler(callback)

    # Должен был ответить предупреждением, а не запустить скан
    callback.message.answer.assert_called_once()
    call_text = callback.message.answer.call_args[0][0]
    assert "compound" in call_text.lower() or "compounding" in call_text.lower(), \
        f"Неверный текст предупреждения: {call_text}"


# ── Тест #2: cmd_compound существует и callable ──────────────────────────────
def test_cmd_compound_is_defined():
    """cmd_compound должна быть определена в bot.py — иначе reply_compound упадёт."""
    import telegram.bot as bot_module
    assert hasattr(bot_module, "cmd_compound"), \
        "cmd_compound не определена в telegram/bot.py — кнопка '💰 Compound' сломана!"
    assert callable(bot_module.cmd_compound)


# ── Тест #3: reply_compound вызывает cmd_compound ────────────────────────────
@pytest.mark.asyncio
async def test_reply_compound_calls_cmd_compound():
    """Нажатие кнопки '💰 Compound' должно вызвать cmd_compound."""
    from telegram.bot import reply_compound
    message = AsyncMock()

    with patch("telegram.bot.cmd_compound", new_callable=AsyncMock) as mock_cmd:
        await reply_compound(message)
        mock_cmd.assert_called_once_with(message)


# ── Тест #4: AuthMiddleware не пропускает unauthorized ────────────────────────
@pytest.mark.asyncio
async def test_auth_middleware_blocks_unauthorized():
    """ignore_mkt_ кнопка НЕ должна обходить авторизацию."""
    from telegram.bot import AuthMiddleware
    middleware = AuthMiddleware()

    event = AsyncMock()
    event.from_user = MagicMock(id=99999999)  # чужой ID
    event.chat = MagicMock(id=99999999)
    event.data = "ignore_mkt_0xabc123"
    event.message = AsyncMock()

    handler = AsyncMock(return_value="should_not_reach")
    data = {}

    with patch("telegram.bot.AUTHORIZED_CHAT_ID", "12345678"):
        result = await middleware(handler, event, data)

    # handler НЕ должен быть вызван
    handler.assert_not_called()
    assert result is None
