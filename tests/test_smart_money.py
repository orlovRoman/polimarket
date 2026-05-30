# tests/test_smart_money.py

import pytest
from unittest.mock import patch
from core.smart_money import analyze_smart_money


def _trade(addr, outcome, size, price):
    return {"maker_address": addr, "outcome_index": outcome, "size": size, "price": price}


def _pos(addr, outcome, size, avg_price):
    return {"proxy_wallet_address": addr, "outcome_index": outcome, "size": size, "avg_price": avg_price}


# ── Баг #1: анонимные трейды не должны агрегироваться ────────

def test_anonymous_trades_skipped():
    trades = [
        {"maker_address": None, "taker_address": None, "outcome_index": 0, "size": 1000, "price": 0.6},
        {"maker_address": "",   "taker_address": "",   "outcome_index": 0, "size": 500,  "price": 0.6},
    ]
    with patch("core.smart_money.get_known_whales", return_value={}):
        result = analyze_smart_money(trades, [])
    assert result.available is True
    assert result.total_yes_usd == 0
    assert result.top_wallets == []


def test_anonymous_trades_not_in_top_wallets():
    trades = [
        {"maker_address": None, "taker_address": None, "outcome_index": 0, "size": 99999, "price": 1.0},
        _trade("0xABC", 0, 100, 0.6),
    ]
    with patch("core.smart_money.get_known_whales", return_value={}):
        result = analyze_smart_money(trades, [])
    assert len(result.top_wallets) == 1
    assert "0xABC" in result.top_wallets[0]


def test_named_trade_is_counted():
    trades = [_trade("0xDEF", 1, 200, 0.4)]
    with patch("core.smart_money.get_known_whales", return_value={}):
        result = analyze_smart_money(trades, [])
    assert result.total_no_usd == pytest.approx(80.0)


# ── Баг #2: positions обрабатываются ─────────────────────────

def test_positions_contribute_to_totals():
    positions = [_pos("0xAAA", 0, 500, 0.7)]
    with patch("core.smart_money.get_known_whales", return_value={}):
        result = analyze_smart_money([], positions)
    assert result.available is True
    assert result.total_yes_usd == pytest.approx(350.0)


def test_positions_anonymous_skipped():
    positions = [{"proxy_wallet_address": None, "wallet_address": "", "outcome_index": 0, "size": 999, "avg_price": 0.9}]
    with patch("core.smart_money.get_known_whales", return_value={}):
        result = analyze_smart_money([], positions)
    assert result.total_yes_usd == 0


def test_positions_and_trades_combined():
    trades = [_trade("0xAAA", 0, 100, 0.6)]
    positions = [_pos("0xAAA", 0, 200, 0.7)]
    with patch("core.smart_money.get_known_whales", return_value={}):
        result = analyze_smart_money(trades, positions)
    # 0xAAA: 100*0.6 + 200*0.7 = 60 + 140 = 200
    assert result.total_yes_usd == pytest.approx(200.0)


def test_only_positions_no_trades_available_true():
    """Если trades пустой а positions непустой → available=True"""
    positions = [_pos("0xBBB", 1, 50, 0.5)]
    with patch("core.smart_money.get_known_whales", return_value={}):
        result = analyze_smart_money([], positions)
    assert result.available is True
    assert result.total_no_usd == pytest.approx(25.0)


# ── Баг #3: win_rate=0.0 должен отображаться ─────────────────

def test_win_rate_zero_shown():
    trades = [_trade("0xWHALE", 0, 1000, 0.5)]
    whales = {"0xWHALE": {"alias": "LooserWhale", "win_rate": 0.0}}
    with patch("core.smart_money.get_known_whales", return_value=whales):
        result = analyze_smart_money(trades, [])
    assert "WR: 0%" in result.summary


def test_win_rate_none_hidden():
    trades = [_trade("0xWHALE", 0, 1000, 0.5)]
    whales = {"0xWHALE": {"alias": "UnknownWhale"}}
    with patch("core.smart_money.get_known_whales", return_value=whales):
        result = analyze_smart_money(trades, [])
    assert "WR:" not in result.summary


def test_win_rate_50_shown():
    trades = [_trade("0xWHALE", 0, 1000, 0.5)]
    whales = {"0xWHALE": {"alias": "MidWhale", "win_rate": 0.5}}
    with patch("core.smart_money.get_known_whales", return_value=whales):
        result = analyze_smart_money(trades, [])
    assert "WR: 50%" in result.summary


# ── yes_dominance edge cases ──────────────────────────────────

def test_yes_dominance_all_yes():
    trades = [_trade("0xA", 0, 100, 1.0)]
    with patch("core.smart_money.get_known_whales", return_value={}):
        result = analyze_smart_money(trades, [])
    assert result.yes_dominance == 1.0


def test_yes_dominance_equal():
    trades = [_trade("0xA", 0, 100, 1.0), _trade("0xB", 1, 100, 1.0)]
    with patch("core.smart_money.get_known_whales", return_value={}):
        result = analyze_smart_money(trades, [])
    assert result.yes_dominance == 0.5


def test_no_data_returns_unavailable():
    with patch("core.smart_money.get_known_whales", return_value={}):
        result = analyze_smart_money([], [])
    assert result.available is False


# ── guards.py: LLMHealthGate ──────────────────────────────────

def test_llm_health_gate_dead_raises():
    from core.guards import LLMHealthGate, LLMUnavailableError
    gate = LLMHealthGate()
    gate._force_dead()
    with pytest.raises(LLMUnavailableError):
        gate.check_availability()


def test_llm_health_gate_degraded_returns_false():
    from core.guards import LLMHealthGate
    from datetime import datetime, timedelta, timezone
    gate = LLMHealthGate()
    with gate.lock:
        gate.state = "DEGRADED"
        gate.retry_after = datetime.now(timezone.utc) + timedelta(seconds=60)
    result = gate.check_availability()
    assert result is False


def test_llm_health_gate_healthy_returns_true():
    from core.guards import LLMHealthGate
    gate = LLMHealthGate()
    assert gate.check_availability() is True


def test_llm_health_gate_recovers_after_pause():
    from core.guards import LLMHealthGate
    from datetime import datetime, timedelta, timezone
    gate = LLMHealthGate()
    with gate.lock:
        gate.state = "DEGRADED"
        gate.retry_after = datetime.now(timezone.utc) - timedelta(seconds=1)  # уже истекло
    result = gate.check_availability()
    assert result is True
    assert gate.state == "HEALTHY"


def test_record_errors_transitions_to_dead():
    from core.guards import LLMHealthGate, LLMUnavailableError
    gate = LLMHealthGate()
    for _ in range(5):
        gate.record_error(429)
    assert gate.state == "DEAD"
    with pytest.raises(LLMUnavailableError):
        gate.check_availability()
