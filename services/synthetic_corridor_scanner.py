import logging
import requests
from datetime import datetime, timezone

from agents.polymarket_arbitrage_agent.src.synthetic.event_loader import load_events_with_levels_from_raw
from agents.polymarket_arbitrage_agent.src.synthetic.detector import find_violations
from agents.polymarket_arbitrage_agent.src.synthetic.orderbook import fetch_real_entry_prices
from agents.polymarket_arbitrage_agent.src.synthetic.sizing import compute_sizing
from agents.polymarket_arbitrage_agent.src.synthetic.models import SyntheticCorridorSignal
from agents.shared.python.db import save_synthetic_corridor, mark_synthetic_corridor_alerted
from agents.shared.adapters.polymarket import PolymarketAdapter
from services.http_utils import make_session_with_timeout, fetch_with_retry
import config

logger = logging.getLogger("NexusPolyBot.SyntheticCorridor")

def run_synthetic_corridor_scan(
    poly_limit: int = 300,
    min_theoretical_spread_pct: float = 0.3,
    min_real_spread_pct: float = None,
    min_volume: float = 1_000,
    min_executable_contracts: float = 3,
    budget_per_trade: float = 200.0,
) -> list[SyntheticCorridorSignal]:
    from core.config_provider import ConfigProvider
    if min_real_spread_pct is None:
        min_real_spread_pct = ConfigProvider.get_min_spread_sync("synthetic_corridor") * 100.0
    
    logger.info(f"[SCA-ДИАГ] min_volume={min_volume}, min_real_spread={min_real_spread_pct}%, min_exec={min_executable_contracts}")
    
    from services.poly_fetch import fetch_poly_events
    adapter = PolymarketAdapter()
    raw = fetch_poly_events(adapter, limit=poly_limit)
    logger.info(f"[SCA-ДИАГ] Загружено сырых событий: {len(raw)}")
    multi = [e for e in raw if len(e.get("markets", [])) >= 2]
    logger.info(f"[SCA-ДИАГ] Из них multi-market (>=2): {len(multi)}")

    events, loader_stats = load_events_with_levels_from_raw(
        raw_events=raw,
        min_markets=2,
        min_volume_per_market=min_volume,
    )
    logger.info(f"[SCA] Уникальных событий после парсинга: {len(events)}")
    
    violations = find_violations(
        events,
        min_spread_pct=min_theoretical_spread_pct,
        min_volume_both=min_volume,
    )
    logger.info(f"[SCA] Теоретических нарушений: {len(violations)}")
    
    if not violations:
        logger.info("[SCA] Арбитражных возможностей не обнаружено (нет теоретических нарушений).")
        return []
    
    session = make_session_with_timeout()
    found: list[SyntheticCorridorSignal] = []
    
    stats = {"no_orderbook": 0, "low_spread": 0, "low_size": 0, "passed": 0}
    
    for v in violations:
        orderbook = fetch_with_retry(fetch_real_entry_prices, v.lower, v.upper, session)
        
        if not orderbook:
            stats["no_orderbook"] += 1
            logger.debug(f"[SCA] Ордербук недоступен для пары {v.lower.market_id} / {v.upper.market_id}")
            continue
        
        if orderbook["real_spread_pct"] < min_real_spread_pct:
            stats["low_spread"] += 1
            continue
        
        if orderbook["executable_size_contracts"] < min_executable_contracts:
            stats["low_size"] += 1
            continue
            
        stats["passed"] += 1
        
        sizing = compute_sizing(
            ask_yes_lower=orderbook["ask_yes_lower"],
            ask_no_upper=orderbook["ask_no_upper"],
            budget=budget_per_trade,
        )
        
        signal = SyntheticCorridorSignal(
            signal_id=f"{v.lower.market_id}__{v.upper.market_id}",
            event_slug=v.event.event_slug,
            event_title=v.event.event_title,
            event_url=v.event.event_url,
            
            lower_market_id=v.lower.market_id,
            lower_question=v.lower.question,
            lower_level=v.lower.numeric_level,
            lower_level_unit=v.lower.level_unit,
            lower_price_yes=v.price_yes_lower,
            lower_ask_yes=orderbook["ask_yes_lower"],
            
            upper_market_id=v.upper.market_id,
            upper_question=v.upper.question,
            upper_level=v.upper.numeric_level,
            upper_level_unit=v.upper.level_unit,
            upper_price_yes=v.price_yes_upper,
            upper_ask_no=orderbook["ask_no_upper"],
            
            theoretical_cost=v.theoretical_cost,
            theoretical_spread_pct=v.theoretical_spread_pct,
            real_cost=orderbook["real_cost"],
            real_spread_pct=orderbook["real_spread_pct"],
            
            executable_contracts=orderbook["executable_size_contracts"],
            depth_5_lower=orderbook["depth_5_lower"],
            depth_5_upper=orderbook["depth_5_upper"],
            
            **sizing,
            
            pnl_s1_above=sizing["pnl_above_upper_usd"],
            pnl_s2_corridor=sizing["pnl_in_corridor_usd"],
            pnl_s3_below=sizing["pnl_below_lower_usd"],
            
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        
        save_synthetic_corridor(signal)
        
        # Запись в Evaluation Engine
        try:
            close_time = None
            try:
                from agents.shared.python.db import get_connection
                with get_connection() as conn:
                    m_row = conn.execute("SELECT close_time FROM markets WHERE id = ?", (v.lower.market_id,)).fetchone()
                    if m_row and m_row["close_time"]:
                        close_time = m_row["close_time"]
            except Exception:
                pass

            from core.eval.signal_logger import SignalLogger, StrategyType
            logger_eval = SignalLogger()
            logger_eval.log_signal(
                signal_id=signal.signal_id,
                strategy_type=StrategyType.SYNTHETIC_CORRIDOR,
                market_id=v.lower.market_id,
                predicted_probability=0.95,
                market_price_at_signal=orderbook["real_cost"],
                edge_at_signal=orderbook["real_spread_pct"] / 100.0,
                metadata={
                    "close_time": close_time,
                    "lower_market_id": v.lower.market_id,
                    "upper_market_id": v.upper.market_id,
                    "lower_price_yes": v.price_yes_lower,
                    "upper_price_yes": v.price_yes_upper,
                    "real_spread_pct": orderbook["real_spread_pct"],
                    "expected_pnl_pct": sizing["roi_min_pct"],
                    "target_outcome": "CORRIDOR",
                    "summary": f"Synthetic Corridor: {signal.event_title}",
                    "platform": "polymarket"
                }
            )
        except Exception as e:
            logger.error(f"[SCA] Ошибка логирования в Evaluation Engine: {e}", exc_info=True)

        found.append(signal)
        
        logger.info(
            f"[SCA] ✅ {v.event.event_title[:45]} | "
            f"${v.lower.numeric_level}{v.lower.level_unit} vs "
            f"${v.upper.numeric_level}{v.upper.level_unit} | "
            f"теор={v.theoretical_spread_pct:.1f}% реал={orderbook['real_spread_pct']:.1f}% | "
            f"min_PnL=${sizing['min_guaranteed_usd']:.2f}"
        )
    
    logger.info(
        f"[SCA] Воронка ордербука (проверено {len(violations)} пар): "
        f"недоступен={stats['no_orderbook']}, "
        f"низкий спред={stats['low_spread']}, "
        f"малый объем={stats['low_size']} -> "
        f"ПРОШЛИ={stats['passed']}. "
        f"(Из-за суммы отброшено {loader_stats.get('low_sum', 0)} событий)"
    )
    return found
