import logging
import requests
from datetime import datetime

from agents.polymarket_arbitrage_agent.src.synthetic.event_loader import load_events_with_levels_from_raw
from agents.polymarket_arbitrage_agent.src.synthetic.detector import find_violations
from agents.polymarket_arbitrage_agent.src.synthetic.orderbook import fetch_real_entry_prices
from agents.polymarket_arbitrage_agent.src.synthetic.sizing import compute_sizing
from agents.polymarket_arbitrage_agent.src.synthetic.models import SyntheticCorridorSignal
from agents.shared.python.db import save_synthetic_corridor, mark_synthetic_corridor_alerted
from services.polymarket_cache import get_raw_events
from agents.shared.adapters.polymarket import PolymarketAdapter
import config

logger = logging.getLogger("NexusPolyBot.SyntheticCorridor")

def run_synthetic_corridor_scan(
    poly_limit: int = 100,
    min_theoretical_spread_pct: float = 0.5,
    min_real_spread_pct: float = 1.5,
    min_volume: float = 10_000,
    min_executable_contracts: float = 50,
    budget_per_trade: float = 200.0,
) -> list[SyntheticCorridorSignal]:
    
    raw = get_raw_events(
        cache_key=f"poly_events_{poly_limit}",
        fetch_fn=lambda: PolymarketAdapter.fetch_raw_events(limit=poly_limit),
        ttl_seconds=config.POLY_EVENTS_CACHE_TTL_SECONDS,
    )
    events = load_events_with_levels_from_raw(
        raw_events=raw,
        min_volume_per_market=min_volume,
    )
    logger.info(f"[SCA] Событий с числовыми уровнями: {len(events)}")
    
    violations = find_violations(
        events,
        min_spread_pct=min_theoretical_spread_pct,
        min_volume_both=min_volume,
    )
    logger.info(f"[SCA] Теоретических нарушений: {len(violations)}")
    
    if not violations:
        return []
    
    session = requests.Session()
    found: list[SyntheticCorridorSignal] = []
    
    for v in violations:
        orderbook = fetch_real_entry_prices(v.lower, v.upper, session)
        
        if not orderbook:
            continue
        
        if orderbook["real_spread_pct"] < min_real_spread_pct:
            continue
        
        if orderbook["executable_size_contracts"] < min_executable_contracts:
            continue
        
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
            
            created_at=datetime.utcnow().isoformat(),
        )
        
        save_synthetic_corridor(signal)
        found.append(signal)
        
        logger.info(
            f"[SCA] ✅ {v.event.event_title[:45]} | "
            f"${v.lower.numeric_level}{v.lower.level_unit} vs "
            f"${v.upper.numeric_level}{v.upper.level_unit} | "
            f"теор={v.theoretical_spread_pct:.1f}% реал={orderbook['real_spread_pct']:.1f}% | "
            f"min_PnL=${sizing['min_guaranteed_usd']:.2f}"
        )
    
    logger.info(f"[SCA] Итого сигналов после фильтрации ордербуком: {len(found)}")
    return found
