# core/event_cluster.py
"""
Группировка рынков по event_slug до передачи в arb_scanner.
O(N log N) вместо O(N²) — сокращает пары в 50–100×.
"""
from __future__ import annotations
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.models import Market


def cluster_by_event_slug(
    markets: list[Market],
) -> dict[str, list[Market]]:
    """
    Группирует рынки по event_slug.
    Рынки без slug попадают в кластер по первым 3 словам заголовка.
    """
    clusters: dict[str, list[Market]] = defaultdict(list)

    for market in markets:
        slug = getattr(market, "event_slug", None)
        if slug:
            clusters[slug].append(market)
        else:
            # Fallback: первые 3 слова заголовка как ключ
            words = market.title.lower().split()[:3]
            key = "_".join(words) if words else "ungrouped"
            clusters[f"__title__{key}"].append(market)

    # Отбрасываем одиночные рынки — пары невозможны
    return {k: v for k, v in clusters.items() if len(v) >= 2}


def iter_cluster_pairs(
    clusters: dict[str, list[Market]],
    min_spread_pct: float = 5.0,
    max_pairs_per_cluster: int = 10,
):
    """
    Генератор: для каждого кластера запускает arb_scanner.
    Возвращает (market_a, market_b, MathFilterResult).
    """
    from core.arb_scanner import find_complementary_pairs

    for slug, cluster_markets in clusters.items():
        pairs = find_complementary_pairs(
            markets=cluster_markets,
            min_spread_pct=min_spread_pct,
            max_pairs=max_pairs_per_cluster,
        )
        for pair in pairs:
            yield pair
