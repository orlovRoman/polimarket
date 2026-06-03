import os
import tempfile
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import pytest
from unittest.mock import patch

# Патчим DB_PATH перед всеми импортами
temp_db_fd, temp_db_path = tempfile.mkstemp(suffix=".sqlite")
os.environ["DB_PATH"] = temp_db_path

import config
config.DB_PATH = Path(temp_db_path)

from agents.shared.python.db import init_db
from core.eval.signal_logger import SignalLogger, StrategyType

@pytest.fixture(scope="module", autouse=True)
def setup_database():
    # Инициализируем схему во временной БД
    import agents.shared.python.db as db_module
    import core.eval.signal_logger as sl_module
    db_module.DB_PATH = Path(temp_db_path)
    sl_module.DB_PATH = Path(temp_db_path)
    db_module._db_initialized = False
    init_db()
    yield
    # Очистка
    try:
        os.close(temp_db_fd)
        os.remove(temp_db_path)
    except Exception:
        pass

def test_log_signal_and_resolution():
    logger = SignalLogger()
    
    # 1. Запись сигнала
    signal_id = "test-sig-1"
    strategy_type = StrategyType.SCOUT
    market_id = "market-1"
    predicted_prob = 0.75
    market_price = 0.60
    edge = 0.15
    metadata = {
        "target_outcome": "YES",
        "priority": "high",
        "summary": "Test scout signal",
        "platform": "polymarket"
    }
    
    logger.log_signal(
        signal_id=signal_id,
        strategy_type=strategy_type,
        market_id=market_id,
        predicted_probability=predicted_prob,
        market_price_at_signal=market_price,
        edge_at_signal=edge,
        metadata=metadata
    )
    
    # Проверяем запись
    conn = sqlite3.connect(temp_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM signals WHERE id = ?", (signal_id,))
    row = cursor.fetchone()
    assert row is not None
    assert row["id"] == signal_id
    assert row["strategy_type"] == "scout"
    assert row["predicted_probability"] == 0.75
    assert row["market_price_at_signal"] == 0.60
    assert row["edge_at_signal"] == 0.15
    assert row["status"] == "PENDING"
    conn.close()

    # 2. Запись резолюции (выигрышный исход)
    logger.log_resolution(
        signal_id=signal_id,
        resolution_outcome="YES",
        resolution_price=1.0
    )
    
    conn = sqlite3.connect(temp_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM signals WHERE id = ?", (signal_id,))
    row = cursor.fetchone()
    assert row is not None
    assert row["status"] == "WIN"
    assert row["resolution_outcome"] == "YES"
    assert row["resolution_price"] == 1.0
    assert row["was_profitable"] == 1
    assert row["pnl_realized"] > 0.0  # PnL должен быть положительным
    conn.close()

def test_log_resolution_idempotence():
    logger = SignalLogger()
    
    signal_id = "test-sig-idempotent"
    logger.log_signal(
        signal_id=signal_id,
        strategy_type=StrategyType.SCOUT,
        market_id="market-2",
        predicted_probability=0.30,
        market_price_at_signal=0.40,
        edge_at_signal=-0.10,
        metadata={"target_outcome": "NO"}
    )
    
    # Записываем резолюцию первый раз
    logger.log_resolution(
        signal_id=signal_id,
        resolution_outcome="NO",
        resolution_price=0.0
    )
    
    conn = sqlite3.connect(temp_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT status, was_profitable, pnl_realized FROM signals WHERE id = ?", (signal_id,))
    first_res = cursor.fetchone()
    conn.close()
    
    # Записываем резолюцию второй раз с теми же данными
    logger.log_resolution(
        signal_id=signal_id,
        resolution_outcome="NO",
        resolution_price=0.0
    )
    
    conn = sqlite3.connect(temp_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT status, was_profitable, pnl_realized FROM signals WHERE id = ?", (signal_id,))
    second_res = cursor.fetchone()
    conn.close()
    
    assert first_res == second_res
