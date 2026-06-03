import os
import tempfile
import sqlite3
import json
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

# Патчим DB_PATH перед всеми импортами
temp_db_fd, temp_db_path = tempfile.mkstemp(suffix=".sqlite")
os.environ["DB_PATH"] = temp_db_path

import config
config.DB_PATH = Path(temp_db_path)

from agents.shared.python.db import init_db
from core.eval.signal_logger import SignalLogger, StrategyType
from services.resolution_fetcher import ResolutionFetcher

@pytest.fixture(scope="module", autouse=True)
def setup_database():
    import agents.shared.python.db as db_module
    import core.eval.signal_logger as sl_module
    import services.resolution_fetcher as rf_module
    db_module.DB_PATH = Path(temp_db_path)
    sl_module.DB_PATH = Path(temp_db_path)
    rf_module.DB_PATH = Path(temp_db_path)
    db_module._db_initialized = False
    init_db()
    yield
    try:
        os.close(temp_db_fd)
        os.remove(temp_db_path)
    except Exception:
        pass

def test_fetch_pending_resolutions():
    # Подготавливаем базу данных: вставляем рынки и сигналы
    conn = sqlite3.connect(temp_db_path)
    cursor = conn.cursor()
    
    # Рынок 1: закрытый, сигнал создан 2 дня назад (должен провериться)
    cursor.execute("""
        INSERT INTO markets (id, platform, title, url, outcome, price, close_time)
        VALUES ('market-resolved', 'polymarket', 'Market Resolved', 'url', 'None', 0.5, '2026-06-03T12:00:00Z')
    """)
    cursor.execute("""
        INSERT INTO signals (id, type, market_id, platform, edge, confidence, priority, summary, details, status, created_at, strategy_type)
        VALUES ('sig-resolved', 'SCOUT', 'market-resolved', 'polymarket', 0.1, 0.6, 'medium', 'summary', '{"target_outcome": "YES"}', 'PENDING', ?, 'scout')
    """, ((datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),))

    # Рынок 2: свежий сигнал, создан 2 часа назад (НЕ должен провериться из-за фильтра 24ч)
    cursor.execute("""
        INSERT INTO markets (id, platform, title, url, outcome, price, close_time)
        VALUES ('market-fresh', 'polymarket', 'Market Fresh', 'url', 'None', 0.5, '2026-06-03T20:00:00Z')
    """)
    cursor.execute("""
        INSERT INTO signals (id, type, market_id, platform, edge, confidence, priority, summary, details, status, created_at, strategy_type)
        VALUES ('sig-fresh', 'SCOUT', 'market-fresh', 'polymarket', 0.1, 0.6, 'medium', 'summary', '{"target_outcome": "YES"}', 'PENDING', ?, 'scout')
    """, ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),))

    conn.commit()
    conn.close()

    # Оборачиваем патч httpx в контекстный менеджер
    with patch("httpx.AsyncClient.get") as mock_get:
        async def mock_get_coro(url, *args, **kwargs):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "closed": True,
                "outcomePrices": '["1", "0"]',  # YES выиграл
                "closedTime": "2026-06-03T12:00:00Z"
            }
            return mock_response
        
        mock_get.side_effect = mock_get_coro

        fetcher = ResolutionFetcher()
        updated = asyncio.run(fetcher.fetch_pending_resolutions())
        
        # Должен быть обновлен ровно 1 сигнал (sig-resolved)
        assert updated == 1

    # Проверяем изменения в БД
    conn = sqlite3.connect(temp_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Проверяем первый сигнал
    cursor.execute("SELECT status, was_profitable, resolution_outcome FROM signals WHERE id = 'sig-resolved'")
    sig_res = cursor.fetchone()
    assert sig_res["status"] == "WIN"
    assert sig_res["was_profitable"] == 1
    assert sig_res["resolution_outcome"] == "YES"

    # Проверяем исход в таблице markets
    cursor.execute("SELECT outcome FROM markets WHERE id = 'market-resolved'")
    m_res = cursor.fetchone()
    assert m_res["outcome"] == "YES"

    # Свежий сигнал должен остаться PENDING
    cursor.execute("SELECT status FROM signals WHERE id = 'sig-fresh'")
    fresh_res = cursor.fetchone()
    assert fresh_res["status"] == "PENDING"

    conn.close()
