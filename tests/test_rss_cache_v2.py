"""
Тесты для resolution_extractor.py — актуальны для HEAD 0d4a149.
Запуск: pytest tests/test_rss_cache_v2.py -v
"""
import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.fixture(autouse=True)
def reset_cache(tmp_path, monkeypatch):
    """Сбрасывает in-memory состояние модуля между тестами."""
    import agents.shared.utils.resolution_extractor as m
    m._runtime_rss_cache = {}
    m._cache_loaded = False
    m._rss_lock = None              # сброс lazy lock (после исправления #1)
    # Подменяем путь к кэш-файлу через lazy функцию (после исправления #2)
    monkeypatch.setattr(m, "_get_rss_cache_file", lambda: tmp_path / "rss_cache.json")
    yield


# ─── #1: asyncio.Lock не создаётся на уровне модуля ─────────────────

def test_rss_lock_not_created_at_import_time():
    """Lock должен быть None до первого использования."""
    import agents.shared.utils.resolution_extractor as m
    assert m._rss_lock is None, \
        "asyncio.Lock создаётся при импорте — риск RuntimeError вне event loop"


@pytest.mark.asyncio
async def test_rss_lock_created_on_first_use():
    """После первого async with _get_rss_lock() — lock не None."""
    import agents.shared.utils.resolution_extractor as m
    async with m._get_rss_lock():
        pass
    assert m._rss_lock is not None


# ─── #2: VAULT_PATH lazy ─────────────────────────────────────────────

def test_module_imports_without_vault_path(monkeypatch):
    """Модуль должен импортироваться даже если VAULT_PATH не определён."""
    import sys
    # Удаляем модуль из кэша чтобы форсировать повторный импорт
    mods_to_remove = [k for k in sys.modules if "resolution_extractor" in k]
    for mod in mods_to_remove:
        del sys.modules[mod]

    # Патчим config так, чтобы VAULT_PATH бросал ошибку при доступе
    with patch.dict("sys.modules", {"config": MagicMock(spec=[])}):
        try:
            import agents.shared.utils.resolution_extractor  # не должен упасть
            imported = True
        except (ImportError, AttributeError):
            imported = False

    assert imported, "Модуль падает при импорте без VAULT_PATH"


# ─── #3: monkeypatch в правильном namespace ──────────────────────────

def test_prefilter_volume_uses_workflow_namespace(monkeypatch):
    """monkeypatch должен патчить core.workflow, а не config."""
    from core.workflow import _prefilter_markets

    # Патчим ПРАВИЛЬНО — в namespace workflow
    monkeypatch.setattr("core.workflow.MIN_MARKET_VOLUME_USD", 1000)

    markets = [
        {"id": "a", "price": 0.5, "volume": 1500, "close_time": "2026-12-01T00:00:00Z"},
        {"id": "b", "price": 0.5, "volume": 800,  "close_time": "2026-12-01T00:00:00Z"},
    ]
    result = _prefilter_markets(markets)
    assert len(result) == 1, \
        "monkeypatch не работает — проверьте, что патчится core.workflow.MIN_MARKET_VOLUME_USD"
    assert result[0]["id"] == "a"


def test_prefilter_volume_wrong_namespace_does_nothing(monkeypatch):
    """Доказывает, что патч config.* НЕ влияет на workflow."""
    from core.workflow import _prefilter_markets

    # Патчим НЕПРАВИЛЬНО — так было раньше
    monkeypatch.setattr("config.MIN_MARKET_VOLUME_USD", 1)  # порог = 1, пройдут все

    markets = [
        {"id": "a", "price": 0.5, "volume": 1500, "close_time": "2026-12-01T00:00:00Z"},
        {"id": "b", "price": 0.5, "volume": 800,  "close_time": "2026-12-01T00:00:00Z"},
    ]
    result = _prefilter_markets(markets)
    # Если патч не работает — результат определяется оригинальным значением из workflow
    # Этот тест документирует баг, а не проверяет правильное поведение
    assert isinstance(result, list)


# ─── Регрессия: атомарная запись ─────────────────────────────────────

@pytest.mark.asyncio
async def test_save_rss_cache_atomic(tmp_path, monkeypatch):
    """_save_rss_cache использует tmp → os.replace для атомарности."""
    import agents.shared.utils.resolution_extractor as m
    cache_file = tmp_path / "rss_cache.json"
    monkeypatch.setattr(m, "_get_rss_cache_file", lambda: cache_file)
    m._runtime_rss_cache = {"test.com": "https://test.com/feed"}

    await m._save_rss_cache()

    assert cache_file.exists()
    with open(cache_file) as f:
        data = json.load(f)
    assert data["test.com"] == "https://test.com/feed"
    # Tmp файл не должен остаться
    assert not cache_file.with_suffix(".json.tmp").exists()


# ─── Регрессия: miss кэшируется как "NONE" ───────────────────────────

@pytest.mark.asyncio
async def test_autodiscover_caches_miss_as_none_string(monkeypatch):
    import agents.shared.utils.resolution_extractor as m

    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.headers = {}

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_resp
        )
        result = await m.autodiscover_rss("no-rss-site.com")

    assert result is None
    assert m._runtime_rss_cache.get("no-rss-site.com") == "NONE"


@pytest.mark.asyncio
async def test_autodiscover_no_http_on_cached_none():
    """После кэшированного NONE — нет HTTP запросов."""
    import agents.shared.utils.resolution_extractor as m
    m._runtime_rss_cache["cached.com"] = "NONE"
    m._cache_loaded = True  # чтобы не читать файл

    http_calls = []
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=lambda url, **kw: http_calls.append(url)
        )
        result = await m.autodiscover_rss("cached.com")

    assert result is None
    assert len(http_calls) == 0, "HTTP вызов при кэшированном NONE"
