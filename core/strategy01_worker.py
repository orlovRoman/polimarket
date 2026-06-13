# core/strategy01_worker.py
import asyncio
import hashlib
import os
import httpx
import logging
from typing import Optional
from agents.shared.python.db import get_connection

logger = logging.getLogger("NexusPolyBot.Strategy01")

POLYGONSCAN_API = "https://api.polygonscan.com/api"
POLYGONSCAN_KEY = os.getenv("POLYGONSCAN_API_KEY", "")
WINDOW_HOURS = 24   # кластеризуем переводы за последние 24 часа

async def fetch_funding_address(proxy_addr: str) -> Optional[str]:
    """Находит первый входящий перевод на proxy — это funding source."""
    if not POLYGONSCAN_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(POLYGONSCAN_API, params={
                "module": "account", "action": "txlist",
                "address": proxy_addr, "sort": "asc",
                "apikey": POLYGONSCAN_KEY, "page": 1, "offset": 5
            })
            if resp.status_code != 200:
                return None
            result = resp.json().get("result")
            txs = result if isinstance(result, list) else []
            for tx in txs:
                if tx.get("to", "").lower() == proxy_addr.lower():
                    # Возвращаем EOA адрес отправителя первого входящего перевода
                    return tx.get("from")
    except Exception as e:
        logger.warning(f"[Strategy01] Ошибка fetch_funding_address для {proxy_addr}: {e}")
    return None

def _get_active_wallets_sync() -> list:
    from agents.shared.python.db import get_connection
    with get_connection() as conn:
        return conn.execute(
            "SELECT DISTINCT address FROM wallets WHERE last_seen > datetime('now', '-24 hours')"
        ).fetchall()


def _save_wallet_clusters_sync(groups: dict[str, list[str]]) -> int:
    from agents.shared.python.db import get_connection
    import hashlib
    inserted = 0
    with get_connection() as conn:
        for funder, addrs in groups.items():
            if len(addrs) < 2:
                continue
            # Генерируем хэш-идентификатор кластера
            cluster_id = hashlib.sha256(
                "|".join(sorted(addrs)).encode()
            ).hexdigest()[:16]
            for addr in addrs:
                conn.execute("""
                    INSERT OR REPLACE INTO wallet_clusters
                    (cluster_id, address, funding_addr, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """, (cluster_id, addr, funder))
                inserted += 1
    return inserted


async def update_wallet_clusters() -> int:
    """Фоновый воркер: строит кластеры по общему funding source."""
    try:
        wallets = await asyncio.to_thread(_get_active_wallets_sync)
    except Exception as e:
        logger.error(f"[Strategy01] Ошибка чтения wallets: {e}")
        return 0

    if not wallets:
        logger.info("[Strategy01] Нет активных кошельков за последние 24 часа.")
        return 0

    # Ограничитель: 5 req/s для Polygonscan free tier
    funding_map: dict[str, str] = {}
    semaphore = asyncio.Semaphore(5)

    async def fetch_with_limit(addr):
        async with semaphore:
            await asyncio.sleep(0.2)   # 5 req/s
            f = await fetch_funding_address(addr)
            if f:
                funding_map[addr] = f.lower()

    await asyncio.gather(*[fetch_with_limit(r["address"]) for r in wallets])

    # Группируем: funding_addr → [proxy_wallets]
    groups: dict[str, list[str]] = {}
    for addr, funder in funding_map.items():
        groups.setdefault(funder, []).append(addr)

    # Записываем только кластеры size >= 2
    try:
        inserted = await asyncio.to_thread(_save_wallet_clusters_sync, groups)
    except Exception as e:
        logger.error(f"[Strategy01] Ошибка записи кластеров в БД: {e}")
        return 0

    logger.info(f"[Strategy01] Обновлено {inserted} записей кластеров ({len(groups)} групп)")
    return inserted
