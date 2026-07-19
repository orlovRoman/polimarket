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
    
    # 1. Первая страница компактных рынков (100 элементов)
    mock_response_compact_page1 = MagicMock()
    mock_response_compact_page1.json.return_value = [
        {"id": f"mkt-{i}", "question": f"Test {i}?", "outcomePrices": '["0.8", "0.2"]', "volume": 1000}
        for i in range(100)
    ]
    mock_response_compact_page1.status_code = 200
    
    # 2. Вторая страница компактных рынков (пустой список)
    mock_response_compact_page2 = MagicMock()
    mock_response_compact_page2.json.return_value = []
    mock_response_compact_page2.status_code = 200
    
    # 3. Детали рынка
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
        mock_get.side_effect = [mock_response_compact_page1, mock_response_compact_page2, mock_response_detail]
        
        # 1. Проверяем кэш list_all_markets_compact (делает 2 запроса из-за пагинации)
        res1 = adapter.list_all_markets_compact()
        assert len(res1) == 100
        assert res1[0]["id"] == "mkt-0"
        
        # Повторный вызов должен вернуть данные из кэша без вызова session.get
        res2 = adapter.list_all_markets_compact()
        assert len(res2) == 100
        
        # 2. Проверяем кэш get_market (делает 1 запрос)
        m1 = adapter.get_market("mkt-1")
        assert m1 is not None
        assert m1.id == "mkt-1"
        
        # Повторный вызов get_market должен использовать кэш
        m2 = adapter.get_market("mkt-1")
        assert m2 is not None
        assert m2.id == "mkt-1"
        
        # Проверяем, что get вызывался ровно 3 раза (2 для compact и 1 для detail)
        assert mock_get.call_count == 3


def test_polymarket_adapter_cache_eviction():
    adapter = PolymarketAdapter()
    
    # Заполним кэш деталей рынков до лимита
    adapter._DETAIL_CACHE_MAX_SIZE = 5
    adapter._DETAIL_CACHE_TTL = 0.05  # маленький TTL
    
    m_mock = MagicMock()
    m_mock.id = "test"
    
    # Запишем 5 элементов, которые искусственно устарели (время на 1 секунду в прошлом)
    for i in range(5):
        adapter._market_detail_cache[f"m-{i}"] = (m_mock, time.time() - 1.0)
        
    # Добавление 6-го элемента должно триггерить очистку
    mock_response_detail = MagicMock()
    mock_response_detail.json.return_value = {
        "id": "mkt-new",
        "question": "Test New?",
        "outcomes": '["YES", "NO"]',
        "outcomePrices": '["0.8", "0.2"]',
        "closed": False,
        "slug": "new-slug",
        "conditionId": "cond-new"
    }
    mock_response_detail.status_code = 200
    
    with patch.object(adapter.session, "get", return_value=mock_response_detail):
        adapter.get_market("mkt-new")
        
    # Кэш должен очиститься от старых элементов, так как их TTL истек
    assert len(adapter._market_detail_cache) == 1
    assert "mkt-new" in adapter._market_detail_cache


def test_outcome_tracker_resolved_manual(isolated_db):
    from services.outcome_tracker import run_resolution_cycle
    from agents.shared.python.db import get_connection
    
    with get_connection() as conn:
        # Сначала вставим рынок для удовлетворения внешнего ключа
        conn.execute("""
            INSERT INTO markets (id, platform, title, url, outcome, price, close_time)
            VALUES ('mkt-manual-1', 'polymarket', 'Manual Title', 'http://url', 'unknown', 0.95, datetime('now', '-20 minutes'))
        """)
        # Добавим тестовую ручную позицию со статусом BOUGHT
        conn.execute("""
            INSERT INTO compound_opportunities (
                id, market_id, title, url, price, volume_usd, close_time, hours_left, confidence, outcome, status, virtual_bought_price, virtual_bought_at
            ) VALUES (
                'opp-manual-1', 'mkt-manual-1', 'Manual Title', 'http://url', 0.95, 10000.0,
                datetime('now', '-20 minutes'), 1.0, 0.9, 'YES', 'BOUGHT', 0.95, datetime('now', '-25 minutes')
            )
        """)
        
    # Мокаем получение резолюции оракулом
    with patch("services.outcome_tracker._fetch_resolution", return_value="YES"), \
         patch("agents.shared.python.db.get_compound_settings", return_value={"virtual_stake": 50.0}):
         
        run_resolution_cycle()
        
    # Проверим, что ручная сделка закрылась и перешла в RESOLVED с PnL = 0.0 для авто-части
    with get_connection() as conn:
        opp = conn.execute("SELECT status, pnl_usd FROM compound_opportunities WHERE id = 'opp-manual-1'").fetchone()
        assert opp['status'] == 'RESOLVED'
        assert opp['pnl_usd'] == 0.0
        
        # Также проверим, что ручная сделка записалась в историю с реальным PnL
        trade = conn.execute("SELECT sold_price, pnl_usd FROM compound_virtual_trades_history WHERE market_id = 'mkt-manual-1'").fetchone()
        assert trade is not None
        assert trade['sold_price'] == 1.0
        # stake = 50.0, entry = 0.95, exit = 1.0, contracts = 50 / 0.95 = 52.6315
        # gross = 52.6315 * 0.05 = 2.6315
        # net = 2.6315 * 0.98 = 2.58
        assert trade['pnl_usd'] == 2.58
