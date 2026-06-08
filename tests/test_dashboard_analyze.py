# tests/test_dashboard_analyze.py
import pytest
import asyncio
from unittest.mock import patch, MagicMock
from aiohttp.test_utils import TestClient, TestServer
from web.dashboard import create_dashboard_app, analysis_jobs
import config
import agents.shared.python.db as db_module

@pytest.fixture(autouse=False)
def isolated_db(tmp_path, monkeypatch):
    """Изолированная база данных для теста."""
    db_path = tmp_path / "test_dashboard_analyze.db"
    db_path_str = str(db_path)
    
    # Патчим DB_PATH в config и db_module
    monkeypatch.setattr(config, "DB_PATH", db_path_str)
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_db_initialized", False)
    
    db_module.init_db()
    # Очищаем кэш перед тестом
    analysis_jobs.clear()
    yield db_path
    db_module._db_initialized = False
    analysis_jobs.clear()

@pytest.mark.asyncio
async def test_api_analyze_existing_opinions(isolated_db):
    # Записываем мнения в БД
    db_module.add_discussion_message("test_mkt", "SCOUT", "Scout opinion here", 0.9, True)
    db_module.add_discussion_message("test_mkt", "SWING", "Swing opinion here", 0.8, True)
    
    app = create_dashboard_app()
    async with TestClient(TestServer(app)) as client:
        # Должен вернуть completed и подтянуть мнения
        resp = await client.post("/api/penny-stocks/analyze", json={"market_id": "test_mkt"})
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "completed"
        assert len(data["opinions"]) == 2
        assert data["opinions"][0]["agent_name"] == "SCOUT"

@pytest.mark.asyncio
@patch("web.dashboard.run_analysis_in_background")
async def test_api_analyze_starts_task(mock_run_bg, isolated_db):
    app = create_dashboard_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/penny-stocks/analyze", json={"market_id": "new_mkt"})
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "running"
        assert "progress" in data
        mock_run_bg.assert_called_once_with("new_mkt")

@pytest.mark.asyncio
async def test_api_analyze_status_not_found(isolated_db):
    app = create_dashboard_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/penny-stocks/analyze-status?market_id=nonexistent")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "not_found"

@pytest.mark.asyncio
@patch("web.dashboard.run_analysis_in_background")
async def test_api_analyze_force_clears_db(mock_run_bg, isolated_db):
    # Сначала записываем мнения в БД
    db_module.add_discussion_message("test_force", "SCOUT", "Old opinion", 0.9, True)
    db_module.mark_market_analyzed("test_force", 0.05)
    
    app = create_dashboard_app()
    async with TestClient(TestServer(app)) as client:
        # Делаем обычный запрос — мнения должны быть
        resp = await client.post("/api/penny-stocks/analyze", json={"market_id": "test_force"})
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "completed"
        
        # Делаем принудительный запрос force=true — кэш и БД должны очиститься, запустится таск
        resp2 = await client.post("/api/penny-stocks/analyze", json={"market_id": "test_force", "force": True})
        assert resp2.status == 200
        data2 = await resp2.json()
        assert data2["status"] == "running"
        mock_run_bg.assert_called_once_with("test_force")
        
        # Проверяем, что в БД действительно стерлось
        opinions = db_module.get_market_discussions("test_force")
        assert len(opinions) == 0
