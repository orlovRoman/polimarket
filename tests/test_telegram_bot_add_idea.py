"""
Тесты для кнопки ручного добавления рынка в /ideas (/grill-me)
"""
import asyncio
from unittest.mock import MagicMock, patch
from telegram.bot import build_market_action_keyboard, callback_add_idea, callback_noop


def test_build_market_action_keyboard_includes_add_idea():
    """Проверяет, что клавиатура содержит кнопку '📥 В идеи'"""
    kb = build_market_action_keyboard("market_uuid_12345", "Test Title")
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    
    add_idea_btn = next((b for b in buttons if b.text == "📥 В идеи"), None)
    assert add_idea_btn is not None
    assert add_idea_btn.callback_data == "add_idea_market_uuid_12345"


def test_callback_add_idea_success():
    """Проверяет успешное добавление рынка в список идей при клике"""
    callback_query = MagicMock()
    callback_query.data = "add_idea_market_123"
    
    # Делаем асинхронные методы мока awaitable
    async def async_noop(*args, **kwargs):
        return None
    callback_query.answer.side_effect = async_noop
    callback_query.message.edit_reply_markup.side_effect = async_noop
    
    # Мокаем БД
    mock_market = {
        "id": "market_123",
        "title": "Will Bitcoin hit 100k?",
        "price": 0.5,
        "url": "https://polymarket.com/event/bitcoin-100k"
    }
    
    with patch("agents.shared.python.db.get_connection") as mock_conn:
        mock_cursor = mock_conn.return_value.__enter__.return_value.cursor.return_value
        
        # 1. Поиск рынка, 2. Поиск scout_edge в audit
        mock_cursor.fetchone.side_effect = [
            mock_market,       # 1. Поиск рынка
            {"scout_edge": 0.25} # 2. Поиск scout_edge в audit
        ]
        
        with patch("agents.shared.python.db.save_signal") as mock_save:
            # Имитируем успешное сохранение уникального сигнала
            mock_save.return_value = True
            
            asyncio.run(callback_add_idea(callback_query))
            
            # Проверяем, что сигнал был сохранен
            mock_save.assert_called_once()
            saved_signal = mock_save.call_args[0][0]
            assert saved_signal.market_id == "market_123"
            assert saved_signal.type == "MANUAL"
            assert saved_signal.edge == 0.25
            
            # Проверяем, что пользователю отправлено уведомление об успехе
            callback_query.answer.assert_called_once_with(
                "✅ Рынок успешно добавлен в список торговых идей /ideas!",
                show_alert=True
            )
            # Проверяем, что клавиатура на сообщении обновилась
            callback_query.message.edit_reply_markup.assert_called_once()


def test_callback_add_idea_already_exists():
    """Проверяет поведение, если рынок уже добавлен"""
    callback_query = MagicMock()
    callback_query.data = "add_idea_market_123"
    
    async def async_noop(*args, **kwargs):
        return None
    callback_query.answer.side_effect = async_noop
    
    mock_market = {
        "id": "market_123",
        "title": "Will Bitcoin hit 100k?",
        "price": 0.5,
        "url": "https://polymarket.com/event/bitcoin-100k"
    }
    
    with patch("agents.shared.python.db.get_connection") as mock_conn:
        mock_cursor = mock_conn.return_value.__enter__.return_value.cursor.return_value
        mock_cursor.fetchone.side_effect = [
            mock_market,  # 1. Поиск рынка
            {"scout_edge": 0.15} # 2. scout_edge из audit (не влияет на дубликат)
        ]
        
        with patch("agents.shared.python.db.save_signal") as mock_save:
            # Имитируем, что save_signal вернул False (сигнал уже существует, UNIQUE constraint сработал)
            mock_save.return_value = False
            
            asyncio.run(callback_add_idea(callback_query))
            
            # Проверяем, что сохранение вызывалось
            mock_save.assert_called_once()
            
            # Пользователю показан алерт о дубликате
            callback_query.answer.assert_called_once_with(
                "ℹ️ Этот рынок уже находится в списке торговых идей /ideas!",
                show_alert=True
            )


def test_callback_noop():
    """Проверяет заглушку noop"""
    callback_query = MagicMock()
    async def async_noop(*args, **kwargs):
        return None
    callback_query.answer.side_effect = async_noop
    
    asyncio.run(callback_noop(callback_query))
    callback_query.answer.assert_called_once()
