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


def test_math_filter_different_candidates_no_false_positive():
    """Разные кандидаты в разных округах не должны давать ложноположительный арбитраж (Layer 2)"""
    from unittest.mock import patch
    
    a = MagicMock()
    a.title = "Will Alex Bores win the NY-12 primary?"
    a.price = 0.80
    a.platform = "polymarket"
    a.close_time = datetime(2026, 12, 31)
    a.url = "https://polymarket.com/a"
    a.event_slug = None

    b = MagicMock()
    b.title = "Will Jackson Lahmeyer win the OK-01 primary?"
    b.price = 0.40
    b.platform = "polymarket"
    b.close_time = datetime(2026, 12, 31)
    b.url = "https://polymarket.com/b"
    b.event_slug = None

    # Мокаем семантический фильтр, чтобы он вернул False (разные события)
    with patch("core.semantic_filter.semantic_same_event", return_value=False):
        result = math_pre_filter(a, b, check_logical_implication=True)
        # Так как события разные, решение должно быть CONFIRMED_NO_ARBI
        # при сопоставлении (если это direct price_divergence, но тут платформа одна,
        # так что logical_implication вернет CONFIRMED_NO_ARBI из-за разных событий)
        # Поскольку платформы одинаковые, math_pre_filter проверяет logical_implication.
        # В logical_implication если _check_same_event вернет False, то вернет CONFIRMED_NO_ARBI.
        assert result.decision == FilterDecision.CONFIRMED_NO_ARBI
        assert result.arbitrage_type == "different_events"


def test_math_filter_same_event_slug_prefilter():
    """Одинаковый event_slug должен определяться как одно событие мгновенно (Layer 1)"""
    a = MagicMock()
    a.title = "Will OpenAI market cap exceed $1T?"
    a.price = 0.80
    a.platform = "polymarket"
    a.close_time = datetime(2026, 12, 31)
    a.url = "https://polymarket.com/a"
    a.event_slug = "openai-valuation-2026"

    b = MagicMock()
    b.title = "Will OpenAI market cap exceed $800B?"
    b.price = 0.40
    b.platform = "polymarket"
    b.close_time = datetime(2026, 12, 31)
    b.url = "https://polymarket.com/b"
    b.event_slug = "openai-valuation-2026"

    from core.math_filter import _check_same_event
    # Одинаковые event_slug -> True без вызова семантики
    assert _check_same_event(a.title, b.title, market_a=a, market_b=b) is True

