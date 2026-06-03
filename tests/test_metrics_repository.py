import os
import tempfile
import sqlite3
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

# Патчим DB_PATH перед всеми импортами
temp_db_fd, temp_db_path = tempfile.mkstemp(suffix=".sqlite")
os.environ["DB_PATH"] = temp_db_path

import config
config.DB_PATH = Path(temp_db_path)

from agents.shared.python.db import init_db
from core.eval.signal_logger import StrategyType
from core.eval.metrics_repository import MetricsRepository

@pytest.fixture(scope="module", autouse=True)
def setup_database():
    import agents.shared.python.db as db_module
    import core.eval.signal_logger as sl_module
    import core.eval.metrics_repository as mr_module
    db_module.DB_PATH = Path(temp_db_path)
    sl_module.DB_PATH = Path(temp_db_path)
    mr_module.DB_PATH = Path(temp_db_path)
    db_module._db_initialized = False
    init_db()
    yield
    try:
        os.close(temp_db_fd)
        os.remove(temp_db_path)
    except Exception:
        pass

def test_metrics_repository_flow():
    # 1. Заполняем БД тестовыми сигналами
    conn = sqlite3.connect(temp_db_path)
    cursor = conn.cursor()
    
    # 3 решенных сигнала scout (2 WIN, 1 LOSS)
    resolved_time = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    cursor.execute("""
        INSERT INTO signals (id, type, market_id, platform, edge, confidence, priority, summary, details, status, created_at, resolved_at, resolution_outcome, predicted_probability, edge_at_signal, strategy_type, pnl_realized)
        VALUES 
        ('sig-scout-1', 'SCOUT', 'm-1', 'polymarket', 0.1, 0.6, 'medium', 'sum', '{}', 'WIN', ?, ?, 'YES', 0.6, 0.1, 'scout', 8.0),
        ('sig-scout-2', 'SCOUT', 'm-2', 'polymarket', 0.2, 0.7, 'medium', 'sum', '{}', 'WIN', ?, ?, 'YES', 0.7, 0.2, 'scout', 12.0),
        ('sig-scout-3', 'SCOUT', 'm-3', 'polymarket', -0.1, 0.4, 'medium', 'sum', '{}', 'LOSS', ?, ?, 'NO', 0.4, -0.1, 'scout', -10.0)
    """, (resolved_time, resolved_time, resolved_time, resolved_time, resolved_time, resolved_time))
    
    conn.commit()
    conn.close()

    repository = MetricsRepository()
    
    # 2. Вычисляем и сохраняем метрики
    metrics = asyncio.run(repository.compute_and_store_metrics(StrategyType.SCOUT, period_days=10))
    
    assert metrics is not None
    assert metrics.total_signals == 3
    assert metrics.profitable_signals == 2
    assert metrics.win_rate == pytest.approx(0.6667, abs=1e-3)
    assert metrics.avg_edge == pytest.approx(0.0667, abs=1e-3)
    assert metrics.avg_realized_pnl == pytest.approx(3.33)  # (8 + 12 - 10) / 3 = 3.33

    # 3. Проверяем получение последних метрик
    latest = asyncio.run(repository.get_latest_metrics(StrategyType.SCOUT))
    assert latest is not None
    assert latest.total_signals == 3
    assert latest.win_rate == metrics.win_rate

    # 4. Проверяем тренд
    trend = asyncio.run(repository.get_metrics_trend(StrategyType.SCOUT, last_n_periods=5))
    assert len(trend) == 1
    assert trend[0].total_signals == 3

    # 5. Проверяем пустую стратегию (должна вернуть None)
    empty_metrics = asyncio.run(repository.compute_and_store_metrics(StrategyType.WHALE, period_days=10))
    assert empty_metrics is None
