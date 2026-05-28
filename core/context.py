from typing import List, Optional
from pydantic import BaseModel, Field
from core.models import Market

class SmartMoneySummary(BaseModel):
    available: bool
    total_yes_usd: float = 0.0
    total_no_usd: float = 0.0
    yes_dominance: float = 0.5
    top_wallets: List[str] = Field(default_factory=list)
    summary: str = "Крупных сделок не найдено."

class MarketContext(BaseModel):
    """
    Единый контекст для всех агентов, содержащий все собранные данные по рынку.
    """
    market: Market
    news_titles: List[str] = Field(default_factory=list)
    reddit_posts: List[str] = Field(default_factory=list)
    wiki_context: str = ""
    smart_money: Optional[SmartMoneySummary] = None
    correlation_hint: str = ""
    
    # Можно расширять (X/Twitter, Onchain metrics, etc.)
