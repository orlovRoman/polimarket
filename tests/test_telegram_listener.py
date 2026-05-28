import pytest
from services.telegram_listener import is_bot_message

def test_is_bot_message_ignores_bot_responses():
    """Проверяет, что фильтр корректно отлавливает сообщения самого бота"""
    
    # Сообщение с найденными рынками
    msg1 = "Найдено 3 связанных рынков:\n1. New Rihanna Album before GTA VI? (YES: 54¢ | NO: 46¢)"
    assert is_bot_message(msg1) is True
    
    # Сообщение о том, что рынки не найдены
    msg2 = "К сожалению, я не нашел связанных рынков на Polymarket для этого поста."
    assert is_bot_message(msg2) is True
    
    # Триггер кита
    msg3 = "🚀 ТРИГГЕР (Whale): Запущен внеочередной скан для рынка"
    assert is_bot_message(msg3) is True

def test_is_bot_message_allows_real_news():
    """Проверяет, что фильтр пропускает реальные новости"""
    
    # Обычная новость
    msg1 = "СРОЧНО: Илон Маск запускает SpaceX на Марс"
    assert is_bot_message(msg1) is False
    
    # Новость с похожими, но не системными словами
    msg2 = "К сожалению, Трамп не приехал. Найдено много улик."
    assert is_bot_message(msg2) is False
