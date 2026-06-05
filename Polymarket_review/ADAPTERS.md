# ADAPTERS.md — Адаптеры рынков предсказаний

## Общая идея
Каждая платформа (Polymarket, Kalshi, Metaculus, Manifold) реализует `BaseMarketAdapter`. Это позволяет SCOUT работать с любыми рынками через единый интерфейс.

## Пример регистрации адаптеров
```python
ADAPTERS = {
    'polymarket': PolymarketAdapter(),
    'kalshi': KalshiAdapter(),
    'metaculus': MetaculusAdapter(),
    'manifold': ManifoldAdapter(),
}
```

## Минимальный контракт адаптера
- `list_markets()` — вернуть активные рынки
- `get_market(id)` — подробно один рынок
- (фаза автотрейдинга) `place_order(...)` — исполнение сделки
