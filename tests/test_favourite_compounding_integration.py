import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone, timedelta
import asyncio

from main import scheduled_favourite_compounding
from core.math_filter_metrics import get_stats

@pytest.mark.asyncio
async def test_scheduled_favourite_compounding_success():
    # Замокаем adapter.list_all_markets_compact и adapter.get_market
    mock_compact = [
        {"id": "mkt-1", "p": 0.96, "vol": 15000.0, "end": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(), "tags": []},
        {"id": "mkt-2", "p": 0.94, "vol": 15000.0, "end": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(), "tags": []}, # отфильтруется по цене
    ]
    
    mock_m1 = MagicMock()
    mock_m1.id = "mkt-1"
    mock_m1.price = 0.96
    mock_m1.volume = 15000.0
    mock_m1.title = "Test Market 1"
    mock_m1.url = "https://polymarket.com/test-1"
    mock_m1.close_time = datetime.now(timezone.utc) + timedelta(hours=24)
    mock_m1._orderbook = None

    with patch("core.singleton.get_core_engine") as mock_engine_getter, \
         patch("agents.shared.python.db.get_compound_settings", return_value={"min_price": 0.95, "min_volume": 500, "max_hours": 336, "virtual_stake": 50, "enabled": 1, "min_confidence": 0.35}), \
         patch("services.favourite_compounder.get_compound_settings", return_value={"min_price": 0.95, "min_volume": 500, "max_hours": 336, "virtual_stake": 50, "enabled": 1, "min_confidence": 0.35}), \
         patch("agents.shared.python.db.get_active_compound_opportunities", return_value=[]), \
         patch("services.favourite_compounder.calibrate_confidence_threshold", return_value=0.5), \
         patch("services.favourite_compounder.ObviousnessValidator.validate", return_value=(0.8, "Test reason")), \
         patch("agents.shared.python.db.upsert_compound_opportunity", return_value=True) as mock_upsert, \
         patch("services.notifications.send_compound_alert", new_callable=AsyncMock) as mock_send_alert, \
         patch("agents.shared.python.db.mark_compound_alerted") as mock_mark_alerted:

        engine = MagicMock()
        engine.adapter.list_all_markets_compact.return_value = mock_compact
        engine.adapter.get_market.side_effect = lambda m_id: mock_m1 if m_id == "mkt-1" else None
        mock_engine_getter.return_value = engine

        await scheduled_favourite_compounding()

        # Проверяем, что list_all_markets_compact и get_market вызвались корректно
        engine.adapter.list_all_markets_compact.assert_called_once()
        engine.adapter.get_market.assert_called_once_with("mkt-1")
        
        # Проверяем, что upsert был вызван для mkt-1 со всеми обязательными полями
        mock_upsert.assert_called_once()
        args = mock_upsert.call_args[0][0]
        assert args["market_id"] == "mkt-1"
        assert args["price"] == 0.96
        assert args["volume_usd"] == 15000.0
        assert args["confidence"] == 0.8
        assert args["obviousness_reason"] == "Test reason"
        assert args["roi_net_pct"] > 0
        assert abs(args["hours_left"] - 24.0) < 0.1

def test_get_stats_creates_table():
    with patch("agents.shared.python.db.get_connection") as mock_get_conn:
        mock_conn_instance = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn_instance
        mock_conn_instance.execute.return_value.fetchall.return_value = []
        
        stats = get_stats()
        
        assert stats == {"rows": []}
        # Должно быть как минимум 2 вызова execute (CREATE TABLE и SELECT)
        assert mock_conn_instance.execute.call_count >= 2
        calls = [call[0][0] for call in mock_conn_instance.execute.call_args_list]
        assert any("CREATE TABLE IF NOT EXISTS math_filter_log" in c for c in calls)
        assert any("SELECT" in c for c in calls)

@pytest.mark.asyncio
async def test_scheduled_favourite_compounding_no_outcome_success():
    # Симулируем рынок, где YES торгуется по 0.04 (NO по 0.96)
    mock_compact = [
        {"id": "mkt-no-1", "p": 0.04, "vol": 15000.0, "end": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(), "tags": []},
    ]
    
    mock_m1 = MagicMock()
    mock_m1.id = "mkt-no-1"
    mock_m1.price = 0.04
    mock_m1.volume = 15000.0
    mock_m1.title = "Test Market NO 1"
    mock_m1.url = "https://polymarket.com/test-no-1"
    mock_m1.close_time = datetime.now(timezone.utc) + timedelta(hours=24)
    mock_m1._orderbook = None

    with patch("core.singleton.get_core_engine") as mock_engine_getter, \
         patch("agents.shared.python.db.get_compound_settings", return_value={"min_price": 0.95, "min_volume": 500, "max_hours": 336, "virtual_stake": 50, "enabled": 1, "min_confidence": 0.35}), \
         patch("services.favourite_compounder.get_compound_settings", return_value={"min_price": 0.95, "min_volume": 500, "max_hours": 336, "virtual_stake": 50, "enabled": 1, "min_confidence": 0.35}), \
         patch("agents.shared.python.db.get_active_compound_opportunities", return_value=[]), \
         patch("services.favourite_compounder.calibrate_confidence_threshold", return_value=0.5), \
         patch("services.favourite_compounder.ObviousnessValidator.validate", return_value=(0.8, "Test reason")), \
         patch("agents.shared.python.db.upsert_compound_opportunity", return_value=True) as mock_upsert, \
         patch("services.notifications.send_compound_alert", new_callable=AsyncMock) as mock_send_alert, \
         patch("agents.shared.python.db.mark_compound_alerted") as mock_mark_alerted:

        engine = MagicMock()
        engine.adapter.list_all_markets_compact.return_value = mock_compact
        engine.adapter.get_market.side_effect = lambda m_id: mock_m1 if m_id == "mkt-no-1" else None
        mock_engine_getter.return_value = engine

        await scheduled_favourite_compounding()

        engine.adapter.list_all_markets_compact.assert_called_once()
        engine.adapter.get_market.assert_called_once_with("mkt-no-1")
        
        mock_upsert.assert_called_once()
        args = mock_upsert.call_args[0][0]
        assert args["market_id"] == "mkt-no-1"
        assert args["price"] == 0.96  # Цена фаворита NO = 1.0 - 0.04
        assert args["volume_usd"] == 15000.0
        assert args["outcome"] == "NO"
        assert args["confidence"] == 0.8

def test_mark_compound_bought_creates_signal():
    from agents.shared.python.db import init_db, get_connection, upsert_compound_opportunity, mark_compound_bought
    import uuid
    
    init_db()
    
    unique_suffix = uuid.uuid4().hex[:8]
    market_id = f"mkt_test_db_bought_{unique_suffix}"
    opp_id = f"{market_id}_2026-06-06"
    
    opp = {
        "id": opp_id,
        "market_id": market_id,
        "title": "Test Db Title",
        "url": "https://test.url",
        "price": 0.96,
        "volume_usd": 15000.0,
        "close_time": "2026-06-07 12:00:00",
        "hours_left": 24.0,
        "spread_pct": 0.005,
        "roi_net_pct": 2.5,
        "confidence": 0.8,
        "obviousness_reason": "Test reason",
        "outcome": "NO"
    }
    
    with get_connection() as conn:
        conn.execute("DELETE FROM compound_opportunities WHERE market_id = ?", (market_id,))
        conn.execute("DELETE FROM signals WHERE market_id = ?", (market_id,))
        
    inserted = upsert_compound_opportunity(opp)
    assert inserted is True
    
    mark_compound_bought(opp["id"])
    
    with get_connection() as conn:
        row = conn.execute("SELECT status, outcome FROM compound_opportunities WHERE id = ?", (opp["id"],)).fetchone()
        assert row is not None
        assert row["status"] == "BOUGHT"
        assert row["outcome"] == "NO"
        
        sig = conn.execute("SELECT * FROM signals WHERE market_id = ?", (market_id,)).fetchone()
        assert sig is not None
        assert sig["type"] == "FAVOURITE_COMPOUND"
        assert sig["strategy_type"] == "FAVOURITE_COMPOUND"
        assert sig["target_outcome"] == "NO"
        assert sig["estimated_probability"] == 0.8
        assert sig["market_price_at_signal"] == 0.96
        assert sig["status"] == "PENDING"


def test_save_and_get_compound_opportunity():
    from agents.shared.python.db import init_db, get_connection, save_compound_opportunity, get_compound_opportunities
    from services.favourite_compounder import FavouriteOpportunity
    import uuid

    init_db()

    unique_suffix = uuid.uuid4().hex[:8]
    market_id = f"mkt_test_save_get_{unique_suffix}"
    
    opp = FavouriteOpportunity(
        market_id=market_id,
        title="Test Opportunity Title",
        url="https://test.opportunity.url",
        price=0.96,
        volume_usd=12000.0,
        close_time=datetime.now(timezone.utc) + timedelta(hours=48),
        hours_left=48.0,
        spread_pct=0.005,
        roi_net_pct=3.5,
        confidence=0.8,
        obviousness_reason="Grounding confirmed",
        outcome="YES"
    )

    with get_connection() as conn:
        conn.execute("DELETE FROM compound_opportunities WHERE market_id LIKE 'mkt_test_save_get_%'")

    save_compound_opportunity(opp)

    active_opps = get_compound_opportunities(limit=100)
    matched = [o for o in active_opps if o["market_id"] == market_id]
    
    assert len(matched) == 1
    assert matched[0]["title"] == "Test Opportunity Title"
    assert matched[0]["price"] == 0.96
    assert matched[0]["volume_usd"] == 12000.0
    assert matched[0]["confidence"] == 0.8
    assert matched[0]["outcome"] == "YES"
    assert matched[0]["status"] == "NEW"

    # Save again should do nothing
    save_compound_opportunity(opp)
