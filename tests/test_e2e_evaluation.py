"""
Сквозной (E2E) интеграционный тест для полного цикла Evaluation Engine:
сигналы -> резолюция -> расчет метрик -> калибровка порогов -> применение -> Live-Reload -> откат.
"""
import pytest
import os
import sqlite3
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from core.eval.signal_logger import SignalLogger, StrategyType
from core.eval.evaluation_engine import EvaluationEngine
from core.eval.calibration_store import CalibrationStore
from core.config_provider import ConfigProvider
import config

@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    """
    Фикстура для изоляции базы данных в тестах.
    """
    db_file = tmp_path / "test_eval_e2e.sqlite"
    old_db_path = config.DB_PATH
    config.DB_PATH = db_file
    
    import agents.shared.python.db as db_module
    import core.eval.signal_logger as sl_module
    import core.eval.calibration_store as cs_module
    import core.eval.metrics_repository as mr_module
    
    db_module.DB_PATH = db_file
    sl_module.DB_PATH = db_file
    cs_module.DB_PATH = db_file
    mr_module.DB_PATH = db_file
    
    db_module._db_initialized = False
    db_module.init_db()
    
    yield db_file
    
    config.DB_PATH = old_db_path
    db_module._db_initialized = False

@pytest.mark.anyio
async def test_e2e_evaluation_cycle():
    # Убеждаемся, что кэш сброшен
    ConfigProvider.invalidate_cache()
    
    # 1. Генерируем тестовую историю сигналов за последние 15 дней для SCOUT
    # Создадим 60 сигналов.
    # 55 сигналов -> предсказано YES (85%), исход YES (реализованный pnl > 0)
    # 5 сигналов -> предсказано YES (85%), исход NO (реализованный pnl < 0)
    # Итого win_rate = 55 / 60 = 91.6%
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()
    
    now = datetime.now(timezone.utc)
    
    for i in range(60):
        sig_id = f"test_sig_{i}"
        market_id = f"mkt_{i}"
        created_at = (now - timedelta(days=5, hours=i)).isoformat()
        
        # Записываем в таблицу signals
        # Для 55 сигналов исход YES (1.0), для 5 - NO (0.0)
        final_outcome = "YES" if i < 55 else "NO"
        status = "WIN" if i < 55 else "LOSS"
        # Для YES реализованный PnL = +$2.0, для NO = -$10.0
        realized_pnl = 2.0 if i < 55 else -10.0
        was_profitable = 1 if i < 55 else 0
        
        cursor.execute("""
            INSERT INTO signals (
                id, type, market_id, platform, edge, edge_at_signal, confidence, predicted_probability, 
                market_price_at_signal, created_at, resolved_at, status, resolution_outcome, pnl_realized, strategy_type, was_profitable,
                priority, summary, details
            ) VALUES (?, 'MISPRICING', ?, 'polymarket', 0.12, 0.12, 0.85, 0.85, 0.68, ?, ?, ?, ?, ?, 'scout', ?, 'medium', 'fake summary', '{}')
        """, (sig_id, market_id, created_at, created_at, status, final_outcome, realized_pnl, was_profitable))
        
    conn.commit()
    conn.close()
    
    # 2. Проверяем дефолтное значение min_edge
    min_edge_default = ConfigProvider.get_min_edge_sync("scout")
    assert min_edge_default == 0.05
    
    # 3. Запускаем Evaluation Engine
    # Отключаем автоотправку уведомлений в Telegram, так как бота нет
    engine = EvaluationEngine()
    
    with patch.object(engine, "_notify_telegram", new_callable=AsyncMock) as mock_notify:
        report = await engine.run_full_evaluation(period_days=30)
        mock_notify.assert_called_once()
        
    # Проверяем метрики в отчете
    assert StrategyType.SCOUT.value in report.results
    scout_res = report.results[StrategyType.SCOUT.value]
    
    assert scout_res.metrics is not None
    assert scout_res.metrics.total_signals == 60
    assert scout_res.metrics.resolved_signals == 60
    assert scout_res.metrics.win_rate == pytest.approx(0.916, abs=0.01)
    
    # Проверяем, что калибратор выработал предложения
    assert len(scout_res.suggestions) > 0
    sug = scout_res.suggestions[0]
    assert sug.param_name == "min_edge"
    
    # Проверяем, что предложение сохранено в CalibrationStore
    store = CalibrationStore()
    history = await store.get_strategy_history("scout", 10)
    assert len(history) == 1
    
    record = history[0]
    assert record.param_name == "min_edge"
    assert record.auto_applied is False  # По умолчанию автоприменение выключено
    
    # 4. Применяем предложение вручную
    sug_id = record.id
    success = await store.apply_suggestion(sug_id)
    assert success is True
    
    # 5. Проверяем Live-Reload через ConfigProvider
    new_min_edge = ConfigProvider.get_min_edge_sync("scout")
    assert new_min_edge == record.param_value
    assert new_min_edge != 0.05
    
    # 6. Откатываем калибровку
    success_rollback = await store.rollback(sug_id)
    assert success_rollback is True
    
    # Проверяем, что значение откатилось к 0.05
    rolled_min_edge = ConfigProvider.get_min_edge_sync("scout")
    assert rolled_min_edge == 0.05
