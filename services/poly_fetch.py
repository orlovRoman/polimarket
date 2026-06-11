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
    key = f"poly_{limit}"
    entry = _cache.get(key)
    if entry and time.monotonic() - entry["ts"] < 60:
        logger.debug(f"[PolyFetch] HIT cache for key {key}")
        return entry["data"]
    
    logger.info(f"[PolyFetch] MISS cache for key {key} - making HTTP request...")
    raw = adapter.fetch_raw_events(limit=limit)
    _cache[key] = {"data": raw, "ts": time.monotonic()}
    return raw
