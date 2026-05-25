import requests
from typing import Optional
from functools import lru_cache
import time

CLOB_BASE = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"

# TTL-кэш чтобы не долбить API на каждом рынке
_cache: dict = {}
CACHE_TTL = 300  # 5 минут

def _cached_get(url: str) -> Optional[dict]:
    now = time.time()
    if url in _cache:
        data, ts = _cache[url]
        if now - ts < CACHE_TTL:
            return data
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            _cache[url] = (data, now)
            return data
    except Exception as e:
        print(f"[OnChain] Ошибка: {e}")
    return None

def get_recent_trades(condition_id: str, limit: int = 50) -> list[dict]:
    """Последние сделки на рынке — адреса, направление, размер."""
    url = f"{CLOB_BASE}/trades?condition_id={condition_id}&limit={limit}"
    data = _cached_get(url)
    if not data:
        return []
    return data.get("data", [])

def get_top_positions(condition_id: str, min_usd: float = 500) -> list[dict]:
    """Текущие позиции кошельков (открытые) — кто сколько держит."""
    url = f"{GAMMA_BASE}/positions?conditionId={condition_id}&sizeThreshold={min_usd}"
    data = _cached_get(url)
    if not data:
        return []
    return data if isinstance(data, list) else data.get("data", [])
