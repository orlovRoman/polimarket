from dataclasses import dataclass
from typing import Optional
from .event_loader import PolyEvent, OutcomeMarket

@dataclass
class ViolationCandidate:
    """
    Пара рынков, где нарушена монотонность вероятностей.
    Инвариант: lower_market.numeric_level < upper_market.numeric_level
    """
    event: PolyEvent
    lower: OutcomeMarket
    upper: OutcomeMarket
    
    price_yes_lower: float
    price_yes_upper: float
    price_no_upper: float      # = 1 - price_yes_upper
    
    theoretical_cost: float          # price_yes_lower + price_no_upper
    theoretical_spread_pct: float    # (1 - theoretical_cost) * 100
    
    pnl_above_upper: float
    pnl_in_corridor: float
    pnl_below_lower: float
    min_pnl: float


def find_violations(
    events: list[PolyEvent],
    min_spread_pct: float = 1.0,
    min_volume_both: float = 10_000,
) -> list[ViolationCandidate]:
    """
    Ищет нарушения монотонности во всех парах уровней внутри каждого события.
    """
    candidates: list[ViolationCandidate] = []
    
    for event in events:
        levels = event.sorted_markets
        
        for i in range(len(levels)):
            for j in range(i + 1, len(levels)):
                lower = levels[i]
                upper = levels[j]
                
                if lower.volume < min_volume_both or upper.volume < min_volume_both:
                    continue
                
                p_lower = lower.price_yes
                p_upper = upper.price_yes
                p_no_upper = upper.price_no
                
                cost = p_lower + p_no_upper
                spread_pct = (1.0 - cost) * 100
                
                if spread_pct < min_spread_pct:
                    continue
                
                pnl_above_upper = 1.0 - cost
                pnl_in_corridor = 2.0 - cost
                pnl_below_lower = 1.0 - cost
                
                min_pnl = min(pnl_above_upper, pnl_in_corridor, pnl_below_lower)
                
                candidates.append(ViolationCandidate(
                    event=event,
                    lower=lower,
                    upper=upper,
                    price_yes_lower=p_lower,
                    price_yes_upper=p_upper,
                    price_no_upper=p_no_upper,
                    theoretical_cost=round(cost, 6),
                    theoretical_spread_pct=round(spread_pct, 3),
                    pnl_above_upper=round(pnl_above_upper, 4),
                    pnl_in_corridor=round(pnl_in_corridor, 4),
                    pnl_below_lower=round(pnl_below_lower, 4),
                    min_pnl=round(min_pnl, 4),
                ))
    
    return sorted(candidates, key=lambda c: c.theoretical_spread_pct, reverse=True)
