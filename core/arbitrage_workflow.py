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
    summary_callback = None
) -> list[CrossArbitrageSignal]:
    
    api_key = api_key or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY не задан")
        
    poly_limit = poly_limit if poly_limit is not None else config.ARB_POLY_LIMIT
    kalshi_limit = kalshi_limit if kalshi_limit is not None else config.ARB_KALSHI_LIMIT
    min_match_score = min_match_score if min_match_score is not None else config.ARB_MIN_MATCH_SCORE
    from core.config_provider import ConfigProvider
    min_spread_alert = min_spread_alert if min_spread_alert is not None else (ConfigProvider.get_min_spread_sync("cross_platform") * 100.0)

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
                raw = adapter.fetch_raw_events(limit=limit)
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
    verified_ids: set[tuple[str, str]] = {(ma.id, mb.id) for ma, mb, _ in manual_pairs}
    
    # high-score без LLM:
    for p in auto_pairs[:50]:
        if p[2] >= 0.72 and (p[0].id, p[1].id) not in verified_ids:
            verified.append(p)
            verified_ids.add((p[0].id, p[1].id))

    # LLM-верификация батчами
    pairs_to_verify = [p for p in auto_pairs[:50] if 0.50 <= p[2] < 0.72]
    
    batch_size = 5
    for i in range(0, len(pairs_to_verify), batch_size):
        batch = pairs_to_verify[i:i+batch_size]
        for ma, mb, score in batch:
            # Simple retry loop
            for attempt in range(3):
                try:
                    llm_result = verify_pair_with_llm(ma, mb, api_key)
                    if llm_result.get("is_same_event") and llm_result.get("confidence", 0) >= 0.75:
                        key = (ma.id, mb.id)
                        if key not in verified_ids:
                            verified.append((ma, mb, llm_result["confidence"]))
                            verified_ids.add(key)
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
            time.sleep(10 if "429" in str(e) else 1)
            continue

        if not signal:
            # Если вернулся None (например, из-за 429 ошибки от всех моделей), делаем паузу побольше
            time.sleep(5)
            continue

        # Сохраняем всегда для диагностики UI
        save_cross_arbitrage(signal)

        # Логируем в Eval Engine всегда, если есть спред
        spread_val = getattr(signal, "spread_percent", 0) or 0
        if spread_val > 0:
            try:
                from core.eval.signal_logger import SignalLogger, StrategyType
                logger_eval = SignalLogger()
                logger_eval.log_signal(
                    signal_id=f"{signal.market_a_id}__{signal.market_b_id}",
                    strategy_type=StrategyType.CROSS_PLATFORM,
                    market_id=signal.market_a_id,
                    predicted_probability=1.0,
                    market_price_at_signal=signal.market_a_price,
                    edge_at_signal=signal.spread_percent / 100.0,
                    metadata={
                        "market_a_id": signal.market_a_id,
                        "market_b_id": signal.market_b_id,
                        "market_a_price": signal.market_a_price,
                        "market_b_price": signal.market_b_price,
                        "spread_percent": signal.spread_percent,
                        "target_outcome": "YES_A" if signal.market_a_price < signal.market_b_price else "YES_B",
                        "summary": f"Arbitrage: {signal.market_a_title} vs {signal.market_b_title}",
                        "platform": "polymarket_kalshi"
                    }
                )
            except Exception as e:
                logger.error(f"[SCAN] Ошибка логирования арбитража в Evaluation Engine: {e}", exc_info=True)

        if signal.has_arbitrage and signal.spread_percent >= min_spread_alert:
            from core.models import ArbitrageSignal
            from core.workflow import process_arbitrage_signal
            from services.notifications import send_telegram
            
            callback = summary_callback or send_telegram
            
            # Конвертируем CrossArbitrageSignal в универсальный ArbitrageSignal
            arb_sig = ArbitrageSignal(
                id=f"sig-arb-cross-{signal.market_a_id}-{int(time.time())}",
                type="CROSS_PLATFORM",
                market_id_a=signal.market_a_id,
                market_id_b=signal.market_b_id,
                platform_a=signal.market_a_platform,
                platform_b=signal.market_b_platform,
                spread_pct=signal.spread_percent,
                target_outcome="YES_A" if signal.market_a_price < signal.market_b_price else "YES_B",
                max_safe_size=100.0,  # Безопасный дефолт для бюджета
                edge=signal.spread_percent / 100.0,
                confidence=signal.match_score,
                summary=f"Арбитраж {signal.market_a_platform} ↔ {signal.market_b_platform} ({signal.spread_percent:.1f}%)",
                details=signal.trade_instruction + "\n\n" + signal.reasoning,
                status="PENDING"
            )
            
            # Загружаем стаканы для проверки ликвидности SHADOW
            poly_adapter = PolymarketAdapter()
            token_a = None
            try:
                m_a_full = poly_adapter.get_market(signal.market_a_id)
                if m_a_full and m_a_full.tokens:
                    token_a = m_a_full.tokens[0]
            except Exception:
                pass
                
            orderbook_a = {}
            if token_a:
                try:
                    orderbook_a = poly_adapter.get_orderbook(token_a) or {}
                except Exception:
                    pass
            
            process_arbitrage_signal(arb_sig, orderbook_a, kalshi_book or {}, callback)
            found.append(signal)
            
            # BUG-02: mark alerted
            signal_id = f"{signal.market_a_id}__{signal.market_b_id}"
            mark_cross_arbitrage_alerted(signal_id)
            
            logger.info(f"[SCAN] 🔥 АРБИТРАЖ: {signal.arbitrage_type} "
                  f"спред={signal.spread_percent:.1f}%  "
                  f"POLY: {signal.market_a_title[:35]} / KALSHI: {signal.market_b_title[:35]}")

    logger.info(f"[SCAN] Итого арбитражей: {len(found)}")
    return found
