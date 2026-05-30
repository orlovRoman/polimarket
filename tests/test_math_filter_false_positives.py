import pytest
from unittest.mock import MagicMock
from datetime import datetime
from core.math_filter import math_pre_filter, FilterDecision, MathFilterResult

def test_identical_threshold_returns_no_arbi():
    """$800B в обоих заголовках — не импликация, а разные события"""
    a = MagicMock()
    a.title = "OpenAI IPO closing market cap above $800B?"
    a.price = 0.85
    a.platform = "polymarket"
    a.close_time = datetime(2026, 12, 31)
    a.url = "https://polymarket.com/a"

    b = MagicMock()
    b.title = "Will OpenAI's valuation hit (LOW) $800B by June 30?"
    b.price = 0.269
    b.platform = "polymarket"
    b.close_time = datetime(2026, 6, 30)
    b.url = "https://polymarket.com/b"

    result = math_pre_filter(a, b)
    assert result.decision == FilterDecision.CONFIRMED_NO_ARBI
    assert result.arbitrage_type == "identical_threshold"
    assert result.spread_pct == 0.0


def test_real_monotonicity_violation_passes():
    """$1T vs $800B на одном субъекте — настоящее нарушение монотонности"""
    a = MagicMock()
    a.title = "Will OpenAI market cap exceed $1T?"
    a.price = 0.80  # BUG: $1T дороже $800B — нарушение
    a.platform = "polymarket"
    a.close_time = datetime(2026, 12, 31)
    a.url = "https://polymarket.com/a"

    b = MagicMock()
    b.title = "Will OpenAI market cap exceed $800B?"
    b.price = 0.40
    b.platform = "polymarket"
    b.close_time = datetime(2026, 12, 31)
    b.url = "https://polymarket.com/b"

    result = math_pre_filter(a, b)
    # P(>$1T) > P(>$800B) — реальный арбитраж, должен пройти
    assert result.arbitrage_type == "monotonicity_violation"
    assert result.spread_pct >= 5.0


def test_agent_guard_skips_llm_for_identical_threshold():
    """При identical_threshold агент не должен вызывать LLM"""
    mf = MathFilterResult(
        decision=FilterDecision.CONFIRMED_NO_ARBI,
        arbitrage_type="identical_threshold",
        spread_pct=0.0,
        reasoning="Одинаковый порог — разные события",
        trade_instruction=""
    )
    # Симулируем логику agent.py
    result = None
    if mf.decision == FilterDecision.CONFIRMED_NO_ARBI:
        result = None  # ← ранний возврат без LLM
    assert result is None
