import os
import tempfile
import sqlite3
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import pytest

# Патчим DB_PATH перед всеми импортами
temp_db_fd, temp_db_path = tempfile.mkstemp(suffix=".sqlite")
os.environ["DB_PATH"] = temp_db_path

import config
config.DB_PATH = Path(temp_db_path)

from agents.shared.python.db import init_db
from core.eval.signal_logger import StrategyType
from core.eval.threshold_calibrator import CalibrationSuggestion
from core.eval.calibration_store import CalibrationStore

@pytest.fixture(scope="module", autouse=True)
def setup_database():
    import agents.shared.python.db as db_module
    import core.eval.signal_logger as sl_module
    import core.eval.calibration_store as cs_module
    db_module.DB_PATH = Path(temp_db_path)
    sl_module.DB_PATH = Path(temp_db_path)
    cs_module.DB_PATH = Path(temp_db_path)
    db_module._db_initialized = False
    init_db()
    yield
    try:
        os.close(temp_db_fd)
        os.remove(temp_db_path)
    except Exception:
        pass

def test_calibration_store_flow():
    store = CalibrationStore()
    
    suggestion = CalibrationSuggestion(
        param_name="min_edge",
        current_value=0.05,
        suggested_value=0.06,
        confidence=0.85,
        reason="Test suggestion",
        supporting_signals_count=100
    )
    
    # 1. Сохраняем не примененное предложение (auto_apply=False)
    sug_id = asyncio.run(store.save_suggestion(suggestion, StrategyType.SCOUT, auto_apply=False))
    assert sug_id > 0
    
    # Проверяем запись в БД
    conn = sqlite3.connect(temp_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT auto_applied, param_value FROM calibration_params WHERE id = ?", (sug_id,))
    row = cursor.fetchone()
    assert row[0] == 0
    assert row[1] == 0.06
    conn.close()

    # 2. Применяем предложение вручную
    success_apply = asyncio.run(store.apply_suggestion(sug_id))
    assert success_apply is True
    
    # Проверяем статус в БД после применения
    conn = sqlite3.connect(temp_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT auto_applied FROM calibration_params WHERE id = ?", (sug_id,))
    row = cursor.fetchone()
    assert row[0] == 1
    conn.close()

    # Проверяем историю (должна содержать 1 примененный параметр)
    history = asyncio.run(store.get_history("min_edge", last_n=5))
    assert len(history) == 1
    assert history[0].param_value == 0.06
    assert history[0].previous_value == 0.05

    # 3. Делаем откат (rollback)
    success_rollback = asyncio.run(store.rollback(sug_id))
    assert success_rollback is True

    # Проверяем историю после отката (должна содержать новую запись с откатом)
    history2 = asyncio.run(store.get_history("min_edge", last_n=5))
    # В истории должно быть 2 записи (последняя — откат к 0.05)
    assert len(history2) == 2
    assert history2[0].param_value == 0.05
    assert history2[0].previous_value == 0.06
    assert "Откат изменения" in history2[0].reason
