from typing import List, Optional, Literal, Any
from datetime import datetime
from pydantic import BaseModel, Field
from core.models import Market

class WalletInfo(BaseModel):
    address: str
    alias: Optional[str] = None
    win_rate: Optional[float] = None
    side: str  # "YES" | "NO"
    volume_usd: float

class SmartMoneySummary(BaseModel):
    available: bool
    total_yes_usd: float = 0.0
    total_no_usd: float = 0.0
    yes_dominance: float = 0.5
    top_wallets: List[str] = Field(default_factory=list)
    summary: str = "Крупных сделок не найдено."
    wallets_list: List[WalletInfo] = Field(default_factory=list)

class OrderbookSnapshot(BaseModel):
    top_bid: Optional[float] = None
    top_ask: Optional[float] = None
    spread_cents: Optional[float] = None   # уже в центах (0.005 → 0.5¢)
    bid_depth_5: Optional[float] = None    # объём 5 лучших bid
    ask_depth_5: Optional[float] = None

class MarketContext(BaseModel):
    """
    Единый контекст для всех агентов, содержащий все собранные данные по рынку.
    """
    market: Market
    orderbook: Optional[OrderbookSnapshot] = None  # Единый источник ордербука
    news_titles: List[str] = Field(default_factory=list)
    reddit_posts: List[str] = Field(default_factory=list)
    wiki_context: List[str] = Field(default_factory=list, description="Массив контекста из Wikipedia")
    trends_data: str = "Google Trends: данные не загружены"
    hn_posts: List[str] = Field(default_factory=list)
    smart_money: Optional[SmartMoneySummary] = None
    correlation_hint: str = ""
    search_query: str = ""
    
    # --- Event metadata ---
    onchain_annotation: str = ""
    trigger_type: Literal["scheduled", "event_driven", "manual"] = "scheduled"
    source_url: Optional[str] = None
    source_text: Optional[str] = None
    triggered_at: Optional[datetime] = None
    
    # Можно расширять (X/Twitter, Onchain metrics, etc.)
    math_filter_result: Optional[Any] = None
    grounded_context: str = ""
    velocity_annotation: str = ""
    orderbook_shape_annotation: str = ""
