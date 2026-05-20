# INTERFACES.md — Контракты и структуры данных

## Dataclass Market
```python
class Market(BaseModel):
    id: str
    platform: str  # polymarket, kalshi, metaculus, manifold
    title: str
    description: str
    url: str
    outcome: str
    price: float
    close_time: datetime
```

## Dataclass Signal
```python
class Signal(BaseModel):
    id: str
    type: Literal['undervaluation', 'arb', 'insider', 'news']
    market_id: str
    platform: str
    edge: float | None
    confidence: float
    priority: Literal['low', 'medium', 'high']
    summary: str
    details: str
    created_at: datetime
```

## Интерфейс критика (LLM Critic)
```python
class CriticProtocol(Protocol):
    name: str
    provider: str  # anthropic, deepseek, qwen, gemini, etc.

    def review(self, idea: dict) -> dict:
        """        Вход: idea = {market, model_prob, price, reasoning}
        Выход: {
            'agree': bool,
            'confidence': float,
            'comments': str,
        }
        """
```

## Интерфейс MarketAdapter
```python
class BaseMarketAdapter(ABC):
    name: str

    @abstractmethod
    def list_markets(self) -> list[Market]: ...

    @abstractmethod
    def get_market(self, market_id: str) -> Market: ...
```
