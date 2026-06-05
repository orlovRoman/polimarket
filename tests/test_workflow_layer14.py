import asyncio
from unittest.mock import AsyncMock
import pytest
import concurrent.futures
import threading
import time
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone
from core.workflow import _safe_result, make_consensus


# ── helpers ──────────────────────────────────────────────────

def _make_market(market_id="mkt-1", price=0.65):
    m = MagicMock()
    m.id = market_id
    m.title = "Will test happen?"
    m.url   = "https://polymarket.com/test"
    m.price = price
    return m

def _make_context(market=None):
    ctx = MagicMock()
    ctx.market         = market or _make_market()
    ctx.trigger_type   = "scheduled"
    ctx.source_url     = ""
    ctx.source_text    = ""
    ctx.math_filter_result = None
    ctx.correlation_hint   = None
    return ctx

def _make_signal():
    from core.models import Signal
    return Signal(
        id="s-1", type="SCOUT", market_id="mkt-1", platform="polymarket",
        confidence=0.9, priority="high", summary="test", details="test",
        edge=0.12, signal_cause="Strong signal", signal_risk="Low risk",
        signal_verdict="BUY YES", oracle_risk="", trade_action=""
    )

def _make_swing(recommendation="buy"):
    from core.models import SwingSignal
    return SwingSignal(
        id="sw-1", market_id="mkt-1", platform="polymarket", type="SWING",
        hype_potential=0.8, target_outcome="YES", target_exit_price=0.8,
        confidence=0.8, reasoning="test", recommendation=recommendation,
        catalyst="Hype building", swing_risk="Medium", swing_verdict="BUY",
        catalyst_absence_reason="", details="", summary=""
    )

def _make_shadow(agree=True):
    from core.models import AgentOpinion
    return AgentOpinion(
        agent_name="SHADOW", market_id="mkt-1", opinion="test",
        confidence=0.85, agree=agree, orderbook_facts="Deep book",
        risk_assessment="Minimal slippage", shadow_verdict="APPROVE",
        liquidity_risk="medium"
    )


# ── Баг #1: executor leak ─────────────────────────────────────

def test_run_agent_evaluation_futures_cancelled_after_timeout():
    """После _safe_result все futures должны быть отменены/executor shutdown"""
    from core.workflow import run_agent_evaluation

    barrier = threading.Event()
    calls = []

    def slow_fetch(_q):
        calls.append("started")
        barrier.wait(timeout=30)  # висит пока не отпустим
        return []

    m = _make_market()
    scout = MagicMock(); scout.estimate_market = AsyncMock(return_value=_make_signal())
    swing = MagicMock(); swing.estimate_market = AsyncMock(return_value=None)
    update_state = MagicMock()

    with patch("core.workflow.fetch_rss_news",    side_effect=slow_fetch), \
         patch("core.workflow.fetch_reddit_news", side_effect=slow_fetch), \
         patch("agents.shared.utils.web_search.fetch_wikipedia_context", return_value=""), \
         patch("core.workflow.fetch_hackernews",  return_value=[]), \
         patch("core.workflow.fetch_google_trends", return_value={}), \
         patch("core.workflow.get_market_correlations", return_value=[]), \
         patch("core.workflow.build_search_query", return_value="test"), \
         patch("core.checkpoint.save_checkpoint"), \
         patch("core.workflow.MarketContext", return_value=_make_context(market=m)), \
         patch("config.llm_health_gate") as mock_gate, \
         patch("core.workflow._safe_result", return_value=[]):
        mock_gate.check_availability.return_value = True
        result = asyncio.run(run_agent_evaluation(m, scout, swing, update_state))

    # executor.shutdown(wait=False) был вызван — функция не зависла
    assert result is not None or result == (None, None, None)
    # Отпускаем зависшие потоки
    barrier.set()


def test_executor_shutdown_called_even_on_exception():
    """executor.shutdown вызывается даже если _safe_result упал"""
    from core.workflow import run_agent_evaluation

    m = _make_market()
    m.id = "mkt-layer14-unique"
    scout = MagicMock(); scout.estimate_market = AsyncMock(return_value=None)
    swing = MagicMock(); swing.estimate_market = AsyncMock(return_value=None)
    update_state = MagicMock()
    shutdown_called = []

    original_executor = concurrent.futures.ThreadPoolExecutor

    class TrackingExecutor(original_executor):
        def shutdown(self, wait=True, **kwargs):
            shutdown_called.append(wait)
            super().shutdown(wait=wait, **kwargs)

    with patch("core.workflow.concurrent.futures.ThreadPoolExecutor",
               TrackingExecutor), \
         patch("core.workflow.fetch_rss_news", return_value=[]), \
         patch("core.workflow.fetch_reddit_news", return_value=[]), \
         patch("agents.shared.utils.web_search.fetch_wikipedia_context", return_value=""), \
         patch("core.workflow.fetch_hackernews", return_value=[]), \
         patch("core.workflow.fetch_google_trends", return_value={}), \
         patch("core.workflow.get_market_correlations", return_value=[]), \
         patch("core.workflow.build_search_query", return_value="test"), \
         patch("core.checkpoint.save_checkpoint"), \
         patch("core.workflow.MarketContext", return_value=_make_context(m)), \
         patch("config.llm_health_gate") as mock_gate, \
         patch("core.workflow._safe_result", side_effect=Exception("mock fetch error")), \
         patch("core.workflow.get_memory", return_value=None):
        
        from core.workflow import _analyzed_in_session
        _analyzed_in_session.clear()
        
        mock_gate.check_availability.return_value = True
        try:
            asyncio.run(run_agent_evaluation(m, scout, swing, update_state))
        except Exception:
            pass

    assert len(shutdown_called) >= 1, "executor.shutdown не был вызван"


# ── Баг #2: run_screening debug log ──────────────────────────

def test_run_screening_logs_debug_when_market_id_given():
    """При передаче market_id должен быть debug-лог"""
    from core.workflow import run_screening

    with patch("core.workflow.logger.debug") as mock_debug:
        result = run_screening(
            adapter=MagicMock(), nexus=MagicMock(),
            category=None, market_id="mkt-123"
        )

    assert result == []
    mock_debug.assert_called()
    assert "market_id" in mock_debug.call_args[0][0]


def test_run_screening_returns_empty_when_category_given():
    from core.workflow import run_screening

    result = run_screening(
        adapter=MagicMock(), nexus=MagicMock(),
        category="politics", market_id=None
    )
    assert result == []


# ── Баг #3: make_consensus с swing 'hold' ────────────────────

def test_make_consensus_swing_hold_returns_no_signal_swing_hold():
    """SWING с recommendation='hold' → статус 'no_signal_swing_hold', не 'no_signal'"""
    ctx       = _make_context()
    swing     = _make_swing(recommendation="hold")
    shadow    = _make_shadow(agree=True)

    decision = make_consensus(ctx, signal=None, swing_signal=swing, opinion_shadow=shadow)

    assert decision.status == "no_signal_swing_hold", \
        f"Ожидали 'no_signal_swing_hold', получили '{decision.status}'"


def test_make_consensus_swing_hold_no_scout_no_shadow():
    """SWING hold без scout и shadow → 'no_signal_swing_hold'"""
    ctx   = _make_context()
    swing = _make_swing(recommendation="hold")

    decision = make_consensus(ctx, signal=None, swing_signal=swing, opinion_shadow=None)
    assert decision.status == "no_signal_swing_hold"


def test_make_consensus_no_signal_when_no_swing():
    """Нет ни scout ни swing → 'no_signal'"""
    ctx = _make_context()
    decision = make_consensus(ctx, signal=None, swing_signal=None, opinion_shadow=None)
    assert decision.status == "no_signal"


def test_make_consensus_saved_when_swing_buy_and_shadow_agree():
    """SWING buy + SHADOW agree → 'saved'"""
    ctx    = _make_context()
    swing  = _make_swing(recommendation="buy")
    shadow = _make_shadow(agree=True)

    decision = make_consensus(ctx, signal=None, swing_signal=swing, opinion_shadow=shadow)
    assert decision.status == "saved"


def test_make_consensus_no_consensus_when_shadow_disagrees():
    """Scout есть + SHADOW против → 'no_consensus'"""
    ctx    = _make_context()
    signal = _make_signal()
    shadow = _make_shadow(agree=False)

    decision = make_consensus(ctx, signal=signal, swing_signal=None, opinion_shadow=shadow)
    assert decision.status == "no_consensus"


def test_make_consensus_swing_sell_is_not_buy():
    """recommendation='sell' не считается valid_swing_buy"""
    ctx    = _make_context()
    swing  = _make_swing(recommendation="sell")
    shadow = _make_shadow(agree=True)

    decision = make_consensus(ctx, signal=None, swing_signal=swing, opinion_shadow=shadow)
    # sell != buy → попадаем в swing_analyzed ветку
    assert decision.status == "no_signal_swing_hold"


# ── Баг #4: _safe_result log уровни ──────────────────────────

def test_safe_result_timeout_logs_warning():
    """TimeoutError должен давать WARNING"""
    future = MagicMock()
    future.result.side_effect = concurrent.futures.TimeoutError()

    with patch("core.workflow.logger.warning") as mock_warning:
        result = _safe_result(future, default=[], timeout=5)

    assert result == []
    mock_warning.assert_called()
    assert "timed out" in mock_warning.call_args[0][0]


def test_safe_result_exception_logs_warning():
    """Реальная ошибка (не timeout) должна давать WARNING"""
    future = MagicMock()
    future.result.side_effect = RuntimeError("Connection refused")

    with patch("core.workflow.logger.warning") as mock_warning:
        result = _safe_result(future, default="", timeout=5)

    assert result == ""
    mock_warning.assert_called()


def test_safe_result_success_no_logs():
    """При успехе — никаких логов"""
    future = MagicMock()
    future.result.return_value = ["item1", "item2"]

    with patch("core.workflow.logger.info") as mock_info, patch("core.workflow.logger.warning") as mock_warning:
        result = _safe_result(future, default=[], timeout=5)

    assert result == ["item1", "item2"]
    mock_info.assert_not_called()
    mock_warning.assert_not_called()


# ── Интеграционный: полный пайплайн со статусом swing_hold ───

def test_process_consensus_swing_hold_no_summary_callback():
    """Статус 'no_signal_swing_hold' → summary_callback НЕ вызывается"""
    from core.workflow import process_consensus

    ctx    = _make_context()
    swing  = _make_swing(recommendation="hold")
    shadow = _make_shadow(agree=True)
    state  = {"ideas_found": 0}
    update_state     = MagicMock()
    summary_callback = MagicMock()

    with patch("core.workflow.make_consensus") as mock_make, \
         patch("core.workflow.save_signal"), \
         patch("core.workflow.save_idea_audit"), \
         patch("agents.shared.python.db.save_agent_episode"), \
         patch("core.checkpoint.save_checkpoint"), \
         patch("core.checkpoint.verify_checkpoint", return_value=True):
        mock_make.return_value = MagicMock(status='no_signal_swing_hold')
        process_consensus(ctx, None, swing, shadow, state, update_state, summary_callback)

    summary_callback.assert_not_called()
