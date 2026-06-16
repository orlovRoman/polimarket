import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

# Modules under test
from agents.polymarket_swing_agent.src.agent import SwingAgent
from agents.polymarket_arbitrage_agent.src.agent import ArbitrageAgent
from core.context import MarketContext
from core.models import Market
import services.telegram_listener as tl
from agents.shared.python.db import add_penny_stock_to_monitoring, add_whale_stock_to_monitoring, resolve_compound_opportunity
from agents.polymarket_arbitrage_agent.src.synthetic.event_loader import load_events_with_levels_from_raw

import sqlite3

@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE markets (id TEXT, platform TEXT, title TEXT, url TEXT, outcome TEXT, price REAL, close_time TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE compound_opportunities (id TEXT, status TEXT, price REAL, market_id TEXT, outcome TEXT, pnl_usd REAL, actual_outcome TEXT, exit_price REAL, resolved_at TEXT)")
    conn.execute("CREATE TABLE penny_stocks_monitoring (market_id TEXT PRIMARY KEY, title TEXT, url TEXT, initial_price REAL, current_price REAL, last_price REAL, close_time TEXT, confidence REAL, status TEXT, max_price_seen REAL, min_price_seen REAL, predicted_outcome TEXT, edge REAL)")
    conn.execute("CREATE TABLE whale_stocks_monitoring (market_id TEXT PRIMARY KEY, title TEXT, url TEXT, initial_price REAL, current_price REAL, last_price REAL, close_time TEXT, status TEXT, wallet_address TEXT, max_price_seen REAL, min_price_seen REAL, predicted_outcome TEXT, edge REAL, confidence REAL)")
    yield conn
    conn.close()

# ----------------- Test Helpers ----------------- #
def make_market(title="Test Market", price=0.5, close_time=None, closed=False, end_date_iso=None):
    if close_time is None:
        close_time = datetime(2026, 12, 31, tzinfo=timezone.utc)
    return Market(
        id="m1",
        platform="polymarket",
        title=title,
        url="https://polymarket.com/test",
        outcome="YES",
        price=price,
        close_time=close_time,
    )

def _make_simple_market(id="m1", closed=False, end_date_iso=None, close_time=None):
    return SimpleNamespace(
        id=id,
        closed=closed,
        end_date_iso=end_date_iso,
        endDate=None,
        end=None,
        close_time=close_time,
        title="Test market",
        price=0.5
    )

# ----------------- Classes ----------------- #

class TestDatetimeInPrompts:
    """Агенты должны включать текущую дату в каждый промпт."""
    
    def test_swing_agent_prompt_has_date(self):
        agent = SwingAgent(api_key="fake-key")
        market = make_market()
        context = MarketContext(market=market, news_titles=[], reddit_posts=[], wiki_context=[], trends_data="", hn_posts=[])
        
        mock_result = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
        with patch("agents.polymarket_swing_agent.src.agent.get_performance_summary", return_value=""), \
             patch("agents.polymarket_swing_agent.src.agent.get_agent_episodes", return_value=[]), \
             patch("agents.shared.utils.gemini_client.generate_content_with_fallback", return_value=(mock_result, "model")) as mock_gen, \
             patch("agents.shared.python.llm_wrapper.with_retry") as mock_retry:
             
            asyncio.run(agent.estimate_market(context))
            
            assert mock_gen.called
            called_payload = mock_gen.call_args[1]["payload"]
            prompt_text = called_payload["contents"][0]["parts"][0]["text"]
            assert "Сегодняшняя дата и время:" in prompt_text

    def test_arbitrage_agent_correlation_prompt_has_date(self):
        agent = ArbitrageAgent(api_key="fake-key")
        market_a = make_market()
        market_b = make_market()
        
        mock_result = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
        with patch.object(agent, '_call_llm', return_value=(mock_result, "raw")) as mock_llm:
            agent.analyze_correlation(market_a, market_b, "threshold", 80)
            assert mock_llm.called
            prompt_text = mock_llm.call_args[0][0]
            assert "Сегодняшняя дата и время:" in prompt_text

    def test_arbitrage_agent_cross_platform_prompt_has_date(self):
        agent = ArbitrageAgent(api_key="fake-key")
        market_a = make_market()
        market_b = make_market()
        
        mock_result = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
        with patch.object(agent, '_call_llm', return_value=(mock_result, "raw")) as mock_llm:
            agent.analyze_cross_platform(market_a, market_b, 0.95)
            assert mock_llm.called
            prompt_text = mock_llm.call_args[0][0]
            assert "Сегодняшняя дата и время:" in prompt_text


class TestMarketActiveFiltering:
    """_is_market_active должен корректно фильтровать по close_time."""
    
    def test_future_close_time_is_active(self):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        m = _make_simple_market(end_date_iso=future)
        with patch("services.telegram_listener.PolymarketAdapter") as MockAdapter:
            adapter_instance = MockAdapter.return_value
            adapter_instance.get_event_by_slug.return_value = [m]
            result = asyncio.run(tl.resolve_market_ids_from_url("https://polymarket.com/event/test", "Test"))
            assert result == ["m1"]

    def test_past_close_time_is_inactive(self):
        past = "2020-01-01T00:00:00Z"
        m = _make_simple_market(end_date_iso=past)
        with patch("services.telegram_listener.PolymarketAdapter") as MockAdapter:
            adapter_instance = MockAdapter.return_value
            adapter_instance.get_event_by_slug.return_value = [m]
            result = asyncio.run(tl.resolve_market_ids_from_url("https://polymarket.com/event/test", "Test"))
            assert result == []

    def test_naive_datetime_does_not_raise_typeerror(self):
        naive = "2020-06-15T12:00:00"  # без timezone
        m = _make_simple_market(end_date_iso=naive)
        with patch("services.telegram_listener.PolymarketAdapter") as MockAdapter:
            adapter_instance = MockAdapter.return_value
            adapter_instance.get_event_by_slug.return_value = [m]
            result = asyncio.run(tl.resolve_market_ids_from_url("https://polymarket.com/event/test", "Test"))
            assert result == []

    def test_closed_flag_overrides_date(self):
        m = _make_simple_market(closed=True)
        with patch("services.telegram_listener.PolymarketAdapter") as MockAdapter:
            adapter_instance = MockAdapter.return_value
            adapter_instance.get_event_by_slug.return_value = [m]
            result = asyncio.run(tl.resolve_market_ids_from_url("https://polymarket.com/event/test", "Test"))
            assert result == []


class TestCloseTimeTimezone:
    """close_time должен всегда быть aware UTC datetime строкой."""
    
    def test_penny_stock_close_time_is_future_utc(self, db):
        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            add_penny_stock_to_monitoring("test-penny", "Title", "http", 0.5)
        row = db.execute("SELECT close_time FROM markets WHERE id='test-penny'").fetchone()
        assert row is not None
        ct = datetime.fromisoformat(row["close_time"]).replace(tzinfo=timezone.utc)
        assert ct > datetime.now(timezone.utc)

    def test_whale_stock_close_time_is_future_utc(self, db):
        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            add_whale_stock_to_monitoring("test-whale", "Title", "http", 0.5)
        row = db.execute("SELECT close_time FROM markets WHERE id='test-whale'").fetchone()
        assert row is not None
        ct = datetime.fromisoformat(row["close_time"]).replace(tzinfo=timezone.utc)
        assert ct > datetime.now(timezone.utc)


class TestResolveCompoundOpportunity:
    """pnl_usd=None не должен тихо записываться в БД."""
    
    def test_pnl_none_raises_or_uses_default(self, db):
        db.execute("INSERT INTO compound_opportunities (id, status, price, market_id, outcome) VALUES (?, ?, ?, ?, ?)",
                   ("opp1", "PENDING", 0.5, "m1", "YES"))
        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            resolve_compound_opportunity("opp1", "YES", pnl_usd=None)
        
        row = db.execute("SELECT pnl_usd FROM compound_opportunities WHERE id='opp1'").fetchone()
        assert row is not None
        assert row["pnl_usd"] == 0.0


class TestLoadEventsNewSignature:
    """load_events_with_levels_from_raw возвращает (events, stats)."""
    
    def test_returns_tuple_not_list(self):
        raw = [{
            "slug": "btc-price",
            "title": "BTC Price",
            "markets": [{"id": "1", "question": "Q", "outcomePrices": '["0.8", "0.2"]', "volume": "5000"}]
        }]
        res = load_events_with_levels_from_raw(raw, min_markets=1, min_volume_per_market=1000)
        assert isinstance(res, tuple)
        assert len(res) == 2
        events, stats = res
        assert isinstance(events, list)
        assert isinstance(stats, dict)

    def test_stats_contains_expected_keys(self):
        raw = [{
            "slug": "btc-price",
            "title": "BTC Price",
            "markets": [{"id": "1", "question": "Q", "outcomePrices": '["0.8", "0.2"]', "volume": "5000"}]
        }]
        _, stats = load_events_with_levels_from_raw(raw, min_markets=1, min_volume_per_market=1000)
        assert "total" in stats
