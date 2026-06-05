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
from core.smart_money import refresh_known_whales_from_holders

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

@pytest.mark.asyncio
async def test_refresh_known_whales_from_holders_success():
    # Настраиваем mock ответы httpx для YES и NO токенов
    mock_resp_yes = MagicMock()
    mock_resp_yes.status_code = 200
    mock_resp_yes.json.return_value = [
        {"proxyWallet": "0xwhale_yes_1", "pseudonym": "pseudonym_yes_1"},
        {"proxyWallet": "0xwhale_yes_2", "pseudonym": "pseudonym_yes_2"}
    ]
    
    mock_resp_no = MagicMock()
    mock_resp_no.status_code = 200
    mock_resp_no.json.return_value = [
        {"proxyWallet": "0xwhale_no_1", "pseudonym": "pseudonym_no_1"},
        {"proxyWallet": "0xwhale_yes_1", "pseudonym": "pseudonym_yes_1_dup"} # дубликат
    ]
    
    async def mock_get(url, *args, **kwargs):
        if "tokenId=0" in url:
            return mock_resp_yes
        elif "tokenId=1" in url:
            return mock_resp_no
        return MagicMock(status_code=404)

    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        res = await refresh_known_whales_from_holders("cond_123")
        assert res == 1
        
        # Проверяем, что кошельки добавлены
        with get_connection() as conn:
            wallets = conn.execute("SELECT * FROM wallets").fetchall()
            assert len(wallets) == 3 # yes_1, yes_2, no_1 (дубликат yes_1 проигнорирован благодаря INSERT OR IGNORE)
            
            w_map = {w["address"]: dict(w) for w in wallets}
            
            assert "0xwhale_yes_1" in w_map
            assert w_map["0xwhale_yes_1"]["alias"] == "pseudonym_yes_1"
            assert w_map["0xwhale_yes_1"]["win_rate"] is None
            
            assert "0xwhale_yes_2" in w_map
            assert w_map["0xwhale_yes_2"]["alias"] == "pseudonym_yes_2"
            
            assert "0xwhale_no_1" in w_map
            assert w_map["0xwhale_no_1"]["alias"] == "pseudonym_no_1"

@pytest.mark.asyncio
async def test_refresh_known_whales_from_holders_api_error():
    # Проверяем поведение при ошибке API
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    
    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        res = await refresh_known_whales_from_holders("cond_123")
        # Должен корректно отработать и вернуть 1 (или 0 при ошибке, функция возвращает 1 если не упала, но из-за try-except возвращает 1 при успехе цикла, 0 при Exception)
        # Посмотрим код: try блок завершается циклом, после чего возвращает 1. Если API вернул 500, то holders = [], цикл просто пройдет вхолостую и вернет 1.
        assert res == 1
        
        with get_connection() as conn:
            wallets = conn.execute("SELECT * FROM wallets").fetchall()
            assert len(wallets) == 0
