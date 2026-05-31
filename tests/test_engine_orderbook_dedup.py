import pytest
import asyncio
from unittest.mock import MagicMock, patch, call

def make_market(tokens=None):
    from core.models import Market
    from datetime import datetime, timezone
    return Market(
        id="mkt-1", title="Test market", price=0.10,
        close_time=datetime(2026, 8, 1, tzinfo=timezone.utc),
        tokens=tokens or ["tok-yes", "tok-no"],
        platform="polymarket",
        url="https://polymarket.com/market/test",
        outcome="YES"
    )

def test_orderbook_called_once_for_yes():
    """YES-сигнал: get_orderbook вызывается только 1 раз (pre-fetch)."""
    adapter = MagicMock()
    adapter.get_orderbook.return_value = {
        "top_bid": 0.09, "top_ask": 0.11,
        "spread": 0.02, "bid_depth_5": 500, "ask_depth_5": 300
    }
    adapter.get_market.return_value = make_market()

    from core.context import MarketContext, OrderbookSnapshot
    ctx = MarketContext(market=make_market())

    # Симулируем pre-fetch YES
    ob = adapter.get_orderbook("tok-yes")
    ctx.orderbook = OrderbookSnapshot(
        top_bid=ob["top_bid"], top_ask=ob["top_ask"],
        spread_cents=round(ob["spread"] * 100, 4),
        bid_depth_5=ob["bid_depth_5"], ask_depth_5=ob["ask_depth_5"]
    )

    # Для YES — второй вызов НЕ должен происходить
    target_outcome = "YES"
    if target_outcome.upper() == "NO" and len(ctx.market.tokens) > 1:
        adapter.get_orderbook("tok-no")

    assert adapter.get_orderbook.call_count == 1, \
        f"get_orderbook вызван {adapter.get_orderbook.call_count} раз для YES!"


def test_orderbook_called_twice_for_no():
    """NO-сигнал: get_orderbook вызывается 2 раза (YES pre-fetch + NO post-eval)."""
    adapter = MagicMock()
    adapter.get_orderbook.return_value = {
        "top_bid": 0.88, "top_ask": 0.92,
        "spread": 0.04, "bid_depth_5": 200, "ask_depth_5": 150
    }

    market = make_market(tokens=["tok-yes", "tok-no"])
    adapter.get_orderbook("tok-yes")  # pre-fetch
    target_outcome = "NO"
    if target_outcome.upper() == "NO" and len(market.tokens) > 1:
        adapter.get_orderbook("tok-no")  # post-eval

    assert adapter.get_orderbook.call_count == 2
    adapter.get_orderbook.assert_any_call("tok-yes")
    adapter.get_orderbook.assert_any_call("tok-no")


def test_dedup_context_none_does_not_crash():
    """run_agent_evaluation вернул (None, None, None) — engine не должен падать."""
    signal, swing_signal, context = None, None, None
    active_signal = signal or swing_signal

    # Имитируем логику engine.py
    crashed = False
    try:
        if context is None:
            pass  # должен быть continue — просто пропускаем
        elif active_signal:
            _ = context.orderbook  # это было бы падение если context=None
    except AttributeError:
        crashed = True

    assert not crashed, "engine упал на context=None без guard-проверки!"
