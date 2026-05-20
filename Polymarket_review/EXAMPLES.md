# EXAMPLES.md — Примеры запросов и ответов

## Пример сигнала в Telegram
```text
🟢 [НЕДООЦЕНКА] SCOUT
Рынок: "Bitcoin Up on April 29?" (Polymarket)
Цена рынка: 0.18
Оценка модели: 0.42
Edge: +24 п.п.
Комментарий: модель считает, что вероятность роста выше из-за последних макро-новостей.
Ссылка: https://polymarket.com/event/...
```

## Пример идеи в state
```json
{
  "market_id": "abc123",
  "platform": "polymarket",
  "price": 0.18,
  "model_prob": 0.42,
  "reasoning": "...",
  "critics": []
}
```
