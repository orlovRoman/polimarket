import time
import threading
import logging
from typing import Callable

logger = logging.getLogger("NexusPolyBot.Cache")

class _CacheEntry:
    __slots__ = ("data", "fetched_at", "ttl")

    def __init__(self, data: list[dict], ttl: int):
        self.data = data
        self.fetched_at = time.monotonic()
        self.ttl = ttl

    def is_valid(self) -> bool:
        return time.monotonic() - self.fetched_at < self.ttl


_lock = threading.Lock()
_store: dict[str, _CacheEntry] = {}


def get_raw_events(
    cache_key: str,
    fetch_fn: Callable[[], list[dict]],
    ttl_seconds: int = 300,
) -> list[dict]:
    """
    Возвращает list[dict] — сырой ответ Polymarket /events API.
    Если кэш валиден — возвращает из памяти без сетевого запроса.
    fetch_fn вызывается вне лока, чтобы не блокировать параллельных читателей.
    """
    with _lock:
        entry = _store.get(cache_key)
        if entry and entry.is_valid():
            logger.debug(f"[PolyCache] HIT {cache_key}")
            return entry.data

    logger.info(f"[PolyCache] MISS {cache_key} — fetching from API...")
    data = fetch_fn()  # сетевой запрос вне лока

    with _lock:
        # double-check: пока мы fetching, другой поток мог уже обновить
        entry = _store.get(cache_key)
        if not (entry and entry.is_valid()):
            _store[cache_key] = _CacheEntry(data=data, ttl=ttl_seconds)

    return data


def invalidate(cache_key: str) -> None:
    """Принудительная инвалидация — например, после force-refresh команды."""
    with _lock:
        _store.pop(cache_key, None)
