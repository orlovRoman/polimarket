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
_fetching: set[str] = set()
_fetch_done: dict[str, threading.Event] = {}


def get_raw_events(
    cache_key: str,
    fetch_fn: Callable[[], list[dict]],
    ttl_seconds: int = 300,
) -> list[dict]:
    """
    Возвращает list[dict] — сырой ответ Polymarket /events API.
    Если кэш валиден — возвращает из памяти без сетевого запроса.
    """
    # Быстрый путь: валидный кэш
    with _lock:
        entry = _store.get(cache_key)
        if entry and entry.is_valid():
            logger.debug(f"[PolyCache] HIT {cache_key}")
            return entry.data
        # Если другой поток уже fetching — ждём его Event
        if cache_key in _fetching:
            event = _fetch_done[cache_key]
        else:
            _fetching.add(cache_key)
            _fetch_done[cache_key] = threading.Event()
            event = None

    if event:
        # Ждём пока первый поток завершит fetch
        event.wait(timeout=15)
        with _lock:
            entry = _store.get(cache_key)
            return entry.data if entry else []

    # Только один поток делает реальный запрос
    try:
        logger.info(f"[PolyCache] MISS {cache_key} — fetching from API...")
        data = fetch_fn()
        with _lock:
            _store[cache_key] = _CacheEntry(data=data, ttl=ttl_seconds)
        return data
    finally:
        with _lock:
            _fetching.discard(cache_key)
            _fetch_done.pop(cache_key, threading.Event()).set()


def invalidate(cache_key: str) -> None:
    """Принудительная инвалидация — например, после force-refresh команды."""
    with _lock:
        _store.pop(cache_key, None)
