"""
Сервис синхронизации открытых позиций китов через Polymarket Gamma API.
"""
import logging
from datetime import datetime, timezone
from typing import Optional
import httpx
import asyncio

from agents.shared.python.db import get_connection

logger = logging.getLogger("NexusPolyBot.WhalePortfolioService")

GAMMA_POSITIONS_URL = "https://data-api.polymarket.com/positions"

# ── Fetching ──────────────────────────────────────────────────────────────────

async def fetch_wallet_positions(wallet_address: str) -> list[dict]:
    """Получает открытые позиции кошелька через Gamma API."""
    params = {
        "user": wallet_address,
        "sizeThreshold": "0.01",  # фильтруем пылевые позиции
        "limit": 500,
    }
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        async with httpx.AsyncClient(headers=headers, timeout=20.0) as client:
            resp = await client.get(GAMMA_POSITIONS_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("positions", [])
    except Exception as e:
        logger.warning(f"[WhalePortfolio] Ошибка при запросе позиций {wallet_address}: {e}")
        return []

# ── Persistence ───────────────────────────────────────────────────────────────

def save_snapshot(wallet_address: str, positions: list[dict]) -> int:
    """
    Сохраняет позиции кошелька как новый снапшот.
    Удаляет предыдущие снапшоты этого кошелька (держим только последний).
    Возвращает количество сохранённых позиций.
    """
    if not positions:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    rows = []

    for pos in positions:
        size = float(pos.get("size", 0) or 0)
        if size <= 0:
            continue
        rows.append((
            wallet_address,
            pos.get("market", pos.get("conditionId", "")),  # market_id
            pos.get("conditionId"),
            pos.get("outcome", "YES").upper(),
            size,
            float(pos.get("avgPrice", 0) or 0),
            float(pos.get("currentValue", size) or size),
            pos.get("title", ""),
            pos.get("url", ""),
            pos.get("endDate", pos.get("closeTime", "")),
            now,
        ))

    if not rows:
        return 0

    with get_connection() as conn:
        # Удаляем устаревший снапшот этого кошелька
        conn.execute(
            "DELETE FROM whale_portfolio_snapshots WHERE wallet_address = ?",
            (wallet_address,)
        )
        conn.executemany("""
            INSERT INTO whale_portfolio_snapshots
                (wallet_address, market_id, condition_id, outcome, size,
                 avg_price, current_value, market_title, market_url,
                 market_close_time, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

    logger.info(f"[WhalePortfolio] Сохранено {len(rows)} позиций для {wallet_address[:10]}...")
    return len(rows)

# ── Aggregation ───────────────────────────────────────────────────────────────

def get_whale_radar_summary(min_whales: int = 1) -> list[dict]:
    """
    Агрегирует позиции всех китов по маркетам.
    Возвращает список событий с разбивкой YES/NO и дельтой.
    """
    with get_connection() as conn:
        rows = conn.execute("""
            WITH filtered_snapshots AS (
                SELECT market_id, market_title, market_url, market_close_time, outcome, wallet_address, current_value 
                FROM whale_portfolio_snapshots 
                WHERE market_close_time IS NULL
                
                UNION ALL
                
                SELECT market_id, market_title, market_url, market_close_time, outcome, wallet_address, current_value 
                FROM whale_portfolio_snapshots 
                WHERE market_close_time IS NOT NULL AND market_close_time > datetime('now', '-1 day')
            )
            SELECT
                w.market_id,
                w.market_title,
                w.market_url,
                w.market_close_time,
                w.outcome,
                COUNT(DISTINCT w.wallet_address)  AS whale_count,
                SUM(w.current_value)              AS total_usd
            FROM filtered_snapshots w
            GROUP BY w.market_id, w.outcome
            ORDER BY total_usd DESC
        """).fetchall()

    # Группируем YES/NO по market_id
    markets: dict[str, dict] = {}
    for r in rows:
        mid = r["market_id"]
        if mid not in markets:
            markets[mid] = {
                "market_id": mid,
                "market_title": r["market_title"],
                "market_url": r["market_url"],
                "market_close_time": r["market_close_time"],
                "yes_whales": 0, "yes_usd": 0.0,
                "no_whales": 0, "no_usd": 0.0,
            }
        if r["outcome"] == "YES":
            markets[mid]["yes_whales"] = r["whale_count"]
            markets[mid]["yes_usd"] = r["total_usd"]
        else:
            markets[mid]["no_whales"] = r["whale_count"]
            markets[mid]["no_usd"] = r["total_usd"]

    result = []
    for m in markets.values():
        total_whales = m["yes_whales"] + m["no_whales"]
        if total_whales < min_whales:
            continue
        delta_usd = m["yes_usd"] - m["no_usd"]
        m["delta_usd"] = delta_usd
        m["dominant_side"] = "YES" if delta_usd >= 0 else "NO"
        result.append(m)

    # Сортировка: сначала рынки с наибольшей абсолютной дельтой
    result.sort(key=lambda x: abs(x["delta_usd"]), reverse=True)
    return result

async def run_portfolio_sync_job():
    """Standalone async джоба — без зависимости от main.py."""
    from agents.shared.python.db import get_connection
    
    logger.info("[WhaleRadar] Запуск фоновой синхронизации портфелей китов...")
    
    try:
        with get_connection() as conn:
            whales = conn.execute(
                "SELECT address, alias FROM wallets"
            ).fetchall()

        if not whales:
            logger.info("[WhaleRadar] Нет китов в wallets для радара.")
            return

        total_positions = 0
        for whale in whales:
            address = whale["address"]
            alias = whale["alias"] or address[:10]
            try:
                positions = await fetch_wallet_positions(address)
                saved = await asyncio.to_thread(save_snapshot, address, positions)
                total_positions += saved
                logger.info(f"[WhalePortfolioSync] {alias}: {saved} позиций сохранено")
                await asyncio.sleep(0.5)  # rate-limit: 2 req/s
            except Exception as e:
                logger.error(f"[WhalePortfolioSync] Ошибка для {alias}: {e}")
                
        logger.info(
            f"[WhalePortfolioSync] Синхронизация завершена. "
            f"{len(whales)} китов, {total_positions} позиций."
        )
    except Exception as e:
        logger.error(f"[WhaleRadar] Глобальная ошибка синхронизации: {e}")
