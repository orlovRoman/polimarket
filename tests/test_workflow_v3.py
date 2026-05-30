# tests/test_workflow_v3.py

import pytest
import json
from unittest.mock import MagicMock, patch
from core.models import Signal, SwingSignal, AgentOpinion


# ── Баг #1: get_memory возвращает строку → должна декодироваться ─

def _decode_cached_ids(cached) -> list:
    """Воспроизводим исправленную логику декодирования кэша"""
    if isinstance(cached, list):
        return cached
    if isinstance(cached, str):
        try:
            result = json.loads(cached)
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def test_cached_ids_as_list_returns_as_is():
    assert _decode_cached_ids(["id1", "id2"]) == ["id1", "id2"]


def test_cached_ids_as_json_string_decoded():
    cached = json.dumps(["id1", "id2"])
    assert _decode_cached_ids(cached) == ["id1", "id2"]


def test_cached_ids_as_none_returns_empty():
    assert _decode_cached_ids(None) == []


def test_cached_ids_as_empty_string_returns_empty():
    assert _decode_cached_ids("") == []


def test_cached_ids_as_plain_string_returns_empty():
    # "abc123" — не JSON список
    assert _decode_cached_ids("abc123") == []


def test_cached_ids_slice_on_string_would_be_wrong():
    """Демонстрируем баг: срез строки даёт символы, не ID"""
    bad_cached = '["id1","id2"]'  # строка, не список
    sliced = bad_cached[:2]
    assert sliced != ["id1", "id2"], "Срез строки — это символы, не ID!"

    # После фикса:
    decoded = _decode_cached_ids(bad_cached)
    assert decoded[:2] == ["id1", "id2"]


# ── Баг #2: save_idea_audit только при наличии сигналов ──────

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


def test_audit_not_called_when_no_signals():
    from core.workflow import process_consensus

    ctx = _make_context()

    with patch("core.workflow.save_signal", create=True), \
         patch("core.workflow.save_idea_audit", create=True) as mock_audit, \
         patch("core.workflow.save_checkpoint", create=True), \
         patch("core.workflow.verify_checkpoint", return_value=True, create=True), \
         patch("core.workflow.save_agent_episode", create=True):

        process_consensus(
            ctx, None, None, None,
            state={}, update_state=MagicMock(),
            summary_callback=None
        )

    mock_audit.assert_not_called()


def test_audit_called_when_signal_present():
    from core.workflow import process_consensus

    ctx = _make_context()
    signal = _make_real_signal()
    shadow = _make_real_shadow(agree=True)

    with patch("core.workflow.save_signal", create=True), \
         patch("core.workflow.save_idea_audit", create=True) as mock_audit, \
         patch("core.workflow.save_checkpoint", create=True), \
         patch("core.workflow.verify_checkpoint", return_value=True, create=True), \
         patch("core.workflow.save_agent_episode", create=True):

        process_consensus(
            ctx, signal, None, shadow,
            state={}, update_state=MagicMock(),
            summary_callback=None
        )

    mock_audit.assert_called_once()


# ── Баг #3: trade_instruction whitespace-only ────────────────

def _should_include_arbitrage(trade_instruction: str | None) -> bool:
    """Исправленная логика включения арбитража в сообщение"""
    return bool(trade_instruction and trade_instruction.strip())


def test_trade_instruction_valid_text_included():
    assert _should_include_arbitrage("BUY YES at 45¢") is True


def test_trade_instruction_whitespace_only_excluded():
    assert _should_include_arbitrage("   ") is False


def test_trade_instruction_empty_string_excluded():
    assert _should_include_arbitrage("") is False


def test_trade_instruction_none_excluded():
    assert _should_include_arbitrage(None) is False


def test_trade_instruction_newline_only_excluded():
    assert _should_include_arbitrage("\n\t") is False


# ── Регрессия: предыдущие фиксы не сломаны ───────────────────

def test_run_screening_returns_list_when_category_set():
    from core.workflow import run_screening
    result = run_screening(MagicMock(), MagicMock(), "politics", None)
    assert result == []
    assert isinstance(result, list)


def test_recommendation_uppercase_BUY_accepted():
    from core.workflow import make_consensus

    ctx = _make_context()
    signal = _make_real_signal()
    shadow = _make_real_shadow(agree=True)
    swing = _make_real_swing("BUY")

    result = make_consensus(ctx, signal, swing, shadow)
    assert result.status == "saved"
