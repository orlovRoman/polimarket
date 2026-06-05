import os
import tempfile
import sqlite3
import asyncio
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

# Патчим DB_PATH перед всеми импортами
temp_db_fd, temp_db_path = tempfile.mkstemp(suffix=".sqlite")
os.environ["DB_PATH"] = temp_db_path

import config
config.DB_PATH = Path(temp_db_path)

from agents.shared.python.db import init_db, get_connection
from core.strategy01_worker import fetch_funding_address, update_wallet_clusters

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
        conn.execute("DELETE FROM wallets")
        conn.execute("DELETE FROM wallet_clusters")
        conn.execute("DELETE FROM trader_transactions")

@pytest.mark.asyncio
@patch("core.strategy01_worker.POLYGONSCAN_KEY", "test_api_key")
async def test_fetch_funding_address():
    # Мокаем httpx.AsyncClient
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "1",
        "message": "OK",
        "result": [
            {"from": "0xfunder1", "to": "0xproxy1", "value": "100000"},
            {"from": "0xfunder2", "to": "0xproxy1", "value": "50000"}
        ]
    }
    
    with patch("httpx.AsyncClient.get", return_value=mock_resp) as mock_get:
        funder = await fetch_funding_address("0xproxy1")
        assert funder == "0xfunder1"
        mock_get.assert_called_once()
        # Проверяем, что параметры запроса верные
        params = mock_get.call_args[1]["params"]
        assert params["address"] == "0xproxy1"
        assert params["apikey"] == "test_api_key"

@pytest.mark.asyncio
@patch("core.strategy01_worker.POLYGONSCAN_KEY", "")
async def test_fetch_funding_address_no_key():
    # Без ключа должен возвращать None сразу
    funder = await fetch_funding_address("0xproxy1")
    assert funder is None

@pytest.mark.asyncio
@patch("core.strategy01_worker.fetch_funding_address")
async def test_update_wallet_clusters_creates_cluster(mock_fetch_funding):
    # Добавляем 3 активных кошелька в таблицу wallets
    with get_connection() as conn:
        # last_seen должен быть в пределах последних 24 часов
        import datetime
        now_str = datetime.datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO wallets (address, alias, win_rate, last_seen) VALUES (?, ?, ?, ?)",
            ("0xwallet1", "W1", 0.8, now_str)
        )
        conn.execute(
            "INSERT INTO wallets (address, alias, win_rate, last_seen) VALUES (?, ?, ?, ?)",
            ("0xwallet2", "W2", 0.7, now_str)
        )
        conn.execute(
            "INSERT INTO wallets (address, alias, win_rate, last_seen) VALUES (?, ?, ?, ?)",
            ("0xwallet3", "W3", 0.6, now_str)
        )
    
    # 0xwallet1 и 0xwallet2 финансируются от 0xfunder_common
    # 0xwallet3 финансируется от 0xfunder_unique
    def side_effect(addr):
        if addr in ("0xwallet1", "0xwallet2"):
            return "0xfunder_common"
        if addr == "0xwallet3":
            return "0xfunder_unique"
        return None
        
    mock_fetch_funding.side_effect = side_effect
    
    # Запускаем воркер
    inserted = await update_wallet_clusters()
    
    # Ожидаем, что обновилось 2 записи (0xwallet1 и 0xwallet2 входят в один кластер, а 0xwallet3 отброшен, т.к. размер группы < 2)
    assert inserted == 2
    
    # Проверяем записи в БД
    with get_connection() as conn:
        clusters = conn.execute("SELECT * FROM wallet_clusters").fetchall()
        assert len(clusters) == 2
        
        c1 = dict(clusters[0])
        c2 = dict(clusters[1])
        
        # Они должны иметь одинаковый cluster_id и funding_addr
        assert c1["cluster_id"] == c2["cluster_id"]
        assert c1["funding_addr"] == "0xfunder_common"
        assert c2["funding_addr"] == "0xfunder_common"
        
        addresses = {c1["address"], c2["address"]}
        assert addresses == {"0xwallet1", "0xwallet2"}
