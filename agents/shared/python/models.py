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
    type: Literal['undervaluation', 'arb', 'insider', 'news', 'hype_pump']
    market_id: str
    platform: str
    edge: Optional[float] = None
    confidence: float
    priority: Literal['low', 'medium', 'high']
    summary: str
    details: str
    status: Literal['PENDING', 'EXECUTED', 'REJECTED', 'ARCHIVED'] = 'PENDING'
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AgentOpinion(BaseModel):
    agent_name: str
    market_id: str
    opinion: str
    confidence: float
    agree: bool
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

