import asyncio
import pytest
import httpx
from unittest.mock import MagicMock, patch, AsyncMock

# BUG-01
def test_process_consensus_no_api_key_no_name_error():
    """process_consensus не должен бросать NameError при api_key=None."""
    from core.workflow import process_consensus
    from unittest.mock import MagicMock, patch

    context = MagicMock()
    context.trigger_type = "scheduled"
    context.source_url = ""
    context.math_filter_result = None
    context.market.id = "mkt-1"
    context.market.price = 0.5
    context.market.url = "https://polymarket.com/mkt-1"
    context.market.title = "Test"

    with patch("core.workflow.save_idea_audit"), \
         patch("core.checkpoint.save_checkpoint"), \
         patch("core.checkpoint.verify_checkpoint", return_value=True), \
         patch("agents.shared.python.db.save_agent_episode"):
        # Не должно бросать NameError
        process_consensus(context, None, None, None, {}, lambda **kw: None, None)

# BUG-02
@pytest.mark.asyncio
async def test_rss_lock_is_same_object_across_calls():
    """Повторные вызовы _get_rss_lock возвращают один и тот же Lock."""
    from agents.shared.utils.resolution_extractor import _get_rss_lock
    lock1 = await _get_rss_lock()
    lock2 = await _get_rss_lock()
    assert lock1 is lock2, "Разные Lock-объекты — гонка при инициализации!"

# BUG-03
@pytest.mark.asyncio
async def test_autodiscover_rss_no_double_http_on_parallel():
    """Параллельные вызовы autodiscover_rss делают HTTP только один раз."""
    import agents.shared.utils.resolution_extractor as re_mod
    re_mod._runtime_rss_cache.clear()
    re_mod._cache_loaded = True

    http_calls = []

    async def fake_get(url, **kwargs):
        http_calls.append(url)
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/xml"}
        return resp

    # Мокаем httpx.AsyncClient как асинхронный контекстный менеджер
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=fake_get)

    with patch("agents.shared.utils.resolution_extractor.httpx.AsyncClient", return_value=mock_client), \
         patch("agents.shared.utils.resolution_extractor._save_rss_cache"):
        await asyncio.gather(
            re_mod.autodiscover_rss("reuters.com"),
            re_mod.autodiscover_rss("reuters.com"),
        )

    # Оба запроса к одному URL — второй должен попасть в кэш
    reuters_calls = [c for c in http_calls if "reuters.com" in c]
    assert len(set(reuters_calls)) == 1, f"HTTP сделан дважды: {reuters_calls}"
