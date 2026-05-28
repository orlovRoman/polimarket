from pydantic import BaseModel
from typing import Optional

class SyntheticCorridorSignal(BaseModel):
    signal_id: str
    event_slug: str
    event_title: str
    event_url: str
    
    lower_market_id: str
    lower_question: str
    lower_level: float
    lower_level_unit: str
    lower_price_yes: float
    lower_ask_yes: float
    
    upper_market_id: str
    upper_question: str
    upper_level: float
    upper_level_unit: str
    upper_price_yes: float
    upper_ask_no: float
    
    theoretical_cost: float
    theoretical_spread_pct: float
    
    real_cost: float
    real_spread_pct: float
    
    executable_contracts: float
    depth_5_lower: float
    depth_5_upper: float
    
    stake_lower_usd: float
    stake_upper_usd: float
    total_invested_usd: float
    contracts_lower: float
    contracts_upper: float
    pnl_above_upper_usd: float
    pnl_in_corridor_usd: float
    pnl_below_lower_usd: float
    min_guaranteed_usd: float
    roi_min_pct: float
    roi_max_pct: float
    
    pnl_s1_above: float
    pnl_s2_corridor: float
    pnl_s3_below: float
    
    created_at: str
    signal_type: str = "SYNTHETIC_CORRIDOR"
