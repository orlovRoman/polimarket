import pytest
import sqlite3
import contextlib
from unittest.mock import patch

@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE signals (id INTEGER PRIMARY KEY, type TEXT, status TEXT,
            was_profitable INTEGER, pnl_realized REAL, resolved_at TEXT);
        CREATE TABLE idea_audit (id INTEGER PRIMARY KEY, market_id TEXT,
            final_outcome TEXT, shadow_agree INTEGER, scout_edge REAL,
            scout_probability REAL, shadow_reason TEXT, created_at TEXT);
        CREATE TABLE markets (id TEXT PRIMARY KEY, outcome TEXT);
        CREATE TABLE penny_stocks_monitoring (market_id TEXT, status TEXT,
            predicted_outcome TEXT, actual_outcome TEXT, resolved_at TEXT);
        CREATE TABLE whale_virtual_trades_history (id INTEGER PRIMARY KEY,
            market_id TEXT, pnl_cents REAL, sold_at TEXT);
        CREATE TABLE compound_virtual_trades_history (id INTEGER PRIMARY KEY,
            pnl_usd REAL, sold_at TEXT);
        CREATE TABLE llm_calls (id INTEGER PRIMARY KEY, agent_name TEXT,
            total_tokens INTEGER, created_at TEXT);
        CREATE TABLE calibration_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            trigger_type TEXT, window_days INTEGER,
            signals_analyzed INTEGER, metrics_json TEXT,
            nexus_response TEXT, params_proposed INTEGER DEFAULT 0,
            params_applied INTEGER DEFAULT 0, status TEXT DEFAULT 'completed');
        CREATE TABLE calibration_params (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_type TEXT, param_name TEXT, param_value TEXT,
            previous_value TEXT, reason TEXT, status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE memory (key TEXT PRIMARY KEY, value TEXT);
    """)
    yield conn
    conn.close()

@contextlib.contextmanager
def mock_conn(db):
    yield db

class TestRunCalibrationReturnType:
    """run_calibration ВСЕГДА должна возвращать (str, bool)."""

    @pytest.mark.asyncio
    async def test_returns_tuple_on_low_data(self, db):
        """Меньше 5 рынков → (str, False)."""
        with patch("agents.orchestrator.scripts.calibrate.get_connection",
                   side_effect=lambda: mock_conn(db)):
            from agents.orchestrator.scripts.calibrate import run_calibration
            result = await run_calibration(window_days=7)

        assert isinstance(result, tuple), f"Ожидался tuple, получен {type(result)}"
        assert len(result) == 2
        report, has_updates = result
        assert isinstance(report, str)
        assert has_updates is False

    @pytest.mark.asyncio
    async def test_returns_tuple_on_llm_error(self, db):
        """Ошибка LLM → (str, False), не исключение."""
        for i in range(10):
            db.execute(
                "INSERT INTO signals VALUES (?,?,?,?,?,datetime('now','-1 day'))",
                (i, "SCOUT", "ARCHIVED", 1, 5.0)
            )
        for i in range(10):
            db.execute(
                "INSERT INTO idea_audit VALUES (?,?,?,?,?,?,?,datetime('now','-1 day'))",
                (i, f"m{i}", "PASS", 1, 0.6, 0.6, None)
            )

        with patch("agents.orchestrator.scripts.calibrate.get_connection",
                   side_effect=lambda: mock_conn(db)), \
             patch("agents.orchestrator.scripts.calibrate._call_llm_for_calibration",
                   side_effect=Exception("API Error")):
            from agents.orchestrator.scripts.calibrate import run_calibration
            result = await run_calibration(window_days=7)

        assert isinstance(result, tuple)
        report, has_updates = result
        assert has_updates is False

    @pytest.mark.asyncio
    async def test_trigger_type_saved_correctly(self, db):
        """trigger_type='manual' должен сохраняться в БД, а не 'schedule'."""
        with patch("agents.orchestrator.scripts.calibrate.get_connection",
                   side_effect=lambda: mock_conn(db)):
            from agents.orchestrator.scripts.calibrate import run_calibration
            await run_calibration(window_days=7, trigger_type="manual")

        row = db.execute(
            "SELECT trigger_type FROM calibration_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row["trigger_type"] == "manual", \
            f"Ожидался 'manual', сохранён '{row['trigger_type']}'"

    @pytest.mark.asyncio
    async def test_scheduled_trigger_saved_correctly(self, db):
        """trigger_type='scheduled' должен сохраняться."""
        with patch("agents.orchestrator.scripts.calibrate.get_connection",
                   side_effect=lambda: mock_conn(db)):
            from agents.orchestrator.scripts.calibrate import run_calibration
            await run_calibration(window_days=7, trigger_type="scheduled")

        row = db.execute(
            "SELECT trigger_type FROM calibration_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["trigger_type"] == "scheduled"

    @pytest.mark.asyncio
    async def test_has_updates_true_when_params_proposed(self, db):
        """Если LLM предложил overlay → has_updates=True."""
        import json, functools
        for i in range(10):
            db.execute(
                "INSERT INTO signals VALUES (?,?,?,?,?,datetime('now','-1 day'))",
                (i, "SCOUT", "ARCHIVED", 1, 5.0)
            )
        for i in range(10):
            db.execute(
                "INSERT INTO idea_audit VALUES (?,?,?,?,?,?,?,datetime('now','-1 day'))",
                (i, f"m{i}", "PASS", 1, 0.6, 0.6, None)
            )

        llm_json_response = json.dumps({
            "scout_overlay": "Снизь уверенность на 10%",
            "swing_overlay": "",
            "shadow_overlay": "",
            "reasoning": "win rate низкий"
        })

        with patch("agents.orchestrator.scripts.calibrate.get_connection",
                   side_effect=lambda: mock_conn(db)), \
             patch("agents.orchestrator.scripts.calibrate._call_llm_for_calibration",
                   return_value=llm_json_response), \
             patch("agents.shared.python.db.get_memory", return_value=""):
            from agents.orchestrator.scripts.calibrate import run_calibration
            result = await run_calibration(window_days=7, trigger_type="manual")

        report, has_updates = result
        assert has_updates is True
