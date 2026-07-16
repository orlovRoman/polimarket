import os
import tempfile
import sqlite3
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Патчим DB_PATH перед всеми импортами
temp_db_fd, temp_db_path = tempfile.mkstemp(suffix=".sqlite")
os.environ["DB_PATH"] = temp_db_path

import config
config.DB_PATH = Path(temp_db_path)

from agents.shared.python.db import init_db
from core.eval.signal_logger import StrategyType
from core.eval.metrics_calculator import StrategyMetrics
from core.eval.threshold_calibrator import CalibrationSuggestion
from core.eval.evaluation_engine import EvaluationEngine

@pytest.fixture(scope="module", autouse=True)
def setup_database():
    import agents.shared.python.db as db_module
    import core.eval.signal_logger as sl_module
    import core.eval.calibration_store as cs_module
    import core.eval.metrics_repository as mr_module
    db_module.DB_PATH = Path(temp_db_path)
    sl_module.DB_PATH = Path(temp_db_path)
    cs_module.DB_PATH = Path(temp_db_path)
    mr_module.DB_PATH = Path(temp_db_path)
    db_module._db_initialized = False
    init_db()
    yield
    try:
        os.close(temp_db_fd)
        os.remove(temp_db_path)
    except Exception:
        pass

@pytest.mark.anyio
async def test_evaluation_engine_empty_db():
    engine = EvaluationEngine()
    
    # Патчим метод _notify_telegram, чтобы не отправлять сообщения в Telegram во время тестов
    with patch.object(engine, "_notify_telegram", new_callable=AsyncMock) as mock_notify:
        report = await engine.run_full_evaluation()
        
        assert report is not None
        assert len(report.results) == 3  # Осталось 3 стратегии
        for res in report.results.values():
            assert res.metrics is None
            assert len(res.suggestions) == 0

@pytest.mark.anyio
@patch.dict(os.environ, {"EVAL_AUTO_APPLY_ENABLED": "True"})
async def test_evaluation_engine_auto_apply_logic():
    engine = EvaluationEngine()
    
    # 1. Готовим фиктивные данные метрик, проходящих все 3 условия автоприменения:
    # - Confidence >= 0.85 (выдадим 0.90)
    # - Signals >= 100 (выдадим 120)
    # - Изменение <= 10% (выдадим с 0.05 на 0.054 -> +8%)
    good_suggestion = CalibrationSuggestion(
        param_name="min_edge",
        current_value=0.050,
        suggested_value=0.054,
        confidence=0.90,
        reason="Good suggestion",
        supporting_signals_count=120
    )
    
    mock_metrics = StrategyMetrics(
        total_signals=120,
        resolved_signals=120,
        profitable_signals=30,
        win_rate=0.25,
        avg_edge=0.05,
        avg_realized_pnl=-5.0,
        brier_score=0.18,
        calibration_error=0.05
    )
    
    with patch.object(engine, "_notify_telegram", new_callable=AsyncMock), \
         patch.object(engine.metrics_repository, "compute_and_store_metrics", return_value=mock_metrics), \
         patch.object(engine.calibrator, "suggest_edge_threshold", return_value=good_suggestion), \
         patch.object(engine.calibration_store, "save_suggestion", new_callable=AsyncMock) as mock_save:
         
         # Запускаем оценку только для SCOUT (другие вернем пустые)
         async def mock_compute(strategy, period):
             if strategy == StrategyType.SCOUT:
                 return mock_metrics
             return None
             
         engine.metrics_repository.compute_and_store_metrics = mock_compute
         
         await engine.run_full_evaluation()
         
         # Проверяем, что save_suggestion был вызван с auto_apply=True
         mock_save.assert_called_once()
         args, kwargs = mock_save.call_args
         assert kwargs["auto_apply"] is True
