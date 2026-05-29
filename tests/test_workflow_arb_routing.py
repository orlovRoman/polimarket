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

# ────────────────────────────────────────────────────────────────────────
# Модульные тесты для route_ambiguous
# ────────────────────────────────────────────────────────────────────────

def test_route_ambiguous_low_spread_skips_llm():
    """Если спред ниже 8%, route_ambiguous возвращает None и не делает сетевых запросов."""
    mf = MathFilterResult(
        decision=FilterDecision.AMBIGUOUS,
        arbitrage_type="price_divergence",
        spread_pct=5.0, reasoning="", trade_instruction=""
    )
    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback") as mock_gen:
        res = route_ambiguous(mf, _mkt("a", "Title A", 0.5), _mkt("b", "Title B", 0.45), api_key="test")
        assert res is None
        mock_gen.assert_not_called()

def test_route_ambiguous_non_ambiguous_skips_llm():
    """Если решение не AMBIGUOUS, route_ambiguous возвращает None и не делает запросов."""
    mf = MathFilterResult(
        decision=FilterDecision.CONFIRMED_ARBITRAGE,
        arbitrage_type="price_divergence",
        spread_pct=15.0, reasoning="", trade_instruction=""
    )
    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback") as mock_gen:
        res = route_ambiguous(mf, _mkt("a", "Title A", 0.5), _mkt("b", "Title B", 0.45), api_key="test")
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
    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback", return_value=mock_payload) as mock_gen, \
         patch("agents.shared.utils.gemini_client.extract_response_text", return_value='{"same_event": true, "confidence": 0.9, "reason": "ok", "confirmed_arb": true}'):
        res = route_ambiguous(mf, _mkt("a", "Title A", 0.5), _mkt("b", "Title B", 0.40), api_key="test")
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

def test_run_agent_evaluation_checks_top_3_and_routes_ambiguous():
    """Проверяем, что run_agent_evaluation:
    1. Берет до 3 корреляций из get_market_correlations.
    2. Вызывает get_market для получения peer рынков.
    3. Вызывает math_pre_filter для каждого peer.
    4. Для AMBIGUOUS со спредом >= 8% вызывает route_ambiguous.
    5. Для AMBIGUOUS со спредом < 8% НЕ вызывает route_ambiguous.
    6. Для CONFIRMED_NO_ARBI НЕ вызывает route_ambiguous.
    """
    from core.workflow import run_agent_evaluation
    
    m = _mkt("main", "Bitcoin above 100K", 0.50)
    scout = MagicMock()
    scout.api_key = "test_key"
    scout.estimate_market.return_value = None
    swing = MagicMock()
    swing.estimate_market.return_value = None
    update_state = MagicMock()
    
    corr_list = [
        {"market_id_a": "main", "market_id_b": "peer1", "title_a": "main", "title_b": "peer1", "correlation_type": "thematic", "confidence": 0.9, "description": "test"},
        {"market_id_a": "main", "market_id_b": "peer2", "title_a": "main", "title_b": "peer2", "correlation_type": "thematic", "confidence": 0.8, "description": "test"},
        {"market_id_a": "main", "market_id_b": "peer3", "title_a": "main", "title_b": "peer3", "correlation_type": "thematic", "confidence": 0.7, "description": "test"},
        {"market_id_a": "main", "market_id_b": "peer4", "title_a": "main", "title_b": "peer4", "correlation_type": "thematic", "confidence": 0.6, "description": "test"},
    ]
    
    peer_markets = {
        "peer1": _mkt("peer1", "Market peer1", 0.40),
        "peer2": _mkt("peer2", "Market peer2", 0.48),
        "peer3": _mkt("peer3", "Market peer3", 0.50),
        "peer4": _mkt("peer4", "Market peer4", 0.50),
    }
    
    adapter = MagicMock()
    adapter.get_market.side_effect = lambda pid: peer_markets.get(pid)
    
    # 1. peer1: AMBIGUOUS, спред 15% (>= 8%)
    # 2. peer2: AMBIGUOUS, спред 4% (< 8%)
    # 3. peer3: CONFIRMED_NO_ARBI
    # peer4 не должен проверяться, так как мы берем только top-3
    mf_results = {
        "peer1": MathFilterResult(FilterDecision.AMBIGUOUS, "price_divergence", 15.0, "", ""),
        "peer2": MathFilterResult(FilterDecision.AMBIGUOUS, "price_divergence", 4.0, "", ""),
        "peer3": MathFilterResult(FilterDecision.CONFIRMED_NO_ARBI, "price_divergence", 0.0, "", ""),
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
        
        sig, swing_sig, ctx = run_agent_evaluation(m, scout, swing, update_state, adapter=adapter)
        
        # Проверяем, что get_market вызвался только для peer1, peer2, peer3
        assert adapter.get_market.call_count == 3
        adapter.get_market.assert_any_call("peer1")
        adapter.get_market.assert_any_call("peer2")
        adapter.get_market.assert_any_call("peer3")
        with pytest.raises(AssertionError):
            adapter.get_market.assert_any_call("peer4")
            
        # Проверяем, что route_ambiguous вызвался ровно 1 раз (для peer1, т.к. там спред 15% >= 8%)
        mock_route.assert_called_once()
        # Проверяем аргументы вызова route_ambiguous
        call_args = mock_route.call_args[0]
        assert call_args[0].spread_pct == 15.0
        assert call_args[1].id == "main"
        assert call_args[2].id == "peer1"
        assert mock_route.call_args[1]["api_key"] == "test_key"
