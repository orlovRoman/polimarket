import pytest
import sqlite3
from datetime import datetime, timezone, timedelta
from core.eval.signal_logger import StrategyType
from core.eval.metrics_repository import MetricsRepository

def test_compute_and_store_metrics_sync_empty(tmp_path):
    # Создаем временную БД со схемой
    db_path = str(tmp_path / "test_sync.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE strategy_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_type TEXT NOT NULL,
            period_start TIMESTAMP NOT NULL,
            period_end TIMESTAMP NOT NULL,
            total_signals INTEGER NOT NULL,
            resolved_signals INTEGER NOT NULL,
            profitable_signals INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            avg_edge REAL,
            avg_realized_pnl REAL,
            brier_score REAL,
            calibration_error REAL,
            sharpe_ratio REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_metrics_unique 
        ON strategy_metrics (strategy_type, period_start, period_end)
    """)
    conn.execute("""
        CREATE TABLE signals (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            market_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            edge REAL,
            confidence REAL NOT NULL,
            priority TEXT NOT NULL,
            summary TEXT NOT NULL,
            details TEXT NOT NULL,
            status TEXT DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            predicted_probability REAL,
            market_price_at_signal REAL,
            edge_at_signal REAL,
            strategy_type TEXT,
            resolved_at TIMESTAMP,
            pnl_realized REAL
        )
    """)
    conn.commit()

    repo = MetricsRepository()
    # Подменяем _get_connection и DB_PATH
    repo._get_connection = lambda: sqlite3.connect(db_path)
    
    with patch("core.eval.metrics_repository.DB_PATH", db_path):
        result = repo.compute_and_store_metrics_sync(StrategyType.SCOUT)
        assert result is None  # сигналов нет -> None

from unittest.mock import patch
