import pytest
from unittest.mock import MagicMock
from datetime import datetime
from core.math_filter import math_pre_filter, FilterDecision, _check_same_event

def test_check_same_event_btc_aliases():
    """btc и bitcoin — один субъект, aliases должны сработать"""
    result = _check_same_event(
        "Will BTC exceed $100K by Dec?",
        "Will Bitcoin hit $100K in December?"
    )
    assert result is True


def test_check_same_event_different_subjects():
    """GDP и revenue — разные субъекты"""
    result = _check_same_event(
        "Will GDP exceed $50B?",
        "Will revenue exceed $50B?"
    )
    assert result is False


def test_identical_threshold_cross_platform_same_event():
    """BTC $100K на Polymarket vs Kalshi — должен быть price_divergence, не identical_threshold"""
    from tests.test_math_filter import make_market

    a = make_market("Will Bitcoin exceed $100K?", 0.55, platform="polymarket")
    b = make_market("Will Bitcoin exceed $100K?", 0.70, platform="kalshi")
    result = math_pre_filter(a, b)
    assert result.arbitrage_type == "price_divergence"
    assert result.decision == FilterDecision.AMBIGUOUS


def test_analyze_correlation_retries_on_llm_failure():
    """analyze_correlation должен иметь @with_retry — сейчас его нет"""
    from agents.polymarket_arbitrage_agent.src.agent import ArbitrageAgent
    method = ArbitrageAgent.analyze_correlation
    assert hasattr(method, '__wrapped__'), \
        "analyze_correlation должен быть декорирован @with_retry"
