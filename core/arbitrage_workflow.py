"""
Оркестратор кросс-платформенного арбитражного скана.
"""
import os
import time
from typing import Optional
import logging
import asyncio

from agents.shared.adapters.polymarket import PolymarketAdapter
from agents.shared.adapters.kalshi import KalshiAdapter
from agents.shared.adapters.base_adapter import BaseMarketAdapter
from agents.polymarket_arbitrage_agent.src.agent import ArbitrageAgent
from agents.shared.python.db import save_cross_arbitrage, mark_cross_arbitrage_alerted
from core.models import CrossArbitrageSignal, Market
from services.market_matcher import find_candidate_pairs, load_manual_pairs, verify_pair_with_llm
import config

logger = logging.getLogger("NexusPolyBot.Arbitrage")


def run_cross_platform_scan(
    api_key: Optional[str] = None,
    adapters: Optional[list[BaseMarketAdapter]] = None,
    poly_limit: Optional[int] = None,
    kalshi_limit: Optional[int] = None,
    min_match_score: Optional[float] = None,
    min_spread_alert: Optional[float] = None,
    dry_run: bool = False,
) -> list[CrossArbitrageSignal]:
    
    api_key = api_key or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY не задан")
        
    poly_limit = poly_limit if poly_limit is not None else config.ARB_POLY_LIMIT
    kalshi_limit = kalshi_limit if kalshi_limit is not None else config.ARB_KALSHI_LIMIT
    min_match_score = min_match_score if min_match_score is not None else config.ARB_MIN_MATCH_SCORE
    min_spread_alert = min_spread_alert if min_spread_alert is not None else config.ARB_MIN_SPREAD_ALERT

    if adapters is None:
        adapters = [PolymarketAdapter(), KalshiAdapter()]

    all_markets: dict[str, list[Market]] = {}
    kalshi_adapter = None
    for adapter in adapters:
        if isinstance(adapter, KalshiAdapter):
            kalshi_adapter = adapter
            
        logger.info(f"[SCAN] Загружаю {adapter.name}...")
        limit = poly_limit if adapter.name == "polymarket" else kalshi_limit
        try:
            if adapter.name == "polymarket":
                from services.polymarket_cache import get_raw_events
                raw = get_raw_events(
                    cache_key=f"poly_events_{limit}",
                    fetch_fn=lambda: adapter.fetch_raw_events(limit=limit),
                    ttl_seconds=config.POLY_EVENTS_CACHE_TTL_SECONDS,
                )
                all_markets[adapter.name] = adapter.parse_events_to_markets(raw, limit)
            else:
                all_markets[adapter.name] = adapter.list_markets(limit=limit)
            logger.info(f"[SCAN] {adapter.name}: {len(all_markets[adapter.name])} рынков")
        except Exception as e:
            logger.error(f"[SCAN] Ошибка загрузки {adapter.name}: {e}")
            all_markets[adapter.name] = []

    manual_raw = load_manual_pairs()
    manual_pairs: list[tuple[Market, Market, float]] = []
    if manual_raw:
        poly_by_id = {m.id: m for m in all_markets.get("polymarket", [])}
        kalshi_by_id = {m.id: m for m in all_markets.get("kalshi", [])}
        for entry in manual_raw:
            ma = poly_by_id.get(entry.get("poly_id", ""))
            mb = kalshi_by_id.get(entry.get("kalshi_id", ""))
            if ma and mb:
                manual_pairs.append((ma, mb, 1.0))
            else:
                logger.warning(f"[SCAN] Ручная пара не найдена в рынках: {entry}")

    poly_markets = all_markets.get("polymarket", [])
    kalshi_markets = all_markets.get("kalshi", [])

    auto_pairs = find_candidate_pairs(
        poly_markets, 
        kalshi_markets, 
        min_score=min_match_score,
        max_days_diff=config.ARB_MAX_DAYS_DIFF
    )

    manual_ids = {(ma.id, mb.id) for ma, mb, _ in manual_pairs}
    auto_pairs = [(ma, mb, s) for ma, mb, s in auto_pairs if (ma.id, mb.id) not in manual_ids]

    all_candidates = manual_pairs + auto_pairs
    logger.info(f"[SCAN] Итого кандидатов: {len(all_candidates)} "
          f"(manual={len(manual_pairs)}, auto={len(auto_pairs)})")

    if dry_run:
        logger.info("=== КАНДИДАТЫ ДЛЯ РУЧНОГО КОНФИГА ===")
        for i, (ma, mb, score) in enumerate(auto_pairs[:20]):
            logger.info(f"{i+1}. [{score:.2f}] POLY:   {ma.title[:60]}\n"
                  f"         KALSHI: {mb.title[:60]}\n"
                  f"   Ценовой спред: {abs(ma.price - mb.price) * 100:.1f}¢")
        return []

    verified: list[tuple[Market, Market, float]] = list(manual_pairs)
    
    # LLM-верификация батчами
    pairs_to_verify = [p for p in auto_pairs[:50] if 0.50 <= p[2] < 0.72]
    # Те что > 0.72 добавляем сразу
    verified.extend([p for p in auto_pairs[:50] if p[2] >= 0.72])
    
    batch_size = 5
    for i in range(0, len(pairs_to_verify), batch_size):
        batch = pairs_to_verify[i:i+batch_size]
        for ma, mb, score in batch:
            # Simple retry loop
            for attempt in range(3):
                try:
                    llm_result = verify_pair_with_llm(ma, mb, api_key)
                    if llm_result.get("is_same_event") and llm_result.get("confidence", 0) >= 0.75:
                        verified.append((ma, mb, llm_result["confidence"]))
                    break # Success
                except Exception as e:
                    if "429" in str(e) or "503" in str(e):
                        logger.warning(f"[SCAN] Rate limit/Overload verify_pair_with_llm (попытка {attempt+1}): {e}")
                        time.sleep(5 * (attempt + 1))
                    else:
                        logger.error(f"[SCAN] Ошибка LLM-верификации пары: {e}")
                        break # Unrecoverable
        
        # Batch delay to avoid 429
        if i + batch_size < len(pairs_to_verify):
            time.sleep(2)

    logger.info(f"[SCAN] Верифицировано пар: {len(verified)}")

    agent = ArbitrageAgent(api_key=api_key)
    found: list[CrossArbitrageSignal] = []

    for ma, mb, match_score in verified:

        # RISK-10: Fetch orderbook for Kalshi
        kalshi_book = None
        if kalshi_adapter and mb.platform == "kalshi":
            kalshi_book = kalshi_adapter.get_orderbook(mb.id)
            
        try:
            signal = agent.analyze_cross_platform(ma, mb, match_score, orderbook_b=kalshi_book)
        except Exception as e:
            logger.error(f"[SCAN] Ошибка анализа пары {ma.id} / {mb.id}: {e}")
            continue

        if not signal:
            continue

        # BUG-01: Only save if has_arbitrage
        if signal.has_arbitrage:
            save_cross_arbitrage(signal)
            
            if signal.spread_percent >= min_spread_alert:
                found.append(signal)
                # BUG-02: mark alerted
                signal_id = f"{signal.market_a_id}__{signal.market_b_id}"
                mark_cross_arbitrage_alerted(signal_id)
                
                logger.info(f"[SCAN] 🔥 АРБИТРАЖ: {signal.arbitrage_type} "
                      f"спред={signal.spread_percent:.1f}%  "
                      f"POLY: {signal.market_a_title[:35]} / KALSHI: {signal.market_b_title[:35]}")

    logger.info(f"[SCAN] Итого арбитражей: {len(found)}")
    return found
