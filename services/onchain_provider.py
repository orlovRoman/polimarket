import httpx
import threading
import time
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger("NexusPolyBot.OnChainProvider")

CLOB_BASE = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"

# TTL-кэш защищенный блокировкой
_cache: Dict[str, tuple] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 300  # 5 минут

def _cached_get(url: str) -> Optional[Any]:
    now = time.time()
    with _cache_lock:
        if url in _cache:
            data, ts = _cache[url]
            if now - ts < CACHE_TTL:
                return data
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                with _cache_lock:
                    _cache[url] = (data, now)
                return data
    except Exception as e:
        logger.error(f"[OnChain] Ошибка запроса к {url}: {e}")
    return None

def get_recent_trades(condition_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Последние сделки на рынке — адреса, направление, размер."""
    url = f"{CLOB_BASE}/trades?condition_id={condition_id}&limit={limit}"
    data = _cached_get(url)
    if not data:
        return []
    return data.get("data", [])

def get_top_positions(condition_id: str, min_usd: float = 500) -> List[Dict[str, Any]]:
    """Текущие позиции кошельков (открытые) — кто сколько держит."""
    url = f"{GAMMA_BASE}/positions?conditionId={condition_id}&sizeThreshold={min_usd}"
    data = _cached_get(url)
    if not data:
        return []
    return data if isinstance(data, list) else data.get("data", [])
