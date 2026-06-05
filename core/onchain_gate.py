# core/onchain_gate.py
from dataclasses import dataclass
from typing import Optional
import os
from core.onchain_scorer import OnchainScore
from core.config_provider import ConfigProvider
from agents.shared.python.db import get_connection
import logging

logger = logging.getLogger("NexusPolyBot.OnchainGate")

@dataclass
class GateResult:
    allow: bool
    reason: str
    blocked_by: str  # "volume" | "whales" | "pass"

def get_cluster_size_for_market(market_id: str) -> int:
    """Читает готовый кэш из wallet_clusters. Синхронно, <1ms."""
    try:
        with get_connection() as conn:
            row = conn.execute("""
                SELECT COUNT(DISTINCT cluster_id) as cnt
                FROM wallet_clusters wc
                JOIN trader_transactions tt ON wc.address = tt.wallet_address
                WHERE tt.market_id = ?
            """, (market_id,)).fetchone()
            return row["cnt"] if row else 0
    except Exception as e:
        logger.warning(f"[OnchainGate] Ошибка get_cluster_size_for_market: {e}")
        return 0

def check_onchain_gate(
    oc_score: Optional[OnchainScore],
    market_id: str,
    total_volume_usd: float,
    market_tag: str = "default",
) -> GateResult:
    """
    Детерминированный гейт. LLM вызывается ТОЛЬКО при allow=True.
    Условие пропуска: объём >= порог AND (есть киты ИЛИ есть кластер).
    """
    min_volume = ConfigProvider.get_swing_min_volume_sync(market_tag)
    min_whales = ConfigProvider.get_swing_min_whale_count_sync()

    if total_volume_usd < min_volume:
        return GateResult(
            allow=False,
            reason=f"Объём ${total_volume_usd:,.0f} < порог ${min_volume:,.0f}",
            blocked_by="volume"
        )

    cluster_size = get_cluster_size_for_market(market_id)
    
    # Защита от None для oc_score
    whale_count = oc_score.whale_count if oc_score else 0
    has_smart_money = (whale_count >= min_whales) or (cluster_size >= 1)

    if not has_smart_money:
        return GateResult(
            allow=False,
            reason=(
                f"Нет умных денег: known_whales={whale_count} "
                f"(порог={min_whales}), clusters={cluster_size}"
            ),
            blocked_by="whales"
        )

    return GateResult(
        allow=True,
        reason=f"Пропущен: vol=${total_volume_usd:,.0f}, whales={whale_count}, clusters={cluster_size}",
        blocked_by="pass"
    )
