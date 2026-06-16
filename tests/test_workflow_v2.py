# tests/test_workflow_v2.py

import pytest
from unittest.mock import MagicMock, patch
from core.models import Signal, SwingSignal, AgentOpinion


# ── Баг #1: run_screening возвращает [] а не None ────────────

def test_run_screening_returns_empty_list_when_category_set():
    from core.workflow import run_screening
    result = run_screening(
        adapter=MagicMock(),
        category="politics",
        market_id=None,
        summary_callback=None
    )
    assert result == [], "Должен вернуть [] а не None"
    assert result is not None, "None сломает caller при итерации"


def test_run_screening_returns_empty_list_when_market_id_set():
    from core.workflow import run_screening
    result = run_screening(
        adapter=MagicMock(),
        category=None,
        market_id="some-market-id",
        summary_callback=None
    )
    assert result == []


def test_run_screening_result_is_iterable():
    from core.workflow import run_screening
    result = run_screening(MagicMock(), "sports", "")
    # Не должно бросать TypeError
    items = list(result)
    assert items == []


# ── Helpers для Pydantic моделей ─────────────────────────────

def _make_context(market_id="test-id", price=0.55):
    ctx = MagicMock()
    ctx.market.id = market_id
    ctx.market.title = "Test Market"
    ctx.market.url = "https://polymarket.com/event/test"
    ctx.market.price = price
    ctx.trigger_type = "scheduled"
    ctx.source_url = ""
    ctx.math_filter_result = None
    return ctx


def _make_real_signal(edge=0.15, outcome="YES", verdict="Strong buy"):
    return Signal(
        id="sig-1",
        type="MISPRICING",
        market_id="test-id",
        platform="polymarket",
        target_outcome=outcome,
        confidence=0.8,
        priority="high",
        summary="summary",
        details="details",
        edge=edge,
        signal_cause=verdict,
        signal_verdict=verdict
    )


def _make_real_swing(recommendation="buy"):
    return SwingSignal(
        id="swing-1",
        market_id="test-id",
        platform="polymarket",
        hype_potential=0.7,
        recommendation=recommendation,
        target_outcome="YES",
        target_exit_price=0.8,
        confidence=0.8,
        reasoning="reason",
        catalyst="Volume spike"
    )


def _make_real_shadow(agree=True):
    return AgentOpinion(
        agent_name="SHADOW",
        market_id="test-id",
        opinion="opinion",
        confidence=0.8,
        agree=agree,
        liquidity_risk="medium",
        orderbook_facts="Good spread"
    )


# ── Баг #2: summary_callback без try/except ──────────────────

def test_process_consensus_survives_failing_callback():
    """Если summary_callback бросит — process_consensus не должен падать"""
    from core.workflow import process_consensus

    ctx = _make_context()
    signal = _make_real_signal()
    shadow = _make_real_shadow(agree=True)

    failing_callback = MagicMock(side_effect=ConnectionError("Telegram API 429"))
    update_state = MagicMock()

    with patch("core.workflow.save_signal", create=True), \
         patch("core.workflow.save_idea_audit", create=True), \
         patch("core.workflow.save_checkpoint", create=True), \
         patch("core.workflow.verify_checkpoint", return_value=True, create=True), \
         patch("core.workflow.save_agent_episode", create=True):

        # Не должно бросать исключение
        process_consensus(ctx, signal, None, shadow,
                          state={}, update_state=update_state,
                          summary_callback=failing_callback)

    failing_callback.assert_called_once()


def test_process_consensus_callback_exception_doesnt_skip_audit():
    """Даже при падении callback — audit должен сохраниться"""
    from core.workflow import process_consensus

    ctx = _make_context()
    signal = _make_real_signal()
    shadow = _make_real_shadow(agree=True)

    failing_callback = MagicMock(side_effect=RuntimeError("network"))

    with patch("core.workflow.save_signal", create=True), \
         patch("core.workflow.save_idea_audit", create=True) as mock_audit, \
         patch("core.workflow.save_checkpoint", create=True), \
         patch("core.workflow.verify_checkpoint", return_value=True, create=True), \
         patch("core.workflow.save_agent_episode", create=True):

        process_consensus(ctx, signal, None, shadow,
                          state={}, update_state=MagicMock(),
                          summary_callback=failing_callback)

    mock_audit.assert_called_once()


# ── Баг #3: recommendation case-insensitive ──────────────────

def test_make_consensus_accepts_lowercase_buy():
    from core.workflow import make_consensus
    ctx = _make_context()
    signal = _make_real_signal()
    shadow = _make_real_shadow(agree=True)
    swing = _make_real_swing("buy")

    result = make_consensus(ctx, signal, swing, shadow)
    assert result.status == "saved"


def test_make_consensus_accepts_uppercase_BUY():
    from core.workflow import make_consensus
    ctx = _make_context()
    signal = _make_real_signal()
    shadow = _make_real_shadow(agree=True)
    swing = _make_real_swing("BUY")   # uppercase — должно работать после фикса

    result = make_consensus(ctx, signal, swing, shadow)
    assert result.status == "saved", \
        "BUY uppercase должен распознаваться как валидный сигнал"


def test_make_consensus_rejects_sell():
    from core.workflow import make_consensus
    ctx = _make_context()
    shadow = _make_real_shadow(agree=True)
    swing = _make_real_swing("sell")

    result = make_consensus(ctx, None, swing, shadow)
    # swing с sell (ignore) -> no_signal_swing_hold
    assert result.status == "no_signal_swing_hold"


def test_make_consensus_no_swing_no_scout_is_no_signal():
    from core.workflow import make_consensus
    ctx = _make_context()
    result = make_consensus(ctx, None, None, None)
    assert result.status == "no_signal"
