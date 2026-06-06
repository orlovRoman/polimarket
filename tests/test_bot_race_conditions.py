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
    from aiogram.types import CallbackQuery
    middleware = AuthMiddleware()

    event = AsyncMock(spec=CallbackQuery)
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


# ── Тест #5: Кнопки действий на рынках обходят stale-check ────────────────────
@pytest.mark.asyncio
async def test_stale_bypass_for_market_actions():
    """Кнопки ignore_mkt_, watch_mkt_, add_idea_ и compound_buy не должны выдавать 'Сессия устарела'."""
    from telegram.bot import AuthMiddleware
    from datetime import datetime, timedelta, timezone
    from aiogram.types import CallbackQuery
    middleware = AuthMiddleware()

    # Сообщение создано 1 час назад (устаревшее)
    old_date = datetime.now(timezone.utc) - timedelta(hours=1)
    
    for action in ("ignore_mkt_123", "analyze_mkt_123", "add_idea_123", "compound_buy:123"):
        event = AsyncMock(spec=CallbackQuery)
        event.from_user = MagicMock(id=12345678)  # авторизованный
        event.chat = MagicMock(id=12345678)
        event.data = action
        event.message = MagicMock()
        event.message.date = old_date

        handler = AsyncMock(return_value="success")
        data = {}

        with patch("telegram.bot.AUTHORIZED_CHAT_ID", "12345678"):
            result = await middleware(handler, event, data)

        # Обработчик должен быть вызван успешно (stale-check пропущен)
        handler.assert_called_once()
        assert result == "success"


# ── Тест #6: get_scout_accuracy_live возвращает корректный тип ───────────────
def test_scout_accuracy_returns_correct_types():
    """get_scout_accuracy_live должна вернуть (float|None, int)."""
    from telegram.bot import get_scout_accuracy_live
    from unittest.mock import patch, MagicMock

    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, key: {"win_rate": 75.0, "resolved": 10}[key]

    with patch("agents.shared.python.db.get_connection") as mock_conn:
        mock_conn.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = mock_row
        acc, cnt = get_scout_accuracy_live()
        assert isinstance(acc, float), f"Ожидался float, получен {type(acc)}"
        assert isinstance(cnt, int)


# ── Тест #7: Stale-check блокирует обычные кнопки старше 10 минут ──────────
@pytest.mark.asyncio
async def test_stale_check_blocks_normal_old_callback():
    """Обычный callback (не market action) старше 10 минут — должен быть заблокирован."""
    from telegram.bot import AuthMiddleware
    from datetime import datetime, timedelta, timezone
    from aiogram import types

    middleware = AuthMiddleware()
    old_date = datetime.now(timezone.utc) - timedelta(minutes=15)

    event = AsyncMock(spec=types.CallbackQuery)
    event.from_user = MagicMock(id=12345678)
    event.chat = MagicMock(id=12345678)
    event.data = "monitor_refresh"  # НЕ market action
    event.message = MagicMock(spec=types.Message)
    event.message.date = old_date
    event.answer = AsyncMock()

    handler = AsyncMock()

    with patch("telegram.bot.AUTHORIZED_CHAT_ID", "12345678"):
        result = await middleware(handler, event, data={})

    handler.assert_not_called()
    event.answer.assert_called_once()


# ── Тест #8: Optional импортирован (регрессионный) ──────────────────────────
def test_optional_imported_in_bot():
    """Проверяет, что Optional из typing доступен в bot.py (нет NameError)."""
    import inspect
    import telegram.bot as bot_module
    src = inspect.getsource(bot_module)
    assert "Optional" not in src or "from typing import" in src, \
        "Optional используется, но не импортирован!"
