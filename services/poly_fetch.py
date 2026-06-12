# services/poly_fetch.py
import time
import logging

logger = logging.getLogger("NexusPolyBot.PolyFetch")
_cache: dict = {}

def fetch_poly_events(adapter, limit: int) -> list:
    """
    Простой функциональный кэш без блокировок.
    Предотвращает дублирующие тяжелые HTTP-запросы при параллельном/последовательном сканировании.
    TTL кэша = 60 секунд.
    """
    key = "poly_data"
    entry = _cache.get(key)
    if entry and time.monotonic() - entry["ts"] < 60:
        if len(entry["data"]) >= limit:
            logger.debug(f"[PolyFetch] HIT cache for key {key} (requested limit {limit}, cached size {len(entry['data'])})")
            return entry["data"][:limit]
    
    fetch_limit = max(limit, 500)
    logger.info(f"[PolyFetch] MISS cache - fetching from API with limit={fetch_limit} (requested limit={limit})...")
    raw = adapter.fetch_raw_events(limit=fetch_limit)
    _cache[key] = {"data": raw, "ts": time.monotonic()}
    return raw[:limit]
