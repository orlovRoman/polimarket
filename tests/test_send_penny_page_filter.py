"""
Тест для проверки фильтрации Penny Stocks (дешёвых опций), включая дешевые NO-позиции.
"""
import pytest
import asyncio
from unittest.mock import MagicMock, patch
from telegram.bot import send_penny_page

def test_penny_filter_no_outcome():
    # 1. Сигнал с ценой YES 0.97 - это дешевый NO (1.0 - 0.97 = 0.03 <= 0.05)
    # 2. Сигнал с ценой YES 0.03 - это дешевый YES (0.03 <= 0.05)
    # 3. Сигнал с ценой YES 0.50 - обычный рынок (не дешевый)
    signals = [
        {"market_price": 0.97, "title": "Cheap NO Option", "summary": "desc", "url": "url", "edge": 0.1, "confidence": 0.8, "target_outcome": "NO"},
        {"market_price": 0.03, "title": "Cheap YES Option", "summary": "desc", "url": "url", "edge": 0.1, "confidence": 0.8, "target_outcome": "YES"},
        {"market_price": 0.50, "title": "Regular Option", "summary": "desc", "url": "url", "edge": 0.1, "confidence": 0.8, "target_outcome": "YES"},
    ]
    
    filtered = [s for s in signals if s.get('market_price') is not None and min(s['market_price'], 1.0 - s['market_price']) <= 0.05]
    assert len(filtered) == 2
    assert filtered[0]["title"] == "Cheap NO Option"
    assert filtered[1]["title"] == "Cheap YES Option"

def test_send_penny_page_calls_send_or_edit():
    callback_query = MagicMock()
    
    async def async_noop(*args, **kwargs):
        return None
    callback_query.answer.side_effect = async_noop
    
    test_signals = [
        {"market_price": 0.97, "title": "Cheap NO Option", "summary": "desc", "url": "url", "edge": 0.1, "confidence": 0.8, "target_outcome": "NO", "id": "sig_1"},
        {"market_price": 0.50, "title": "Regular Option", "summary": "desc", "url": "url", "edge": 0.1, "confidence": 0.8, "target_outcome": "YES", "id": "sig_2"},
    ]
    
    with patch("telegram.bot.get_signals", return_value=test_signals):
        with patch("telegram.bot.send_or_edit") as mock_send:
            mock_send.side_effect = async_noop
            asyncio.run(send_penny_page(callback_query, page=0))
            
            mock_send.assert_called_once()
            response_text = mock_send.call_args[0][1]
            assert "Cheap NO Option" in response_text
            assert "Regular Option" not in response_text
