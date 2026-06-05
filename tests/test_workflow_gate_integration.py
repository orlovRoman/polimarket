import os
import tempfile
import sqlite3
import asyncio
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone

# Патчим DB_PATH перед всеми импортами
temp_db_fd, temp_db_path = tempfile.mkstemp(suffix=".sqlite")
os.environ["DB_PATH"] = temp_db_path

import config
config.DB_PATH = Path(temp_db_path)

from agents.shared.python.db import init_db, get_connection
from core.workflow import run_agent_evaluation
from core.models import Market
from core.onchain_gate import GateResult
from core.context import SmartMoneySummary

@pytest.fixture(scope="module", autouse=True)
def setup_database():
    import agents.shared.python.db as db_module
    db_module.DB_PATH = Path(temp_db_path)
    db_module._db_initialized = False
    init_db()
    yield
    try:
        os.close(temp_db_fd)
        os.remove(temp_db_path)
    except Exception:
        pass

@pytest.fixture(autouse=True)
def clean_tables():
    with get_connection() as conn:
        conn.execute("DELETE FROM gate_metrics")
        conn.execute("DELETE FROM signals")

@pytest.mark.asyncio
async def test_workflow_gate_blocks_evaluation():
    m = Market(
        id="test_market_blocked",
        platform="polymarket",
        title="Will it rain tomorrow?",
        description="Rain tomorrow",
        url="http://test",
        outcome="YES",
        price=0.5,
        close_time=datetime.now()
    )
    
    scout = MagicMock()
    scout.api_key = "test"
    scout.model = "gemini-2.5-flash"
    scout.estimate_market = AsyncMock()
    
    swing = MagicMock()
    swing.api_key = "test"
    swing.model = "gemini-2.5-flash"
    swing.estimate_market = AsyncMock()
    
    update_state = MagicMock()

    # Замокаем check_onchain_gate так, чтобы он заблокировал
    gate_mock_res = GateResult(allow=False, reason="Low volume", blocked_by="volume")
    
    # Мокаем обнаружение аномалии (has_anomaly = False) и другие зависимости, чтобы быстро пройти
    mock_velocity = MagicMock()
    mock_velocity.has_anomaly = False
    
    mock_sm = SmartMoneySummary(
        available=True,
        total_yes_usd=0.0,
        total_no_usd=0.0,
        yes_dominance=0.5,
        top_wallets=[],
        summary="No data",
        whale_count=0,
        wallets_list=[]
    )
    
    with patch("core.onchain_gate.check_onchain_gate", return_value=gate_mock_res), \
         patch("core.smart_money.fetch_smart_money_sync", return_value=mock_sm), \
         patch("core.price_velocity.detect_velocity_anomaly", return_value=mock_velocity), \
         patch("core.workflow.fetch_rss_news", return_value=[]), \
         patch("core.workflow.fetch_reddit_news", return_value=[]), \
         patch("core.workflow.fetch_google_trends", return_value=""), \
         patch("core.workflow.get_memory", return_value=None), \
         patch("core.workflow.save_memory"), \
         patch("config.llm_health_gate.check_availability"):
         
        sig, swing_sig, ctx = await run_agent_evaluation(m, scout, swing, update_state)
        
        # Должен быть ранний возврат
        assert sig is None
        assert swing_sig is None
        assert ctx is None
        
        # Оценка не должна запускаться
        assert not scout.estimate_market.called
        
        # Проверяем, что в БД записаны метрики блокировки
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM gate_metrics").fetchall()
            assert len(rows) == 1
            row = dict(rows[0])
            assert row["total"] == 1
            assert row["passed"] == 0
            assert row["blocked_no_volume"] == 1
            assert row["blocked_no_whales"] == 0

@pytest.mark.asyncio
async def test_workflow_gate_allows_evaluation():
    m = Market(
        id="test_market_passed",
        platform="polymarket",
        title="Will Bitcoin hit 100k?",
        description="Bitcoin 100k",
        url="http://test",
        outcome="YES",
        price=0.5,
        close_time=datetime.now()
    )
    
    scout_signal = MagicMock()
    scout_signal.edge = 0.6
    
    scout = MagicMock()
    scout.api_key = "test"
    scout.model = "gemini-2.5-flash"
    scout.estimate_market = AsyncMock(return_value=scout_signal)
    
    swing_signal = MagicMock()
    
    swing = MagicMock()
    swing.api_key = "test"
    swing.model = "gemini-2.5-flash"
    swing.estimate_market = AsyncMock(return_value=swing_signal)
    
    update_state = MagicMock()

    # Замокаем check_onchain_gate так, чтобы он пропустил
    gate_mock_res = GateResult(allow=True, reason="Volume ok, whale ok", blocked_by="pass")
    
    # Мокаем обнаружение аномалии (has_anomaly = False) и другие зависимости
    mock_velocity = MagicMock()
    mock_velocity.has_anomaly = False
    mock_velocity.suspicion = "NOISE"
    
    mock_sm = SmartMoneySummary(
        available=True,
        total_yes_usd=1000.0,
        total_no_usd=500.0,
        yes_dominance=0.67,
        top_wallets=[],
        summary="Some summary",
        whale_count=1,
        wallets_list=[]
    )
    
    with patch("core.onchain_gate.check_onchain_gate", return_value=gate_mock_res), \
         patch("core.smart_money.fetch_smart_money_sync", return_value=mock_sm), \
         patch("core.price_velocity.detect_velocity_anomaly", return_value=mock_velocity), \
         patch("core.workflow.fetch_rss_news", return_value=[]), \
         patch("core.workflow.fetch_reddit_news", return_value=[]), \
         patch("core.workflow.fetch_google_trends", return_value=""), \
         patch("core.workflow._fetch_grounded_context", return_value="Context"), \
         patch("core.workflow.get_memory", return_value=None), \
         patch("core.workflow.save_memory"), \
         patch("config.llm_health_gate.check_availability"), \
         patch("core.checkpoint.save_checkpoint"):
         
        sig, swing_sig, ctx = await run_agent_evaluation(m, scout, swing, update_state)
        
        # Должны получить сигналы
        assert sig == scout_signal
        assert swing_sig == swing_signal
        
        # Оценка должна быть вызвана
        assert scout.estimate_market.called
        
        # Проверяем, что в БД записаны метрики прохода
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM gate_metrics").fetchall()
            assert len(rows) == 1
            row = dict(rows[0])
            assert row["total"] == 1
            assert row["passed"] == 1
            assert row["blocked_no_volume"] == 0
            assert row["blocked_no_whales"] == 0
