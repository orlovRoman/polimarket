# tests/test_dashboard_routes.py
import pytest
from aiohttp.test_utils import TestClient, TestServer
from web.dashboard import create_dashboard_app
import config
import agents.shared.python.db as db_module

@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Изолированная база данных для теста."""
    db_path = tmp_path / "test_dashboard_routes.db"
    db_path_str = str(db_path)
    
    # Патчим DB_PATH в config и db_module
    monkeypatch.setattr(config, "DB_PATH", db_path_str)
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_db_initialized", False)
    
    db_module.init_db()
    return db_path

@pytest.mark.asyncio
async def test_api_overview_returns_200(isolated_db):
    app = create_dashboard_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/overview")
        assert resp.status == 200
        data = await resp.json()
        assert "scout" in data

@pytest.mark.asyncio
async def test_api_signals_requires_strategy(isolated_db):
    app = create_dashboard_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/signals")
        assert resp.status == 400
        data = await resp.json()
        assert "error" in data

@pytest.mark.asyncio
async def test_html_routes_render_200(isolated_db):
    app = create_dashboard_app()
    async with TestClient(TestServer(app)) as client:
        for route in ["/", "/penny-stocks", "/scout", "/whale", "/corridors"]:
            resp = await client.get(route)
            assert resp.status == 200
            html = await resp.text()
            assert "<html" in html.lower()
