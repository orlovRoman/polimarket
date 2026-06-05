"""
Детерминированный поиск арбитражных пар без LLM.
Заменяет LLM-корреляции из NEXUS screener.
"""
from __future__ import annotations
import re
import logging
from typing import TYPE_CHECKING
from core.math_filter import math_pre_filter, FilterDecision, MathFilterResult
from config import ARB_MIN_SPREAD_PCT

if TYPE_CHECKING:
    from core.models import Market

logger = logging.getLogger("NexusPolyBot.arb_scanner")

_STOPWORDS = frozenset({
    'will', 'the', 'a', 'an', 'in', 'by', 'to', 'at', 'on',
    'for', 'be', 'is', 'are', 'was', 'or', 'and', 'if',
    'yes', 'no',
})

_PRICE_TAG_RE = re.compile(r'\([A-Z]+:\s*\d+¢[^)]*\)', re.IGNORECASE)

def _strip_price_tag(title: str) -> str:
    return _PRICE_TAG_RE.sub("", title).strip()

def _quick_pair_check(title_a: str, title_b: str, min_common: int = 2) -> bool:
    """
    Быстрый pre-check: есть ли минимальное пересечение слов.
    ~0.01ms — запускается до math_pre_filter, чтобы сэкономить O(n²) вызовов.
    """
    title_a = _strip_price_tag(title_a)
    title_b = _strip_price_tag(title_b)
    words_a = set(re.findall(r'\b[a-z\d]{2,}\b', title_a.lower())) - _STOPWORDS
    words_b = set(re.findall(r'\b[a-z\d]{2,}\b', title_b.lower())) - _STOPWORDS
    if not words_a or not words_b:
        return False
    common = len(words_a & words_b)
    # Для коротких заголовков (≤4 слова) достаточно 1 общего, если min_common не переопределен явно
    effective_min = 1 if min_common == 2 and min(len(words_a), len(words_b)) <= 4 else min_common
    return common >= effective_min

def find_complementary_pairs(
    markets: list[Market],
    min_spread_pct: float = ARB_MIN_SPREAD_PCT,
    max_pairs: int = 20,
) -> list[tuple[Market, Market, MathFilterResult]]:
    """
    Ищет арбитражные и AMBIGUOUS-пары без LLM.
    O(n²) с early exit через _quick_pair_check.
    На 150 рынках: ~11 000 сравнений < 150ms.

    Returns: список (market_a, market_b, MathFilterResult),
             отсортированный по убыванию spread_pct.
    """
    results: list[tuple[Market, Market, MathFilterResult]] = []

    for i, a in enumerate(markets):
        if len(results) >= max_pairs:   # ← FIX: guard вверху внешнего цикла
            break
        for b in markets[i + 1:]:
            if not _quick_pair_check(a.title, b.title):
                continue
            try:
                mf = math_pre_filter(a, b)
            except Exception as e:
                logger.warning(f"[arb_scanner] math_pre_filter error ({a.id}, {b.id}): {e}")
                continue

            if mf.decision == FilterDecision.CONFIRMED_ARBITRAGE:
                results.append((a, b, mf))
            elif (
                mf.decision == FilterDecision.AMBIGUOUS
                and mf.spread_pct >= min_spread_pct
            ):
                results.append((a, b, mf))

            if len(results) >= max_pairs:
                break   # ← внутренний цикл

    results.sort(key=lambda x: x[2].spread_pct, reverse=True)
    logger.info(f"[arb_scanner] Найдено {len(results)} пар (min_spread={min_spread_pct}%)")
    return results
