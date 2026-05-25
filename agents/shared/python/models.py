from datetime import datetime, timezone
from typing import Literal, Optional, List
from pydantic import BaseModel, Field

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

class Signal(BaseModel):
    id: str
    type: str  # MISPRICING, SWING, etc.
    market_id: str
    platform: str
    edge: Optional[float] = None
    confidence: float
    priority: Literal['low', 'medium', 'high']
    summary: str
    details: str
    
    # НОВЫЕ ПОЛЯ (со значением по умолчанию — backward-compatible)
    signal_cause: str = ""       # Причина сигнала
    signal_risk: str = ""        # Главный риск
    signal_verdict: str = ""     # Итог
    
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
    market_id: str
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

