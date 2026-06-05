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
    Группирует рынки по event_slug и объединяет схожие кластеры
    по сходству названий (difflib SequenceMatcher >= 0.8) для
    поддержки временных (temporal) коридоров.
    """
    from difflib import SequenceMatcher
    from core.arb_scanner import _strip_price_tag

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

    # Дополнительное слияние кластеров на основе схожести названий представителей
    merged_clusters: dict[str, list[Market]] = {}
    rep_titles = {}
    for cid, cl_markets in clusters.items():
        if cl_markets:
            rep_titles[cid] = _strip_price_tag(cl_markets[0].title).lower()

    visited = set()
    for cid, cl_markets in clusters.items():
        if cid in visited:
            continue
        visited.add(cid)
        
        current_cluster = list(cl_markets)
        rep_a = rep_titles[cid]
        
        for other_cid, other_markets in clusters.items():
            if other_cid in visited:
                continue
            rep_b = rep_titles[other_cid]
            ratio = SequenceMatcher(None, rep_a, rep_b).ratio()
            if ratio >= 0.8:
                current_cluster.extend(other_markets)
                visited.add(other_cid)
                
        merged_clusters[cid] = current_cluster

    # Отбрасываем одиночные рынки — пары невозможны
    return {k: v for k, v in merged_clusters.items() if len(v) >= 2}


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
