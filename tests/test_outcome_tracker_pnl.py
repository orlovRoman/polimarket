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


# ── Баг #1: compound resolution ищет по market_id, а не opp["id"] ──────────
class TestResolveCompoundByMarketId:
    @patch("services.outcome_tracker._fetch_resolution", return_value="YES")
    @patch("services.outcome_tracker._resolve_signal")
    @patch("agents.shared.python.db.resolve_compound_opportunity")
    @patch("agents.shared.python.db.get_compound_settings",
           return_value={"virtual_stake": 50.0})
    @patch("agents.shared.python.db.get_connection")
    def test_signal_found_by_market_id_not_opp_id(
        self, mock_conn, mock_cfg,
        mock_resolve_opp, mock_resolve_sig, mock_fetch
    ):
        opp = {
            "id": "0xabc123_2026-06-05",  # opp_id, НЕ signal.id
            "market_id": "0xabc123",
            "status": "BOUGHT",
            "price": 0.97,
        }
        # Сигнал найден по market_id
        fake_sig = {"id": "scout-uuid-0xabc123", "market_id": "0xabc123",
                    "strategy_type": "FAVOURITE_COMPOUND", "target_outcome": "YES",
                    "edge": 0.03, "confidence": 0.8, "created_at": "2026-06-05",
                    "estimated_probability": 0.97, "market_price_at_signal": 0.97,
                    "market_title": "Test market"}

        class FakeCursor:
            def __init__(self, data):
                self.data = data
            def fetchall(self):
                return self.data
            def fetchone(self):
                return self.data

        def smart_execute(query, params=()):
            if "compound_opportunities" in query:
                return FakeCursor([opp])
            elif "signals" in query:
                return FakeCursor(fake_sig)
            return FakeCursor(None)

        conn_mock = mock_conn.return_value.__enter__.return_value
        conn_mock.execute.side_effect = smart_execute

        from services.outcome_tracker import _resolve_compound_outcomes
        count = _resolve_compound_outcomes()

        assert count == 1
        mock_resolve_sig.assert_called_once()
        # Должен искать по market_id, не по opp["id"]
        call_args = conn_mock.execute.call_args
        assert "market_id" in call_args[0][0]

    @patch("services.outcome_tracker._fetch_resolution", return_value="NO")
    @patch("services.outcome_tracker._resolve_signal")
    @patch("agents.shared.python.db.resolve_compound_opportunity")
    @patch("agents.shared.python.db.get_compound_settings",
           return_value={"virtual_stake": 50.0})
    @patch("agents.shared.python.db.get_connection")
    def test_resolve_compound_no_outcome_correct_pnl(
        self, mock_conn, mock_cfg,
        mock_resolve_opp, mock_resolve_sig, mock_fetch
    ):
        opp = {
            "id": "0xabc123_2026-06-05",
            "market_id": "0xabc123",
            "status": "BOUGHT",
            "price": 0.96,
            "outcome": "NO",
        }

        class FakeCursor:
            def __init__(self, data):
                self.data = data
            def fetchall(self):
                return self.data
            def fetchone(self):
                return self.data

        def smart_execute(query, params=()):
            if "compound_opportunities" in query:
                return FakeCursor([opp])
            elif "signals" in query:
                return FakeCursor(None)
            return FakeCursor(None)

        conn_mock = mock_conn.return_value.__enter__.return_value
        conn_mock.execute.side_effect = smart_execute

        from services.outcome_tracker import _resolve_compound_outcomes
        count = _resolve_compound_outcomes()

        assert count == 1
        mock_resolve_opp.assert_called_once()
        args = mock_resolve_opp.call_args[0]
        assert args[0] == opp["id"]
        assert args[1] == "NO"
        import pytest
        assert args[2] == pytest.approx(2.04, abs=0.01)


# ── Баг #2: avg_realized_pnl пишется в strategy_metrics ─────────────────────
class TestStrategyMetricsAvgPnl:
    @patch("services.outcome_tracker.get_connection")
    def test_avg_realized_pnl_not_null(self, mock_conn):
        """avg_realized_pnl должен записываться, а не быть NULL"""
        from services.outcome_tracker import _upsert_strategy_metrics

        mock_row = {
            "total": 5, "resolved": 5, "wins": 4,
            "avg_edge": 0.05, "win_rate": 0.8,
            "brier_score": 0.1, "avg_realized_pnl": 2.5,
        }
        mock_pnl_rows = [{"pnl_realized": 2.0}, {"pnl_realized": 3.0}]
        conn_mock = mock_conn.return_value.__enter__.return_value
        conn_mock.execute.return_value.fetchone.return_value = mock_row
        conn_mock.execute.return_value.fetchall.return_value = mock_pnl_rows

        _upsert_strategy_metrics("FAVOURITE_COMPOUND")

        # Проверяем что в INSERT переданы параметры включая avg_realized_pnl
        insert_call = [
            c for c in conn_mock.execute.call_args_list
            if "INSERT INTO strategy_metrics" in str(c)
        ]
        assert len(insert_call) >= 1
        params = insert_call[0][0][1]
        assert 2.5 in params, "avg_realized_pnl должен быть в параметрах INSERT"


# ── Баг #3: _send_telegram_summary находит метрики при окне 1h ───────────────
class TestTelegramSummaryMetricsWindow:
    @patch("services.notifications.send_telegram")
    @patch("services.outcome_tracker.get_connection")
    def test_metrics_window_is_1_hour_not_5_minutes(self, mock_conn, mock_tg):
        conn_mock = mock_conn.return_value.__enter__.return_value
        conn_mock.execute.return_value.fetchall.return_value = []

        from services.outcome_tracker import _send_telegram_summary
        _send_telegram_summary([])

        query_call = conn_mock.execute.call_args
        query_str = query_call[0][0]
        # Убедиться что используется 1 hour, а не 5 minutes
        assert "1 hour" in query_str or "-1 hour" in query_str, \
            f"Слишком узкое окно в запросе: {query_str}"
