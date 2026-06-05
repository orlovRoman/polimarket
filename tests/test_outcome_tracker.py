# tests/test_outcome_tracker.py
"""Самотесты Outcome Tracker — без сети и реальной БД."""
import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
import pytest

from services.outcome_tracker import (
    _get_pending_with_closed_market,
    _fetch_resolution,
    _resolve_signal,
    _upsert_strategy_metrics,
    run_resolution_cycle,
)

# ── Helpers ──────────────────────────────────────────────────

def _iso(delta_hours: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).isoformat()

def _make_row(
    signal_id="sig-1",
    market_id="mkt-1",
    target_outcome="YES",
    strategy_type="SCOUT",
    edge=0.12,
    confidence=0.65,
):
    return {
        "id": signal_id,
        "market_id": market_id,
        "target_outcome": target_outcome,
        "strategy_type": strategy_type,
        "edge": edge,
        "confidence": confidence,
        "created_at": _iso(-10),
        "close_time": _iso(-1),
        "market_resolved_outcome": None,
        "market_title": "Test Market Title",
        "estimated_probability": 0.65,
    }


# ── 1. _fetch_resolution: использует API или локальную БД ───

@patch("services.outcome_tracker.get_connection")
@patch("services.polymarket_client.get_market_resolution", return_value="YES")
def test_fetch_resolution_from_api(mock_api, mock_conn):
    result = _fetch_resolution("mkt-1")
    assert result == "YES"
    mock_api.assert_called_once_with("mkt-1")


@patch("services.outcome_tracker.get_connection")
@patch("services.polymarket_client.get_market_resolution", return_value=None)
def test_fetch_resolution_from_db_when_api_empty(mock_api, mock_conn):
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {"outcome": "NO"}
    mock_conn.return_value.__enter__ = lambda s: MagicMock(
        execute=lambda *a, **kw: mock_cursor
    )
    mock_conn.return_value.__exit__ = MagicMock(return_value=False)
    
    result = _fetch_resolution("mkt-1")
    assert result == "NO"


@patch("services.outcome_tracker.get_connection")
@patch("services.polymarket_client.get_market_resolution", return_value=None)
def test_fetch_resolution_returns_none_when_both_empty(mock_api, mock_conn):
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {"outcome": ""}
    mock_conn.return_value.__enter__ = lambda s: MagicMock(
        execute=lambda *a, **kw: mock_cursor
    )
    mock_conn.return_value.__exit__ = MagicMock(return_value=False)
    
    with patch("services.outcome_tracker.logger"):
        result = _fetch_resolution("mkt-no-outcome")
    assert result is None


# ── 2. _resolve_signal: WIN / LOSS ───────────────────────────

@patch("services.outcome_tracker.get_connection")
def test_resolve_signal_marks_win(mock_conn):
    executed = []
    conn_mock = MagicMock()
    conn_mock.execute.side_effect = lambda q, p=None: executed.append((q, p))
    mock_conn.return_value.__enter__ = lambda s: conn_mock
    mock_conn.return_value.__exit__ = MagicMock(return_value=False)

    row = _make_row(target_outcome="YES")
    _resolve_signal(row, resolution="YES")

    assert len(executed) == 2
    
    # 1. Update signals query
    params_sig = executed[0][1]
    assert params_sig[1] == "YES"        # resolution_outcome
    assert params_sig[2] == 1            # was_profitable = WIN
    
    # 2. Update markets query
    params_mkt = executed[1][1]
    assert params_mkt[0] == "YES"        # outcome
    assert params_mkt[1] == "mkt-1"      # market_id


@patch("services.outcome_tracker.get_connection")
def test_resolve_signal_marks_loss(mock_conn):
    executed = []
    conn_mock = MagicMock()
    conn_mock.execute.side_effect = lambda q, p=None: executed.append((q, p))
    mock_conn.return_value.__enter__ = lambda s: conn_mock
    mock_conn.return_value.__exit__ = MagicMock(return_value=False)

    row = _make_row(target_outcome="YES")
    _resolve_signal(row, resolution="NO")

    params_sig = executed[0][1]
    assert params_sig[2] == 0            # was_profitable = LOSS


@patch("services.outcome_tracker.get_connection")
def test_resolve_signal_no_target_defaults_to_yes(mock_conn):
    """target_outcome=None должен сравниваться с YES (default)."""
    executed = []
    conn_mock = MagicMock()
    conn_mock.execute.side_effect = lambda q, p=None: executed.append((q, p))
    mock_conn.return_value.__enter__ = lambda s: conn_mock
    mock_conn.return_value.__exit__ = MagicMock(return_value=False)

    row = _make_row(target_outcome=None)
    _resolve_signal(row, resolution="YES")

    assert executed[0][1][2] == 1   # WIN


# ── 3. run_resolution_cycle: end-to-end с моками ─────────────

@patch("services.outcome_tracker._send_telegram_summary")
@patch("services.outcome_tracker._update_all_strategy_metrics")
@patch("services.outcome_tracker._resolve_signal")
@patch("services.outcome_tracker._fetch_resolution", return_value="YES")
@patch("services.outcome_tracker._get_pending_with_closed_market")
def test_run_cycle_resolves_all(mock_pending, mock_fetch, mock_resolve, mock_metrics, mock_send):
    mock_pending.return_value = [_make_row("s1"), _make_row("s2")]
    stats = run_resolution_cycle()
    assert stats["resolved"] == 2
    assert stats["skipped"] == 0
    assert stats["errors"] == 0
    mock_metrics.assert_called_once()
    mock_send.assert_called_once()


@patch("services.outcome_tracker._send_telegram_summary")
@patch("services.outcome_tracker._update_all_strategy_metrics")
@patch("services.outcome_tracker._fetch_resolution", return_value=None)
@patch("services.outcome_tracker._get_pending_with_closed_market")
def test_run_cycle_skips_unresolved(mock_pending, mock_fetch, mock_metrics, mock_send):
    mock_pending.return_value = [_make_row()]
    stats = run_resolution_cycle()
    assert stats["skipped"] == 1
    assert stats["resolved"] == 0
    mock_metrics.assert_not_called()  # нет резолюций → не пересчитываем метрики
    mock_send.assert_not_called()


@patch("services.outcome_tracker._send_telegram_summary")
@patch("services.outcome_tracker._update_all_strategy_metrics")
@patch("services.outcome_tracker._resolve_signal", side_effect=RuntimeError("boom"))
@patch("services.outcome_tracker._fetch_resolution", return_value="NO")
@patch("services.outcome_tracker._get_pending_with_closed_market")
def test_run_cycle_catches_errors(mock_pending, mock_fetch, mock_resolve, mock_metrics, mock_send):
    mock_pending.return_value = [_make_row()]
    stats = run_resolution_cycle()
    assert stats["errors"] == 1
    assert stats["resolved"] == 0
    mock_metrics.assert_not_called()
    mock_send.assert_not_called()


# ── 4. Граничный кейс: нет закрытых рынков ───────────────────

@patch("services.outcome_tracker._get_pending_with_closed_market", return_value=[])
def test_run_cycle_no_markets(_):
    stats = run_resolution_cycle()
    assert stats == {"resolved": 0, "skipped": 0, "errors": 0}
