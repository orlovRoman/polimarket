from pydantic import BaseModel
from datetime import datetime

class TemporalLeg(BaseModel):
    market_id: str
    question: str
    market_url: str
    expiry: datetime          # Market.close_time
    price_yes: float          # mid-price из API
    ask_price: float          # реальная цена входа из ордербука
    side: str                 # "NO" или "YES"
    entry_cost: float         # ask_price если YES, (1 - bid_price) если NO
    token_id: str | None      # clobTokenIds[0] для ордербука
    volume: float

class TemporalCorridorSignal(BaseModel):
    signal_id: str            # f"{early_id}__{late_id}"
    event_slug: str
    event_title: str
    event_url: str

    early_leg: TemporalLeg    # NO на раннюю дату
    late_leg: TemporalLeg     # YES на позднюю дату

    date_gap_days: int        # разнос экспираций в днях

    # Математика через implied probabilities (без LLM)
    p_early: float            # P(event <= early_expiry) = price_yes_early
    p_late: float             # P(event <= late_expiry)  = price_yes_late
    p_in_corridor: float      # P(early < event <= late) = p_late - p_early
    p_before_early: float     # = p_early
    p_never: float            # = 1 - p_late

    # Стоимость конструкции (теоретическая и реальная)
    theoretical_cost: float   # (1 - p_early) + p_late = NO_cost + YES_cost
    real_cost: float          # по реальным ask-ценам из ордербука
    theoretical_spread_pct: float
    real_spread_pct: float

    # PnL по трём сценариям (на $budget)
    pnl_s1_before_early: float   # анонс ДО ранней даты
    pnl_s2_in_corridor: float    # анонс В коридоре — лучший случай
    pnl_s3_never: float          # нет анонса — выходим вручную

    # Sizing
    early_stake_usd: float
    late_stake_usd: float
    early_contracts: float
    late_contracts: float
    ev_usd: float
    roi_pct: float

    # Качество сигнала (0..1) — без LLM
    quality_score: float

    # Детерминированный план выхода
    exit_rule: str

    created_at: datetime
    signal_type: str = "TEMPORAL_CORRIDOR"
