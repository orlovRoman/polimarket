import logging
import requests
from datetime import datetime, timezone
from agents.polymarket_arbitrage_agent.src.temporal.loader import load_events_from_raw
from agents.polymarket_arbitrage_agent.src.temporal.detector import find_candidates, compute_quality_score
from agents.polymarket_arbitrage_agent.src.temporal.orderbook import fetch_real_entry_prices
from agents.polymarket_arbitrage_agent.src.temporal.sizing import compute_sizing, compute_exit_rule
from agents.polymarket_arbitrage_agent.src.temporal.models import TemporalCorridorSignal, TemporalLeg
from agents.shared.python.db import save_temporal_corridor
from services.polymarket_cache import get_raw_events
from agents.shared.adapters.polymarket import PolymarketAdapter
from services.http_utils import make_session_with_timeout, fetch_with_retry
import config

logger = logging.getLogger("NexusPolyBot.TemporalCorridor")

def run_temporal_corridor_scan(
    poly_limit: int = 100,
    min_theoretical_spread_pct: float = 1.0,   # предфильтр
    min_real_spread_pct: float = 2.0,          # финальный фильтр по ордербуку
    min_date_gap_days: int = 14,
    min_volume: float = 5_000,
    min_executable_contracts: float = 30.0,
    min_quality_score: float = 0.4,
    budget: float = 200.0,
) -> list[TemporalCorridorSignal]:
    from core.config_provider import ConfigProvider
    min_real_spread_pct = ConfigProvider.get_min_spread_sync("temporal_corridor") * 100.0

    # 1. Загрузка событий (/events API — группы готовы) через кэш
    raw = get_raw_events(
        cache_key=f"poly_events_{poly_limit}",
        fetch_fn=lambda: PolymarketAdapter.fetch_raw_events(limit=poly_limit),
        ttl_seconds=config.POLY_EVENTS_CACHE_TTL_SECONDS,
    )
    events = load_events_from_raw(
        raw_events=raw,
        min_markets=2,
        min_volume=min_volume,
    )
    logger.info(f"[TC] Событий с несколькими рынками: {len(events)}")

    # 2. Детектор — чистая математика
    candidates = find_candidates(
        events,
        min_date_gap_days=min_date_gap_days,
        min_theoretical_spread_pct=min_theoretical_spread_pct,
        min_volume=min_volume,
    )
    logger.info(f"[TC] Теоретических кандидатов: {len(candidates)}")

    if not candidates:
        return []

    session = make_session_with_timeout()
    found: list[TemporalCorridorSignal] = []

    stats = {"no_orderbook": 0, "low_spread": 0, "low_size": 0, "low_quality": 0, "passed": 0}

    # 3. Реальные цены + sizing
    for c in candidates:
        ob = fetch_with_retry(fetch_real_entry_prices, c.early, c.late, session)

        if not ob:
            stats["no_orderbook"] += 1
            logger.debug(f"[TC] Ордербук недоступен: {c.early.market_id}")
            continue

        if ob["real_spread_pct"] < min_real_spread_pct:
            stats["low_spread"] += 1
            continue

        if ob["executable_contracts"] < min_executable_contracts:
            stats["low_size"] += 1
            continue

        sizing = compute_sizing(
            ask_no_early=ob["ask_no_early"],
            ask_yes_late=ob["ask_yes_late"],
            p_before=c.p_before,
            p_in_corridor=c.p_in_corridor,
            p_never=c.p_never,
            budget=budget,
        )

        quality = compute_quality_score(
            real_spread_pct=ob["real_spread_pct"],
            date_gap_days=c.date_gap_days,
            executable_contracts=ob["executable_contracts"],
            p_in_corridor=c.p_in_corridor,
        )

        if quality < min_quality_score:
            stats["low_quality"] += 1
            continue

        stats["passed"] += 1

        exit_rule = compute_exit_rule(
            early_expiry=c.early.close_time,
            late_expiry=c.late.close_time,
            p_never=c.p_never,
        )

        signal = TemporalCorridorSignal(
            signal_id=f"{c.early.market_id}__{c.late.market_id}",
            event_slug=c.event.event_slug,
            event_title=c.event.event_title,
            event_url=c.event.url,

            early_leg=TemporalLeg(
                market_id=c.early.market_id,
                question=c.early.question,
                market_url=c.event.url,
                expiry=c.early.close_time,
                price_yes=c.early.price_yes,
                ask_price=ob["ask_no_early"],
                side="NO",
                entry_cost=ob["ask_no_early"],
                token_id=c.early.token_yes,
                volume=c.early.volume,
            ),
            late_leg=TemporalLeg(
                market_id=c.late.market_id,
                question=c.late.question,
                market_url=c.event.url,
                expiry=c.late.close_time,
                price_yes=c.late.price_yes,
                ask_price=ob["ask_yes_late"],
                side="YES",
                entry_cost=ob["ask_yes_late"],
                token_id=c.late.token_yes,
                volume=c.late.volume,
            ),

            date_gap_days=c.date_gap_days,
            p_early=c.p_early,
            p_late=c.p_late,
            p_in_corridor=c.p_in_corridor,
            p_before_early=c.p_before,
            p_never=c.p_never,

            theoretical_cost=c.theoretical_cost,
            real_cost=ob["real_cost"],
            theoretical_spread_pct=c.theoretical_spread_pct,
            real_spread_pct=ob["real_spread_pct"],

            pnl_s1_before_early=sizing["pnl_s1_before_early"],
            pnl_s2_in_corridor=sizing["pnl_s2_in_corridor"],
            pnl_s3_never=sizing["pnl_s3_never"],

            early_stake_usd=sizing["early_stake_usd"],
            late_stake_usd=sizing["late_stake_usd"],
            early_contracts=sizing["early_contracts"],
            late_contracts=sizing["late_contracts"],
            ev_usd=sizing["ev_usd"],
            roi_pct=sizing["roi_min_pct"],

            quality_score=quality,
            exit_rule=exit_rule,
            created_at=datetime.now(timezone.utc),
        )

        save_temporal_corridor(signal)
        
        # Запись в Evaluation Engine
        try:
            from core.eval.signal_logger import SignalLogger, StrategyType
            logger_eval = SignalLogger()
            logger_eval.log_signal(
                signal_id=signal.signal_id,
                strategy_type=StrategyType.TEMPORAL_CORRIDOR,
                market_id=c.early.market_id,
                predicted_probability=c.p_in_corridor if c.p_in_corridor is not None else 0.80,
                market_price_at_signal=ob["real_cost"],
                edge_at_signal=ob["real_spread_pct"] / 100.0,
                metadata={
                    "early_market_id": c.early.market_id,
                    "late_market_id": c.late.market_id,
                    "early_cost": ob["ask_no_early"],
                    "late_cost": ob["ask_yes_late"],
                    "expected_pnl_pct": sizing["roi_min_pct"],
                    "target_outcome": "CORRIDOR",
                    "summary": f"Temporal Corridor: {signal.event_title}",
                    "platform": "polymarket"
                }
            )
        except Exception as e:
            logger.error(f"[TC] Ошибка логирования в Evaluation Engine: {e}", exc_info=True)

        found.append(signal)

        logger.info(
            f"[TC] ✅ {c.event.event_title[:45]} | "
            f"gap={c.date_gap_days}d | "
            f"теор={c.theoretical_spread_pct:.1f}% реал={ob['real_spread_pct']:.1f}% | "
            f"EV=${sizing['ev_usd']:.2f} | Q={quality:.2f}"
        )

    logger.info(f"[TC] Воронка: {stats}")
    logger.info(f"[TC] Итого сигналов: {len(found)}")
    return found
