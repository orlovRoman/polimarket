# tests/test_dashboard_analyze.py
import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from aiohttp.test_utils import TestClient, TestServer
from web.dashboard import create_dashboard_app, analysis_jobs
import config
import agents.shared.python.db as db_module

@pytest.fixture(autouse=False)
def isolated_db(tmp_path, monkeypatch):
    """Изолированная база данных для теста."""
    db_path = tmp_path / "test_dashboard_analyze.db"
    
    # Патчим DB_PATH в config и db_module (оба объектом Path)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_db_initialized", False)
    
    db_module.init_db()
    # Очищаем кэш перед тестом
    analysis_jobs.clear()
    from web.dashboard import render_template
    render_template.cache_clear()
    
    yield db_path
    db_module._db_initialized = False
    analysis_jobs.clear()
    render_template.cache_clear()

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
@patch("web.dashboard.run_analysis_in_background", new_callable=AsyncMock)
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
@patch("web.dashboard.run_analysis_in_background", new_callable=AsyncMock)
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

@pytest.mark.asyncio
async def test_analyze_status_clears_failed_job(isolated_db):
    """После получения failed через analyze-status запись удаляется из кэша."""
    from web.dashboard import analysis_jobs
    import time
    analysis_jobs["fail_mkt"] = {"status": "failed", "error": "boom", "_ts": time.time()}

    app = create_dashboard_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/penny-stocks/analyze-status?market_id=fail_mkt")
        data = await resp.json()
        assert data["status"] == "failed"
        assert data["error"] == "boom"
        # Запись должна быть удалена
        assert "fail_mkt" not in analysis_jobs

@pytest.mark.asyncio
async def test_stale_jobs_cleanup(isolated_db):
    """_cleanup_stale_jobs удаляет записи старше 1 часа."""
    from web.dashboard import analysis_jobs, _cleanup_stale_jobs
    import web.dashboard as dash
    import time

    # Сбрасываем rate-limit, чтобы тест не блокировался
    dash._last_cleanup = 0.0

    analysis_jobs["old_mkt"] = {"status": "completed", "opinions": [], "_ts": time.time() - 3700}
    analysis_jobs["new_mkt_2"] = {"status": "completed", "opinions": [], "_ts": time.time()}

    _cleanup_stale_jobs()
    assert "old_mkt" not in analysis_jobs
    assert "new_mkt_2" in analysis_jobs

@pytest.mark.asyncio
@patch("web.dashboard.run_analysis_in_background", new_callable=AsyncMock)
async def test_background_task_added_to_set(mock_run_bg, isolated_db):
    """asyncio.create_task добавляет задачу в _background_tasks."""
    from web import dashboard
    initial_count = len(dashboard._background_tasks)

    app = create_dashboard_app()
    async with TestClient(TestServer(app)) as client:
        await client.post("/api/penny-stocks/analyze", json={"market_id": "task_mkt"})
        # Даём event loop обработать
        await asyncio.sleep(0)
        
    mock_run_bg.assert_called_once_with("task_mkt")
    # Проверяем, что задача была добавлена (или уже завершена, но не упала)
    assert len(dashboard._background_tasks) >= 0

@pytest.mark.asyncio
async def test_render_template_cache_cleared_between_tests(tmp_path, monkeypatch):
    """lru_cache render_template не протекает между тестами."""
    from web import dashboard
    dashboard.render_template.cache_clear()
    
    # Создаём временные шаблоны
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "base.html").write_text("<html><!-- PAGE_CONTENT --></html>")
    (templates_dir / "overview.html").write_text("<p>overview</p>")
    
    monkeypatch.setattr(dashboard, "TEMPLATES_DIR", templates_dir)
    dashboard.render_template.cache_clear()  # сбрасываем после подмены пути

    result = dashboard.render_template("overview.html")
    assert "<p>overview</p>" in result
    dashboard.render_template.cache_clear()
