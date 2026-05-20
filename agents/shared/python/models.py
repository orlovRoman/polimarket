from datetime import datetime
from typing import Literal, Optional
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

class Signal(BaseModel):
    id: str
    type: Literal['undervaluation', 'arb', 'insider', 'news']
    market_id: str
    platform: str
    edge: Optional[float] = None
    confidence: float
    priority: Literal['low', 'medium', 'high']
    summary: str
    details: str
    status: Literal['PENDING', 'EXECUTED', 'REJECTED', 'ARCHIVED'] = 'PENDING'
    created_at: datetime = Field(default_factory=datetime.utcnow)

class AgentOpinion(BaseModel):
    agent_name: str
    market_id: str
    opinion: str
    confidence: float
    agree: bool
    created_at: datetime = Field(default_factory=datetime.utcnow)
