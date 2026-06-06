import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram import types
from aiogram.types import CallbackQuery
from telegram.bot import (
    command_blacklist_handler,
    callback_block_tags_select,
    callback_block_tag_add,
    callback_unblock_tag
)

def create_mock_message():
    message = AsyncMock(spec=types.Message)
    message.answer = AsyncMock()
    message.reply = AsyncMock()
    message.delete = AsyncMock()
    return message

def create_mock_callback():
    callback = AsyncMock(spec=CallbackQuery)
    callback.answer = AsyncMock()
    callback.message = create_mock_message()
    callback.message.edit_text = AsyncMock()
    return callback


@pytest.mark.asyncio
@patch("telegram.bot.get_blacklist_tags", return_value=["nfl", "tennis"])
async def test_command_blacklist_handler(mock_get_tags):
    message = create_mock_message()
    await command_blacklist_handler(message)
    
    # Должен быть вызван метод answer для отправки ответа
    assert message.answer.called
    args, kwargs = message.answer.call_args
    text = args[0] if args else kwargs.get("text", "")
    
    # Проверяем, что теги вывелись в тексте
    assert "nfl" in text
    assert "tennis" in text
    
    # Проверяем, что передана клавиатура с кнопками удаления
    keyboard = kwargs.get("reply_markup") or (args[2] if len(args) > 2 else None)
    assert keyboard is not None
    # 2 кнопки тегов + 1 кнопка закрытия
    assert len(keyboard.inline_keyboard) == 3
    assert keyboard.inline_keyboard[0][0].text == "❌ nfl"
    assert keyboard.inline_keyboard[0][0].callback_data == "unblock_tag_nfl"


@pytest.mark.asyncio
@patch("agents.shared.adapters.polymarket.PolymarketAdapter.get_market_tags", return_value=["sports", "nfl"])
async def test_callback_block_tags_select(mock_get_tags):
    callback = create_mock_callback()
    callback.data = "block_tags_select_market123"
    
    await callback_block_tags_select(callback)
    
    # Проверяем, что бот ответил на callback
    assert callback.answer.called
    # И прислал сообщение со списком тегов
    assert callback.message.reply.called
    
    args, kwargs = callback.message.reply.call_args
    reply_text = args[0] if args else kwargs.get("text", "")
    assert "Выберите тег" in reply_text
    
    keyboard = kwargs.get("reply_markup") or (args[2] if len(args) > 2 else None)
    assert keyboard is not None
    # 2 тега + 1 кнопка отмены
    assert len(keyboard.inline_keyboard) == 3
    assert keyboard.inline_keyboard[0][0].text == '🚫 Блокировать "sports"'
    assert keyboard.inline_keyboard[0][0].callback_data == "block_tag_add_sports"


@pytest.mark.asyncio
@patch("telegram.bot.add_blacklist_tag")
async def test_callback_block_tag_add(mock_add_tag):
    callback = create_mock_callback()
    callback.data = "block_tag_add_tennis"
    
    await callback_block_tag_add(callback)
    
    # Должна вызваться функция добавления в БД
    mock_add_tag.assert_called_once_with("tennis")
    # Должен быть ответ на callback с уведомлением
    assert callback.answer.called
    # Проверяем наличие уведомления в одном из вызовов
    any_success_msg = any("добавлен в черный список" in str(call) for call in callback.answer.call_args_list)
    assert any_success_msg
    # Сообщение с выбором тегов должно удалиться
    callback.message.delete.assert_called_once()


@pytest.mark.asyncio
@patch("telegram.bot.remove_blacklist_tag")
@patch("telegram.bot.get_blacklist_tags", return_value=["tennis"])
async def test_callback_unblock_tag(mock_get_tags, mock_remove_tag):
    callback = create_mock_callback()
    callback.data = "unblock_tag_nfl"
    
    await callback_unblock_tag(callback)
    
    # Должна вызваться функция удаления из БД
    mock_remove_tag.assert_called_once_with("nfl")
    # Должен быть ответ на callback с уведомлением
    assert callback.answer.called
    # Проверяем, что в одном из вызовов было сообщение об удалении
    any_deleted_msg = any("удален из черного списка" in str(call) for call in callback.answer.call_args_list)
    assert any_deleted_msg
    
    # Должно обновиться сообщение черного списка
    assert callback.message.edit_text.called or callback.message.answer.called
