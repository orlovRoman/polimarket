import pytest
import sqlite3
import contextlib
from unittest.mock import patch
from web.calibration_provider import CalibrationProvider

@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS calibration_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            trigger_type TEXT NOT NULL,
            window_days INTEGER NOT NULL,
            signals_analyzed INTEGER NOT NULL,
            metrics_json TEXT NOT NULL,
            nexus_response TEXT,
            params_proposed INTEGER DEFAULT 0,
            params_applied INTEGER DEFAULT 0,
            status TEXT DEFAULT 'completed'
        );
        CREATE TABLE IF NOT EXISTS calibration_params (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_type TEXT NOT NULL,
            param_name TEXT NOT NULL,
            param_value TEXT NOT NULL,
            previous_value TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            approved_at TIMESTAMP,
            approved_by TEXT DEFAULT 'dashboard',
            rejected_at TIMESTAMP,
            rejected_by TEXT DEFAULT 'dashboard',
            auto_applied INTEGER DEFAULT 0,
            run_id INTEGER REFERENCES calibration_runs(id) DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS memory (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            category TEXT DEFAULT 'general',
            ttl INTEGER DEFAULT NULL,
            priority INTEGER DEFAULT 0,
            expires_at DATETIME DEFAULT NULL
        );
    """)
    yield conn
    conn.close()

@contextlib.contextmanager
def mock_conn(db):
    yield db

def test_get_recent_calibration_runs(db):
    db.execute("INSERT INTO calibration_runs (trigger_type, window_days, signals_analyzed, metrics_json) VALUES (?, ?, ?, ?)",
               ("scheduled", 7, 10, "{}"))
    with patch("web.calibration_provider.get_connection", side_effect=lambda: mock_conn(db)):
        runs = CalibrationProvider.get_recent_calibration_runs(limit=10)
        assert len(runs) == 1
        assert runs[0]['trigger_type'] == "scheduled"
        assert runs[0]['window_days'] == 7

def test_get_pending_calibration_params(db):
    db.execute("INSERT INTO calibration_params (strategy_type, param_name, param_value, previous_value, reason, status) VALUES (?, ?, ?, ?, ?, ?)",
               ("SCOUT", "overlay_prompt", "New scout value", "Old scout value", "test reason", "pending"))
    db.execute("INSERT INTO calibration_params (strategy_type, param_name, param_value, previous_value, reason, status) VALUES (?, ?, ?, ?, ?, ?)",
               ("SWING", "overlay_prompt", "New swing value", "Old swing value", "test reason", "approved"))
    with patch("web.calibration_provider.get_connection", side_effect=lambda: mock_conn(db)):
        pending = CalibrationProvider.get_pending_calibration_params()
        assert len(pending) == 1
        assert pending[0]['strategy_type'] == "SCOUT"
        assert pending[0]['param_value'] == "New scout value"

def test_approve_calibration_param(db):
    db.execute("INSERT INTO calibration_params (strategy_type, param_name, param_value, previous_value, reason, status) VALUES (?, ?, ?, ?, ?, ?)",
               ("SCOUT", "overlay_prompt", "New scout value", "Old scout value", "test reason", "pending"))
    param_id = db.execute("SELECT id FROM calibration_params").fetchone()[0]

    with patch("web.calibration_provider.get_connection", side_effect=lambda: mock_conn(db)), \
         patch("agents.shared.python.db.get_connection", side_effect=lambda: mock_conn(db)):
        success = CalibrationProvider.approve_calibration_param(param_id, approved_by="test_user")
        assert success is True

        # Проверим, что статус обновился
        row = db.execute("SELECT status, approved_by, approved_at FROM calibration_params WHERE id = ?", (param_id,)).fetchone()
        assert row['status'] == 'approved'
        assert row['approved_by'] == 'test_user'
        assert row['approved_at'] is not None

        # Проверим, что сохранилось в memory
        from agents.shared.python.db import get_memory
        assert get_memory("scout_overlay_prompt") == "New scout value"

def test_reject_calibration_param(db):
    db.execute("INSERT INTO calibration_params (strategy_type, param_name, param_value, previous_value, reason, status) VALUES (?, ?, ?, ?, ?, ?)",
               ("SWING", "overlay_prompt", "New swing value", "Old swing value", "test reason", "pending"))
    param_id = db.execute("SELECT id FROM calibration_params").fetchone()[0]

    with patch("web.calibration_provider.get_connection", side_effect=lambda: mock_conn(db)):
        success = CalibrationProvider.reject_calibration_param(param_id, rejected_by="test_user")
        assert success is True

        # Проверим, что статус обновился
        row = db.execute("SELECT status, rejected_by, rejected_at FROM calibration_params WHERE id = ?", (param_id,)).fetchone()
        assert row['status'] == 'rejected'
        assert row['rejected_by'] == 'test_user'
        assert row['rejected_at'] is not None

def test_get_calibration_history(db):
    db.execute("INSERT INTO calibration_params (strategy_type, param_name, param_value, previous_value, reason, status) VALUES (?, ?, ?, ?, ?, ?)",
               ("SCOUT", "overlay_prompt", "val1", "old1", "reason1", "approved"))
    db.execute("INSERT INTO calibration_params (strategy_type, param_name, param_value, previous_value, reason, status) VALUES (?, ?, ?, ?, ?, ?)",
               ("SWING", "overlay_prompt", "val2", "old2", "reason2", "rejected"))
    db.execute("INSERT INTO calibration_params (strategy_type, param_name, param_value, previous_value, reason, status) VALUES (?, ?, ?, ?, ?, ?)",
               ("SHADOW", "overlay_prompt", "val3", "old3", "reason3", "pending"))

    with patch("web.calibration_provider.get_connection", side_effect=lambda: mock_conn(db)):
        history = CalibrationProvider.get_calibration_history(limit=10)
        assert len(history) == 3
        # Проверим сортировку по id DESC
        assert history[0]['strategy_type'] == "SHADOW"
        assert history[1]['strategy_type'] == "SWING"
        assert history[2]['strategy_type'] == "SCOUT"
