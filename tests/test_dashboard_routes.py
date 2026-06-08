# tests/test_dashboard_routes.py
import pytest
from aiohttp.test_utils import TestClient, TestServer
from web.dashboard import create_dashboard_app
import config
import agents.shared.python.db as db_module

@pytest.fixture(autouse=False)
def isolated_db(tmp_path, monkeypatch):
    """Изолированная база данных для теста."""
    db_path = tmp_path / "test_dashboard_routes.db"
    db_path_str = str(db_path)
    
    # Патчим DB_PATH в config и db_module (оба объектом Path)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_db_initialized", False)
    
    db_module.init_db()
    yield db_path
    db_module._db_initialized = False

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

@pytest.mark.asyncio
async def test_api_buy_sell_routes(isolated_db):
    # Создаем тестовую запись
    with db_module.get_connection() as conn:
        conn.execute("""
            INSERT INTO penny_stocks_monitoring (market_id, title, url, initial_price, current_price, max_price_seen, min_price_seen, status, predicted_outcome)
            VALUES ('penny_api', 'Penny API', 'http://api', 0.04, 0.05, 0.05, 0.04, 'ACTIVE', 'YES')
        """)
        
    app = create_dashboard_app()
    async with TestClient(TestServer(app)) as client:
        # Покупаем
        resp = await client.post("/api/penny-stocks/buy", json={"market_id": "penny_api", "price": 0.04})
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        
        # Проверяем, что в БД записалось
        rows = db_module.get_active_penny_stocks()
        item = next(r for r in rows if r['market_id'] == 'penny_api')
        assert item['virtual_bought_price'] == 0.04
        
        # Продаем
        resp = await client.post("/api/penny-stocks/sell", json={"market_id": "penny_api"})
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        
        # Проверяем сброс в БД
        rows = db_module.get_active_penny_stocks()
        item = next(r for r in rows if r['market_id'] == 'penny_api')
        assert item['virtual_bought_price'] is None
