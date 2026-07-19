import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import time

from core.eval.signal_logger import SignalLogger
from agents.shared.adapters.polymarket import PolymarketAdapter
from services.favourite_compounder import calc_compound_pnl

def test_signal_logger_scout_performance():
    logger = SignalLogger()
    
    # Сценарий 1: Выигрыш по Scout-стратегии. 
    # Ставка = $10, цена покупки = 0.50 (YES).
    # gross_pnl = (10 / 0.50) * (1.0 - 0.50) = 10.0
    # net_pnl = 10.0 * 0.98 = 9.80
    is_win, pnl = logger._calculate_performance(
        strategy_type='scout',
        target_outcome='YES',
        market_price_at_signal=0.50,
        resolution_outcome='YES',
        metadata={}
    )
    assert is_win is True
    assert pnl == 9.80

    # Сценарий 2: Проигрыш по Scout-стратегии.
    # Должен возвращать чистый убыток в размере ставки (-$10).
    is_win, pnl = logger._calculate_performance(
        strategy_type='scout',
        target_outcome='YES',
        market_price_at_signal=0.50,
        resolution_outcome='NO',
        metadata={}
    )
    assert is_win is False
    assert pnl == -10.00


def test_signal_logger_favourite_compound_performance():
    logger = SignalLogger()
    
    # Сценарий: Выигрыш Favourite Compounding.
    # Замокаем get_compound_settings, чтобы возвращалась ставка 10.0
    with patch("agents.shared.python.db.get_compound_settings", return_value={"virtual_stake": 10.0}):
        is_win, pnl = logger._calculate_performance(
            strategy_type='favourite_compound',
            target_outcome='YES',
            market_price_at_signal=0.95,
            resolution_outcome='YES',
            metadata={}
        )
        assert is_win is True
        expected_pnl = calc_compound_pnl(10.0, 0.95, 1.0)
        assert pnl == expected_pnl


def test_polymarket_adapter_caching():
    adapter = PolymarketAdapter()
    
    # Подменяем сессию mock-объектом
    mock_response_compact = MagicMock()
    mock_response_compact.json.return_value = [
        {"id": "mkt-1", "question": "Test?", "outcomePrices": '["0.8", "0.2"]', "volume": 1000}
    ]
    mock_response_compact.status_code = 200
    
    mock_response_detail = MagicMock()
    mock_response_detail.json.return_value = {
        "id": "mkt-1",
        "question": "Test?",
        "description": "Desc",
        "outcomes": '["YES", "NO"]',
        "outcomePrices": '["0.8", "0.2"]',
        "closed": False,
        "endDate": "2026-12-31T23:59:59Z",
        "slug": "test-slug",
        "conditionId": "cond-1"
    }
    mock_response_detail.status_code = 200

    with patch.object(adapter.session, "get") as mock_get:
        mock_get.side_effect = [mock_response_compact, mock_response_detail]
        
        # 1. Проверяем кэш list_all_markets_compact
        res1 = adapter.list_all_markets_compact()
        assert len(res1) == 1
        assert res1[0]["id"] == "mkt-1"
        
        # Повторный вызов должен вернуть данные из кэша без вызова session.get
        res2 = adapter.list_all_markets_compact()
        assert len(res2) == 1
        assert res2[0]["id"] == "mkt-1"
        
        # 2. Проверяем кэш get_market
        m1 = adapter.get_market("mkt-1")
        assert m1 is not None
        assert m1.id == "mkt-1"
        
        # Повторный вызов get_market должен использовать кэш
        m2 = adapter.get_market("mkt-1")
        assert m2 is not None
        assert m2.id == "mkt-1"
        
        # Проверяем, что get вызывался ровно 2 раза (один раз для compact и один раз для detail)
        assert mock_get.call_count == 2
