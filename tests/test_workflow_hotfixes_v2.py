import pytest, asyncio, uuid
from unittest.mock import patch, MagicMock, AsyncMock
from core.workflow import run_agent_evaluation, _fetch_markets_parallel, process_consensus, _prefilter_markets

@pytest.mark.asyncio
async def test_executor_shutdown_on_runtime_error():
    """При RuntimeError в submit executor должен быть закрыт."""
    market = MagicMock()
    market.id = f"fake_{uuid.uuid4()}"
    market.title = "Test Market"
    scout = MagicMock()
    scout.estimate_market = AsyncMock(return_value=None)
    scout.api_key = "test"
    swing = MagicMock()
    swing.estimate_market = AsyncMock(return_value=None)

    mock_executor = MagicMock()
    mock_executor.submit.side_effect = RuntimeError("cannot schedule new futures after interpreter shutdown")

    with patch("core.workflow.concurrent.futures.ThreadPoolExecutor", return_value=mock_executor):
        # mock build_search_query to not fail
        with patch("core.workflow.build_search_query", return_value="query"):
            with patch("config.llm_health_gate.check_availability", return_value=True):
                with patch("core.workflow._fetch_grounded_context", return_value="mocked context"):
                    with patch("core.workflow.MarketContext", return_value=MagicMock()):
                        with patch("core.workflow.IdeaDecision", return_value=MagicMock()):
                            await run_agent_evaluation(market, scout, swing, lambda **kw: None)

    assert mock_executor.shutdown.called, "executor.shutdown() не был вызван"

def test_fetch_markets_parallel_handles_partial_failures():
    """Частичные ошибки get_market не роняют весь скрининг."""
    call_count = 0
    def fake_get_market(mid):
        nonlocal call_count
        call_count += 1
        if mid == "bad_id":
            raise ConnectionError("timeout")
        return MagicMock(id=mid)

    adapter = MagicMock()
    adapter.get_market.side_effect = fake_get_market

    result = _fetch_markets_parallel(adapter, ["id1", "bad_id", "id2"])
    assert len(result) == 2  # bad_id пропущен
    assert call_count == 3

def test_fetch_markets_parallel_timeout():
    """При полном зависании возвращает пустой список через 30с."""
    import concurrent.futures
    adapter = MagicMock()
    with patch("core.workflow.concurrent.futures.as_completed") as mock_ac:
        mock_ac.side_effect = concurrent.futures.TimeoutError
        result = _fetch_markets_parallel(adapter, ["id1"], max_workers=1)
    assert result == []

def test_process_consensus_calls_callback_with_markup():
    calls = []
    def cb_with_markup(text, reply_markup):
        calls.append({"text": text, "markup": reply_markup})

    context = MagicMock()
    context.market.id = "1"
    context.math_filter_result = None
    signal = MagicMock(edge=0.6)
    opinion = MagicMock(agree=True, liquidity_risk="LOW", confidence=0.8,
                        orderbook_facts="bid=0.55", risk_assessment="ok",
                        shadow_verdict="ok", opinion="ok")

    with patch("core.workflow.IdeaDecision", return_value=MagicMock(status='saved')):
        with patch("agents.shared.python.db.save_agent_episode", create=True):
            with patch("core.workflow.save_idea_audit"):
                with patch("core.workflow.save_signal"):
                    process_consensus(context, signal, None, opinion, MagicMock(status='saved'), lambda **kw: None, cb_with_markup)
    assert calls[0]["markup"] is not None

def test_process_consensus_calls_callback_without_markup():
    calls = []
    def cb_no_markup(text):
        calls.append(text)

    context = MagicMock()
    context.market.id = "1"
    context.math_filter_result = None
    signal = MagicMock(edge=0.6)
    opinion = MagicMock(agree=True, confidence=0.8, liquidity_risk="LOW",
                        orderbook_facts="", risk_assessment="", shadow_verdict="ok", opinion="ok")

    with patch("core.workflow.IdeaDecision", return_value=MagicMock(status='saved')):
        with patch("agents.shared.python.db.save_agent_episode", create=True):
            with patch("core.workflow.save_idea_audit"):
                with patch("core.workflow.save_signal"):
                    process_consensus(context, signal, None, opinion, MagicMock(status='saved'), lambda **kw: None, cb_no_markup)
    assert len(calls) == 1

def test_prefilter_respects_volume_config(monkeypatch):
    monkeypatch.setattr("core.workflow.MIN_MARKET_VOLUME_USD", 1000)
    markets = [
        {"id": "a", "price": 0.5, "volume": 1500, "close_time": "2026-12-01T00:00:00Z"},
        {"id": "b", "price": 0.5, "volume": 800,  "close_time": "2026-12-01T00:00:00Z"},
    ]
    result = _prefilter_markets(markets)
    assert len(result) == 1
    assert result[0]["id"] == "a"
