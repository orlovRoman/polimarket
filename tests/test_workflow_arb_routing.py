import asyncio
from unittest.mock import AsyncMock
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
from core.math_filter import MathFilterResult, FilterDecision
from core.models import Market
from core.arb_router import route_ambiguous

def _mkt(id, title, price, platform="polymarket"):
    return Market(id=id, platform=platform, title=title,
                  description="", url=f"http://x/{id}", outcome="YES",
                  price=price, close_time=datetime.now(timezone.utc) + timedelta(days=14))

@pytest.fixture(autouse=True)
def clean_llm_cache():
    try:
        from agents.shared.python.db import get_connection
        with get_connection() as conn:
            conn.execute("DELETE FROM arb_llm_cache")
    except Exception:
        pass
    yield
    try:
        from agents.shared.python.db import get_connection
        with get_connection() as conn:
            conn.execute("DELETE FROM arb_llm_cache")
    except Exception:
        pass


# ────────────────────────────────────────────────────────────────────────
# Модульные тесты для route_ambiguous
# ────────────────────────────────────────────────────────────────────────

def test_route_ambiguous_low_spread_calls_llm():
    """Даже если спред ниже 8%, route_ambiguous вызывает LLM, так как лимита спреда больше нет."""
    mf = MathFilterResult(
        decision=FilterDecision.AMBIGUOUS,
        arbitrage_type="price_divergence",
        spread_pct=5.0, reasoning="", trade_instruction=""
    )
    mock_payload = (MagicMock(), None)
    with patch("core.arb_router.generate_content_with_fallback", return_value=mock_payload) as mock_gen, \
         patch("core.arb_router.extract_response_text", return_value='{"same_event": true, "confidence": 0.9, "reason": "ok", "confirmed_arb": true}'):
        res = route_ambiguous(mf, _mkt("low_a", "Title A", 0.5), _mkt("low_b", "Title B", 0.45), api_key="test")
        assert res is not None
        assert res["same_event"] is True
        mock_gen.assert_called_once()

def test_route_ambiguous_non_ambiguous_skips_llm():
    """Если решение не AMBIGUOUS, route_ambiguous возвращает None и не делает запросов."""
    mf = MathFilterResult(
        decision=FilterDecision.CONFIRMED_ARBITRAGE,
        arbitrage_type="price_divergence",
        spread_pct=15.0, reasoning="", trade_instruction=""
    )
    with patch("core.arb_router.generate_content_with_fallback") as mock_gen:
        res = route_ambiguous(mf, _mkt("non_a", "Title A", 0.5), _mkt("non_b", "Title B", 0.45), api_key="test")
        assert res is None
        mock_gen.assert_not_called()

def test_route_ambiguous_high_spread_calls_llm_and_parses_json():
    """Если спред >= 8%, route_ambiguous вызывает LLM и парсит JSON-ответ."""
    mf = MathFilterResult(
        decision=FilterDecision.AMBIGUOUS,
        arbitrage_type="price_divergence",
        spread_pct=10.0, reasoning="", trade_instruction=""
    )
    mock_payload = (MagicMock(), None)
    with patch("core.arb_router.generate_content_with_fallback", return_value=mock_payload) as mock_gen, \
         patch("core.arb_router.extract_response_text", return_value='{"same_event": true, "confidence": 0.9, "reason": "ok", "confirmed_arb": true}'):
        res = route_ambiguous(mf, _mkt("high_a", "Title A", 0.5), _mkt("high_b", "Title B", 0.40), api_key="test")
        assert res == {
            "same_event": True,
            "confidence": 0.9,
            "reason": "ok",
            "confirmed_arb": True
        }
        mock_gen.assert_called_once()

# ────────────────────────────────────────────────────────────────────────
# Интеграционные тесты для run_agent_evaluation
# ────────────────────────────────────────────────────────────────────────

def test_run_agent_evaluation_checks_top_5_and_routes_ambiguous():
    """Проверяем, что run_agent_evaluation:
    1. Берет до 5 корреляций из get_market_correlations (по константе _MAX_CORR_PEERS).
    2. Вызывает get_market для получения peer рынков.
    3. Вызывает math_pre_filter для каждого peer.
    4. Для всех AMBIGUOUS (включая спред < 8%) вызывает route_ambiguous.
    5. Для CONFIRMED_NO_ARBI НЕ вызывает route_ambiguous.
    """
    from core.workflow import run_agent_evaluation
    
    m = _mkt("main", "Bitcoin above 100K", 0.50)
    scout = MagicMock()
    scout.api_key = "test_key"
    scout.estimate_market = AsyncMock(return_value=None)
    swing = MagicMock()
    swing.estimate_market = AsyncMock(return_value=None)
    update_state = MagicMock()
    
    corr_list = [
        {"market_id_a": "main", "market_id_b": "peer1", "title_a": "main", "title_b": "peer1", "correlation_type": "thematic", "confidence": 0.9, "description": "test"},
        {"market_id_a": "main", "market_id_b": "peer2", "title_a": "main", "title_b": "peer2", "correlation_type": "thematic", "confidence": 0.8, "description": "test"},
        {"market_id_a": "main", "market_id_b": "peer3", "title_a": "main", "title_b": "peer3", "correlation_type": "thematic", "confidence": 0.7, "description": "test"},
        {"market_id_a": "main", "market_id_b": "peer4", "title_a": "main", "title_b": "peer4", "correlation_type": "thematic", "confidence": 0.6, "description": "test"},
        {"market_id_a": "main", "market_id_b": "peer5", "title_a": "main", "title_b": "peer5", "correlation_type": "thematic", "confidence": 0.5, "description": "test"},
        {"market_id_a": "main", "market_id_b": "peer6", "title_a": "main", "title_b": "peer6", "correlation_type": "thematic", "confidence": 0.4, "description": "test"},
    ]
    
    peer_markets = {
        "peer1": _mkt("peer1", "Market peer1", 0.40),
        "peer2": _mkt("peer2", "Market peer2", 0.48),
        "peer3": _mkt("peer3", "Market peer3", 0.50),
        "peer4": _mkt("peer4", "Market peer4", 0.50),
        "peer5": _mkt("peer5", "Market peer5", 0.50),
        "peer6": _mkt("peer6", "Market peer6", 0.50),
    }
    
    adapter = MagicMock()
    adapter.get_market.side_effect = lambda pid: peer_markets.get(pid)
    
    # 1. peer1: AMBIGUOUS, спред 15%
    # 2. peer2: AMBIGUOUS, спред 4%
    # 3. peer3: CONFIRMED_NO_ARBI
    # peer4, peer5: CONFIRMED_NO_ARBI
    # peer6 не должен проверяться, так как мы берем только top-5
    mf_results = {
        "peer1": MathFilterResult(FilterDecision.AMBIGUOUS, "price_divergence", 15.0, "", ""),
        "peer2": MathFilterResult(FilterDecision.AMBIGUOUS, "price_divergence", 4.0, "", ""),
        "peer3": MathFilterResult(FilterDecision.CONFIRMED_NO_ARBI, "price_divergence", 0.0, "", ""),
        "peer4": MathFilterResult(FilterDecision.CONFIRMED_NO_ARBI, "price_divergence", 0.0, "", ""),
        "peer5": MathFilterResult(FilterDecision.CONFIRMED_NO_ARBI, "price_divergence", 0.0, "", ""),
        "peer6": MathFilterResult(FilterDecision.CONFIRMED_NO_ARBI, "price_divergence", 0.0, "", ""),
    }
    
    def fake_math_pre_filter(m_a, m_b):
        return mf_results[m_b.id]
        
    with patch("core.workflow.get_market_correlations", return_value=corr_list), \
         patch("core.workflow.get_memory", return_value=None), \
         patch("core.workflow.save_memory"), \
         patch("core.workflow.fetch_rss_news", return_value=[]), \
         patch("core.workflow.fetch_reddit_news", return_value=[]), \
         patch("agents.shared.utils.web_search.fetch_wikipedia_context", return_value=[]), \
         patch("core.workflow.fetch_hackernews", return_value=[]), \
         patch("core.workflow.fetch_google_trends", return_value=""), \
         patch("core.workflow._fetch_grounded_context", return_value=""), \
         patch("core.math_filter.math_pre_filter", side_effect=fake_math_pre_filter), \
         patch("core.arb_router.route_ambiguous", return_value={"confirmed_arb": True, "same_event": True, "reason": "ok", "confidence": 0.9}) as mock_route, \
         patch("core.checkpoint.save_checkpoint"):
        
        sig, swing_sig, ctx = asyncio.run(run_agent_evaluation(m, scout, swing, update_state, adapter=adapter))
        
        # Проверяем, что get_market вызвался для peer1 - peer5
        assert adapter.get_market.call_count == 5
        adapter.get_market.assert_any_call("peer1")
        adapter.get_market.assert_any_call("peer2")
        adapter.get_market.assert_any_call("peer3")
        adapter.get_market.assert_any_call("peer4")
        adapter.get_market.assert_any_call("peer5")
        with pytest.raises(AssertionError):
            adapter.get_market.assert_any_call("peer6")
            
        # Проверяем, что route_ambiguous вызвался 2 раза (для peer1 и peer2, т.к. оба AMBIGUOUS)
        assert mock_route.call_count == 2
