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
        assert item['virtual_bought_price'] == pytest.approx(0.04)
        
        # Продаем
        resp = await client.post("/api/penny-stocks/sell", json={"market_id": "penny_api"})
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        
        # Проверяем сброс в БД
        rows = db_module.get_active_penny_stocks()
        item = next(r for r in rows if r['market_id'] == 'penny_api')
        assert item['virtual_bought_price'] is None

# ============================================================
# Новые тесты (Итерация 17 / dashboard fixes)
# ============================================================

@pytest.mark.asyncio
async def test_api_equity_curve_invalid_days(isolated_db):
    """Негативные и невалидные значения days должны возвращать 200 без падения."""
    app = create_dashboard_app()
    async with TestClient(TestServer(app)) as client:
        # days=-1 → должен клэмпиться до 1
        resp = await client.get("/api/equity-curve?strategy=scout&days=-1")
        assert resp.status == 200
        data = await resp.json()
        assert isinstance(data, list)

        # days=abc → fallback 30
        resp2 = await client.get("/api/equity-curve?strategy=scout&days=abc")
        assert resp2.status == 200
        assert isinstance(await resp2.json(), list)

        # days=99999 → должен клэмпиться до 365
        resp3 = await client.get("/api/equity-curve?strategy=scout&days=99999")
        assert resp3.status == 200
        assert isinstance(await resp3.json(), list)


@pytest.mark.asyncio
async def test_api_analyze_force(isolated_db):
    """force=True должен сбрасывать кэш анализа, статус не должен быть completed со старым мнением."""
    import time
    from web import dashboard

    # Имитируем завершённый анализ в кэше
    dashboard.analysis_jobs["mkt_force"] = {
        "status": "completed",
        "opinions": [{"agent": "scout", "opinion": "old"}],
        "_ts": time.time()
    }

    app = create_dashboard_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/penny-stocks/analyze",
            json={"market_id": "mkt_force", "force": True}
        )
        assert resp.status == 200
        data = await resp.json()
        # После force кэш должен быть сброшен — статус running или not_found,
        # но не completed со старым мнением
        assert data["status"] in ("running", "not_found")
        job = dashboard.analysis_jobs.get("mkt_force")
        assert job is None or job["status"] != "completed"

    # Чистим за собой
    dashboard.analysis_jobs.pop("mkt_force", None)


def test_cleanup_stale_jobs_rate_limited():
    """_cleanup_stale_jobs не должен чистить словарь при каждом вызове (rate-limit 60 сек)."""
    import web.dashboard as dash
    from web.dashboard import _cleanup_stale_jobs, analysis_jobs, _jobs_lock

    # Сбрасываем таймер чтобы первый вызов обязательно выполнил очистку
    dash._last_cleanup = 0.0

    # Добавляем устаревший job
    with _jobs_lock:
        analysis_jobs["stale_mkt"] = {"status": "running", "_ts": 0.0}

    # Первый вызов — должен очистить (т.к. _last_cleanup=0)
    _cleanup_stale_jobs()
    with _jobs_lock:
        assert "stale_mkt" not in analysis_jobs, "Первый вызов должен удалить устаревший job"

    # Добавляем снова и сразу вызываем — не должен очищать (rate-limit)
    with _jobs_lock:
        analysis_jobs["stale_mkt2"] = {"status": "running", "_ts": 0.0}
    _cleanup_stale_jobs()   # должен пропустить — last_cleanup только что обновился
    with _jobs_lock:
        assert "stale_mkt2" in analysis_jobs, "Второй вызов должен пропустить очистку из-за rate-limit"
        # Чистим за собой
        del analysis_jobs["stale_mkt2"]


@pytest.mark.asyncio
async def test_penny_stocks_active_predicted_count(isolated_db):
    """active_predicted_count должен корректно считать только рынки с прогнозом."""
    with db_module.get_connection() as conn:
        conn.execute("""
            INSERT INTO penny_stocks_monitoring (market_id, title, url, initial_price, current_price, max_price_seen, min_price_seen, status, predicted_outcome)
            VALUES
              ('pm_yes', 'Predicted YES', 'http://y', 0.03, 0.03, 0.03, 0.03, 'ACTIVE', 'YES'),
              ('pm_no',  'Predicted NO',  'http://n', 0.97, 0.97, 0.97, 0.97, 'ACTIVE', 'NO'),
              ('pm_null','No prediction', 'http://z', 0.05, 0.05, 0.05, 0.05, 'ACTIVE', NULL)
        """)

    app = create_dashboard_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/penny-stocks")
        assert resp.status == 200
        data = await resp.json()

        stats = data["stats"]
        # Все три попадают в active (с прогнозом и без)
        assert stats["active_count"] == 3
        # Только pm_yes и pm_no имеют predicted_outcome
        assert stats["active_predicted_count"] == 2

        # Проверяем что pm_null присутствует в active с серым бейджем (cheap_outcome)
        active_ids = [r["market_id"] for r in data["active"]]
        assert "pm_null" in active_ids
        null_row = next(r for r in data["active"] if r["market_id"] == "pm_null")
        assert null_row["predicted_outcome"] is None
        assert null_row["cheap_outcome"] == "YES"  # 0.05 < 0.90
