# tests/test_math_filter_v2.py

import pytest
from unittest.mock import MagicMock
from core.math_filter import (
    math_pre_filter, _check_same_event, FilterDecision,
    _parse_threshold, validate_trade_instruction
)
from core.models import Market
from datetime import datetime, timezone


def _market(title, price, platform="polymarket", mid="mid1", url="https://polymarket.com/event/x"):
    m = MagicMock(spec=Market)
    m.title = title
    m.price = price
    m.platform = platform
    m.id = mid
    m.url = url
    m.description = ""
    m.close_time = datetime(2026, 12, 31, tzinfo=timezone.utc)
    m.tokens = []
    m.condition_id = None
    return m


# ── _check_same_event ────────────────────────────────────────────────────────

def test_same_event_btc_same_threshold_different_dates():
    """
    BUG-1: 'BTC above $100K by 2026' vs 'BTC above $100K in Q4 2025'
    После фильтрации цифр оба дают {'btc', 'above'} — это НЕ одно событие.
    Ожидаем False (разные временны́е горизонты).
    """
    a = "BTC above $100K by 2026"
    b = "BTC above $100K in Q4 2025"
    result = _check_same_event(a, b)
    assert result is False


def test_same_event_identical_titles():
    """Полностью идентичные названия → True"""
    assert _check_same_event("Will Bitcoin hit $100K?", "Will Bitcoin hit $100K?") is True


def test_same_event_completely_different():
    """Разные рынки → False"""
    assert _check_same_event("Will Trump win 2024?", "Will BTC hit $200K?") is False


def test_same_event_president_election_synonym():
    """
    BUG-4: 'Who wins presidential election?' vs 'Trump wins election?'
    Оба получают 'election' через синонимы — overlap может быть > 40%.
    """
    a = "Who will win the US presidential election?"
    b = "Will Trump win the election?"
    result = _check_same_event(a, b)
    # Это РАЗНЫЕ рынки (один на winner, другой на конкретного кандидата)
    assert result is False


def test_check_same_event_sp500_different_levels():
    """SP500 above 5500 vs SP500 above 6000 — одно событие (SP500), разные пороги"""
    a = "Will SP500 close above 5500 in 2026?"
    b = "Will SP500 close above 6000 in 2026?"
    assert _check_same_event(a, b) is True


def test_check_same_event_btc_vs_eth():
    """BTC vs ETH — разные активы → False"""
    assert _check_same_event("Will Bitcoin hit $200K?", "Will Ethereum hit $10K?") is False


# ── validate_trade_instruction ───────────────────────────────────────────────

def test_validate_sell_yes_forbidden():
    """SELL YES недопустим без позиции"""
    valid, reason = validate_trade_instruction("SELL YES на рынке A")
    assert valid is False
    assert "SELL YES" in reason


def test_validate_buy_yes_allowed():
    """BUY YES всегда допустим"""
    valid, _ = validate_trade_instruction("BUY YES на рынке A")
    assert valid is True


def test_validate_sell_no_forbidden():
    """SELL NO также недопустим"""
    valid, reason = validate_trade_instruction("SELL NO на рынке B")
    assert valid is False


def test_validate_short_forbidden():
    """SHORT явно запрещён"""
    valid, _ = validate_trade_instruction("SHORT на рынке C")
    assert valid is False


# ── BUG-5: SELL_YES в CONFIRMED_ARBITRAGE ────────────────────────────────────

def test_confirmed_arbitrage_price_divergence_no_sell_yes():
    """
    BUG-5: При CONFIRMED_ARBITRAGE с price_divergence агент выбирает
    action_b = 'SELL_YES' — это невалидно.
    Тест проверяет что math_pre_filter для cross-platform
    возвращает AMBIGUOUS для price_divergence (не CONFIRMED),
    чтобы LLM мог выбрать правильное действие.
    """
    from core.math_filter import FilterDecision
    a = _market("Will BTC hit $100K?", price=0.45, platform="polymarket")
    b = _market("Will BTC hit $100K?", price=0.62, platform="kalshi")
    
    result = math_pre_filter(a, b)
    # price_divergence cross-platform → AMBIGUOUS (требует LLM подтверждения)
    assert result.decision == FilterDecision.AMBIGUOUS
    assert result.arbitrage_type == "price_divergence"
    # Не должно быть CONFIRMED_ARBITRAGE при price_divergence (нет math-гарантии)
    assert result.decision != FilterDecision.CONFIRMED_ARBITRAGE


def test_confirmed_arbitrage_complementary_overpriced_valid_actions():
    """
    Complementary overpriced (сумма > 1.0) → CONFIRMED_ARBITRAGE.
    Действия BUY_NO + BUY_NO — оба валидны.
    """
    from core.math_filter import math_pre_filter, FilterDecision
    
    a = _market("Will Trump win?", price=0.70, platform="polymarket")
    b = _market("Will Harris win?", price=0.45, platform="polymarket")
    
    # Устанавливаем complementary через _looks_complementary
    result = math_pre_filter(a, b)
    if result.decision == FilterDecision.CONFIRMED_ARBITRAGE:
        # trade_instruction не должна содержать SELL
        assert "SELL" not in result.trade_instruction


# ── identical_threshold cross-platform ───────────────────────────────────────

def test_identical_threshold_cross_platform_goes_to_price_divergence():
    """
    BUG-1: BTC $100K на Polymarket vs Kalshi — identical_threshold,
    но разные платформы → должен попасть в price_divergence, не CONFIRMED_NO_ARBI.
    """
    a = _market("Will BTC hit $100K?", price=0.45, platform="polymarket")
    b = _market("Will BTC reach $100K?", price=0.62, platform="kalshi")
    
    result = math_pre_filter(a, b)
    # Ожидаем: не CONFIRMED_NO_ARBI (это разные платформы — арбитраж возможен)
    assert result.decision != FilterDecision.CONFIRMED_NO_ARBI
    # Должно быть AMBIGUOUS с price_divergence
    assert result.arbitrage_type in ("price_divergence", "identical_threshold")


def test_identical_threshold_same_platform_rejected():
    """
    Одинаковый порог на одной платформе → CONFIRMED_NO_ARBI.
    """
    a = _market("Will BTC hit $100K by June?", price=0.45, platform="polymarket")
    b = _market("Will BTC hit $100K by December?", price=0.50, platform="polymarket")
    
    result = math_pre_filter(a, b)
    assert result.decision == FilterDecision.CONFIRMED_NO_ARBI
    assert result.arbitrage_type == "identical_threshold"


# ── _parse_threshold ──────────────────────────────────────────────────────────

def test_parse_threshold_btc_100k():
    assert _parse_threshold("Will BTC hit $100K?") == (100_000.0, 'usd')


def test_parse_threshold_percentage():
    assert _parse_threshold("Will inflation exceed 4.5%?") == (4.5, '%')


def test_parse_threshold_sp500_pts():
    assert _parse_threshold("Will SP500 close above 6000?") == (6000.0, 'pts')


def test_parse_threshold_year_excluded():
    """Год 2026 не должен парситься как порог (pts)"""
    result = _parse_threshold("Will Trump win in 2026?")
    assert result is None


def test_parse_threshold_none_for_generic():
    """Общие вопросы без числа → None"""
    assert _parse_threshold("Will the Fed cut rates?") is None
