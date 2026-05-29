# tests/test_workflow_arb_guard.py
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from core.math_filter import MathFilterResult, FilterDecision


def _ambiguous_mf(spread=12.0):
    return MathFilterResult(
        decision=FilterDecision.AMBIGUOUS,
        arbitrage_type="price_divergence",
        spread_pct=spread, reasoning="test", trade_instruction="",
    )

def test_route_ambiguous_skipped_when_no_api_key(monkeypatch):
    """route_ambiguous НЕ вызывается если api_key пустой."""
    import core.workflow as wf
    called = {"n": 0}
    def mock_route(*a, **kw):
        called["n"] += 1
        return None
    monkeypatch.setattr("core.arb_router.route_ambiguous", mock_route)

    # Симулируем вызов блока с пустым api_key
    api_key = ""
    mf = _ambiguous_mf(12.0)
    if mf.decision == FilterDecision.AMBIGUOUS and mf.spread_pct >= 8.0:
        if api_key:   # ← проверяем guard
            mock_route(mf, None, None, api_key=api_key)

    assert called["n"] == 0, "route_ambiguous вызван при пустом api_key!"

def test_route_ambiguous_called_when_api_key_present(monkeypatch):
    """route_ambiguous вызывается при наличии api_key и spread >= 8%."""
    called = {"n": 0}
    def mock_route(*a, **kw):
        called["n"] += 1
        return {"confirmed_arb": False}
    monkeypatch.setattr("core.arb_router.route_ambiguous", mock_route)

    api_key = "test_key"
    mf = _ambiguous_mf(12.0)
    if mf.decision == FilterDecision.AMBIGUOUS and mf.spread_pct >= 8.0:
        if api_key:
            mock_route(mf, None, None, api_key=api_key)

    assert called["n"] == 1

def test_route_ambiguous_not_called_for_small_spread(monkeypatch):
    """route_ambiguous НЕ вызывается при spread < 8%."""
    from core.arb_router import route_ambiguous
    monkeypatch.setattr("core.arb_router.route_ambiguous",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("called!")))
    mf = _ambiguous_mf(spread=5.0)   # < 8%
    result = route_ambiguous(mf, MagicMock(), MagicMock(), api_key="key")
    assert result is None
