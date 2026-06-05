# tests/test_outcome_tracker_pnl.py
"""Покрывает новую PnL-логику и edge-кейсы резолюции."""
import pytest
from unittest.mock import patch, MagicMock

def _make_row(**kwargs):
    base = {
        "id": "sig-test-1",
        "market_id": "mkt-1",
        "market_title": "Test Market Title That Is Long Enough",
        "target_outcome": "YES",
        "strategy_type": "SCOUT",
        "edge": 0.10,
        "confidence": 0.7,
        "estimated_probability": 0.65,
        "market_price_at_signal": 0.55,
        "created_at": "2026-06-01T00:00:00",
    }
    base.update(kwargs)
    return base


# ── 1. pnl_realized = NULL при resolution=N/A ────────────────

@patch("services.outcome_tracker.get_connection")
@patch("agents.shared.python.db.save_agent_episode")
@patch("agents.shared.python.db.get_memory", return_value=0)
@patch("agents.shared.python.db.save_memory")
def test_pnl_is_none_when_resolution_na(mock_sm, mock_gm, mock_ep, mock_conn):
    """При resolution=N/A pnl_realized должен быть NULL (None), не 0.0."""
    params_captured = []

    class FakeConn:
        def execute(self, q, p=()):
            params_captured.append((q, p))
            return self
        def __enter__(self): return self
        def __exit__(self, *a): return False

    mock_conn.return_value = FakeConn()

    from services.outcome_tracker import _resolve_signal
    _resolve_signal(_make_row(), resolution="N/A")

    # Найти UPDATE signals и проверить pnl_realized
    update_signals = [p for q, p in params_captured if "UPDATE signals" in q]
    assert update_signals, "UPDATE signals не вызван"
    pnl_val = update_signals[0][4]  # 5-й параметр: pnl_realized
    assert pnl_val is None, f"Ожидался None, получено {pnl_val!r}"


# ── 2. pnl > 0 при WIN ───────────────────────────────────────

@patch("services.outcome_tracker.get_connection")
@patch("agents.shared.python.db.save_agent_episode")
@patch("agents.shared.python.db.get_memory", return_value=0)
@patch("agents.shared.python.db.save_memory")
def test_pnl_positive_on_win(mock_sm, mock_gm, mock_ep, mock_conn):
    """При WIN pnl_realized должен быть > 0."""
    params_captured = []

    class FakeConn:
        def execute(self, q, p=()):
            params_captured.append((q, p))
            return self
        def __enter__(self): return self
        def __exit__(self, *a): return False

    mock_conn.return_value = FakeConn()
    
    from services.outcome_tracker import _resolve_signal
    _resolve_signal(_make_row(target_outcome="YES", market_price_at_signal=0.5), resolution="YES")

    update_signals = [p for q, p in params_captured if "UPDATE signals" in q]
    pnl_val = update_signals[0][4]
    assert isinstance(pnl_val, float) and pnl_val > 0, f"Ожидался pnl > 0, получено {pnl_val}"


# ── 3. pnl < 0 при LOSS ──────────────────────────────────────

@patch("services.outcome_tracker.get_connection")
@patch("agents.shared.python.db.save_agent_episode")
@patch("agents.shared.python.db.get_memory", return_value=0)
@patch("agents.shared.python.db.save_memory")
def test_pnl_negative_on_loss(mock_sm, mock_gm, mock_ep, mock_conn):
    """При LOSS pnl_realized должен быть < 0."""
    params_captured = []

    class FakeConn:
        def execute(self, q, p=()):
            params_captured.append((q, p))
            return self
        def __enter__(self): return self
        def __exit__(self, *a): return False

    mock_conn.return_value = FakeConn()
    
    from services.outcome_tracker import _resolve_signal
    _resolve_signal(_make_row(target_outcome="YES", market_price_at_signal=0.5), resolution="NO")

    update_signals = [p for q, p in params_captured if "UPDATE signals" in q]
    pnl_val = update_signals[0][4]
    assert isinstance(pnl_val, float) and pnl_val < 0, f"Ожидался pnl < 0, получено {pnl_val}"


# ── 4. _send_telegram_summary не падает при win_rate=None ────

@patch("services.outcome_tracker.get_connection")
@patch("services.notifications.send_telegram")
def test_send_telegram_summary_handles_null_win_rate(mock_send, mock_conn):
    """Не должно быть TypeError при win_rate=NULL в strategy_metrics."""
    class FakeRow(dict):
        pass

    class FakeConn:
        def execute(self, q, p=()):
            class R:
                def fetchall(_):
                    row = FakeRow({"strategy_type": "SCOUT", "win_rate": None, "total_signals": 5})
                    return [row]
            return R()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    mock_conn.return_value = FakeConn()

    from services.outcome_tracker import _send_telegram_summary
    # Не должно бросать исключение
    _send_telegram_summary([(_make_row(), "YES")])


# ── 5. cleanup_stale_signals существует и возвращает int ─────

def test_cleanup_stale_signals_exists():
    """cleanup_stale_signals должна быть в db.py и возвращать int."""
    try:
        from agents.shared.python.db import cleanup_stale_signals
    except ImportError:
        pytest.fail("cleanup_stale_signals не найдена в db.py — добавь функцию!")

    from unittest.mock import patch as mpatch

    class FakeConn:
        rowcount = 3
        def execute(self, q, p=()):
            return self
        def cursor(self):
            return self
        def __enter__(self): return self
        def __exit__(self, *a): return False

    with mpatch("agents.shared.python.db.get_connection", return_value=FakeConn()):
        result = cleanup_stale_signals(days=90)
    assert isinstance(result, int), f"Должен вернуть int, вернул {type(result)}"
