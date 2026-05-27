from datetime import datetime, timezone
from typing import Literal, Optional, List
from pydantic import BaseModel, Field, field_validator

class Market(BaseModel):
    id: str
    platform: str  # polymarket, kalshi, metaculus, manifold
    title: str
    description: Optional[str] = None
    url: str
    outcome: str
    price: float
    close_time: datetime
    tokens: Optional[List[str]] = None  # clobTokenIds для CLOB API (orderbook)
    volume: Optional[float] = None      # Объём торгов (для ранжирования)
    condition_id: Optional[str] = None  # Для Onchain аналитики

class Signal(BaseModel):
    id: str
    type: str  # MISPRICING, SWING, etc.
    market_id: str
    platform: str
    target_outcome: str = "YES"
    edge: Optional[float] = None
    
    @field_validator('confidence', 'edge')
    @classmethod
    def clamp_percentages(cls, v):
        if v is None:
            return v
        return max(0.0, min(1.0, float(v)))

    confidence: float
    priority: Literal['low', 'medium', 'high']
    summary: str
    details: str
    
    # НОВЫЕ ПОЛЯ (со значением по умолчанию — backward-compatible)
    signal_cause: str = ""       # Причина сигнала
    signal_risk: str = ""        # Главный риск
    signal_verdict: str = ""     # Итог
    oracle_risk: str = ""        # Оценка расплывчатости формулировки / рисков оракула
    
    status: Literal['PENDING', 'EXECUTED', 'REJECTED', 'ARCHIVED', 'EVALUATED'] = 'PENDING'
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SwingSignal(BaseModel):
    id: str
    market_id: str
    platform: str
    type: str = "SWING"
    edge: Optional[float] = 0.0
    priority: str = "medium"
    summary: str = ""
    details: str = ""
    hype_potential: float
    recommendation: str        # "buy" | "ignore"
    target_outcome: str        # "YES" | "NO"
    target_exit_price: float
    confidence: float
    reasoning: str             
    
    # НОВЫЕ ПОЛЯ
    catalyst: str = ""                   
    catalyst_absence_reason: str = ""    
    swing_risk: str = ""                 
    swing_verdict: str = ""              
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AgentOpinion(BaseModel):
    agent_name: str
    market_id: str = ""
    opinion: str
    confidence: float
    agree: bool

    # НОВЫЕ ПОЛЯ
    orderbook_facts: str = ""    
    risk_assessment: str = ""    
    shadow_verdict: str = ""     
    liquidity_risk: str = "medium"  # "low" | "medium" | "high"

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class MarketCorrelation(BaseModel):
    """Обнаруженная корреляция между двумя рынками."""
    market_id_a: str
    market_id_b: str
    title_a: str
    title_b: str
    correlation_type: Literal['causal', 'inverse', 'arbitrage', 'thematic']
    description: str
    confidence: float
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class IdeaDecision(BaseModel):
    """Финальный результат консенсуса по рынку."""
    market_id: str
    status: Literal['saved', 'no_consensus', 'no_signal']
    scout_signal: Optional[Signal] = None
    swing_signal: Optional[SwingSignal] = None
    shadow_opinion: Optional[AgentOpinion] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class CrossArbitrageSignal(BaseModel):
    """Арбитражный сигнал между рынками с разных платформ."""

    # Рынок A
    market_a_id: str
    market_a_platform: str
    market_a_title: str
    market_a_price: float
    market_a_url: str

    # Рынок B
    market_b_id: str
    market_b_platform: str
    market_b_title: str
    market_b_price: float
    market_b_url: str

    # Арбитраж
    has_arbitrage: bool
    arbitrage_type: Literal[
        "price_divergence",       # Тип 1: прямое ценовое расхождение
        "logical_contradiction",  # Тип 2: логическое противоречие
        "pair_trade",             # Тип 3: парный трейд
        "none"
    ]
    spread_percent: float
    reasoning: str
    trade_instruction: str

    # Мета
    match_score: float
    status: Literal["new", "alerted", "expired"] = "new"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SignalDetails(BaseModel):
    agent_name: str = "SCOUT"
    target_outcome: Literal['YES', 'NO'] = "YES"
    estimated_probability: float = 0.5
    prompt_version: str = "v1"
    reasoning: str = ""
