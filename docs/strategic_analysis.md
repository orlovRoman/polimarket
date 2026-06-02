# 🧭 Стратегический анализ направлений развития NexusPolyBot

> Дата: 2026-06-02 | Версия: 1.0

---

## 📊 TL;DR — Рекомендация

> **Развивать в первую очередь: Внутренний арбитраж (Synthetic + Temporal Corridors) + Whale-following как сигнальный слой.**  
> Кросс-платформенный арбитраж — второй приоритет, но с серьёзной оговоркой по Kalshi API.  
> Mispricing через LLM — самый рискованный с точки зрения reliability, требует калибровки.

---

## 1. Что говорит рынок: успешные кейсы

### 1.1 Реальная картина Polymarket (2024–2026)

По данным анализа миллионов сделок:
- **Только 0.51% кошельков** достигают стабильной прибыли
- Большинство "winners" — это **специализированные боты**, а не люди
- Среднее окно простого арбитража сократилось с **~12 секунд (2024)** до **~2.7 секунды (2026)**
- Топ-прибыльные стратегии требуют либо **скорости < 100ms**, либо **информационного преимущества**

### 1.2 Что реально работает по данным исследований

| Стратегия | Реальная прибыльность | Барьер входа |
|-----------|----------------------|--------------|
| **Structural arbitrage** (math violations) | Высокая, но требует HFT инфраструктуры | Очень высокий |
| **Domain expertise** (информационный edge) | Умеренная, зависит от домена | Умеренный |
| **Whale copying / Smart money following** | Умеренная, задержка съедает часть прибыли | Низкий (данные публичны) |
| **Cross-platform arb** (Poly ↔ Kalshi) | Умеренная (спреды 2–8% на нишевых рынках) | Средний |
| **Favorite compounding** (≥95¢ рынки) | Стабильная, но требует капитала и терпения | Низкий |

### 1.3 Выводы из академических исследований

- **Систематическое смещение**: рынки **недооценивают** крайние вероятности (favorite/longshot bias)
- **Временной эффект**: рынки плохо откалиброваны на **дальних горизонтах** (>30 дней)
- **Арбитраж остался неиспользованным** даже на выборах 2024 при объёме $2.4B — что подтверждает ценность вашего подхода
- Цены между платформами **регулярно расходятся** даже на одинаковых контрактах

### 1.4 Whale tracking — публичные инструменты конкурентов

- **Betmoar** — терминал с отслеживанием крупных позиций + новостная лента (самый комплексный)
- **Arkham Intelligence** — ончейн анализ через CTF Exchange контракты Polymarket
- **PolyAlertHub** — Telegram-алерты по входу/выходу китов в позиции
- **Polygonscan** — `matchOrders` функция для real-time трекинга капитала
- **polymarket-insider-detector** (GitHub) — p-value анализ + wallet clustering

> **Вывод**: конкуренты делают whale-трекинг, но большинство — через UI. Ваш бот с Telegram-алертами по ончейн-данным — это **правильная ниша**.

---

## 2. Оценка направлений — что есть в коде

### 2.1 🟢 Внутренний арбитраж (Synthetic + Temporal Corridors)

**Зрелость кода: ВЫСОКАЯ**

**Что уже работает:**
```
synthetic_corridor_scanner.py → find_violations() → fetch_real_entry_prices() → compute_sizing()
temporal_corridor_scanner.py  → find_candidates() → quality_score → TemporalCorridorSignal
```
- Полноценный детерминированный pipeline (без LLM — значит надёжно)
- Фильтрация по реальному orderbook (min_real_spread, min_executable_contracts)
- Compute sizing с PnL для 3 сценариев (above/corridor/below)
- Quality score для temporal арбитража
- Данные сохраняются в БД и уходят в Telegram

**Почему это лучшее направление:**
1. **Математически гарантированная прибыль** — если исполнение возможно (ордербук + спред > комиссии)
2. **Нет конкуренции с HFT** — потому что речь о ВНУТРЕННИХ нарушениях монотонности в рамках одного события (не speed race)
3. **Temporal corridors уникальны** — одновременная ставка на early NO + late YES даёт +EV при заблокированном исходе
4. **Код уже зрелый** — минимум доработки нужно

**Ключевые слабости:**
- `datetime.utcnow()` deprecated (строка 110) — мелкая правка
- Нет retry при ошибке orderbook fetch — теряет сигналы
- HTTP Session без timeout — может зависнуть
- `PolymarketAdapter.fetch_raw_events` вызывается как статический метод — хрупко

**Оценка потенциала: ⭐⭐⭐⭐⭐**

---

### 2.2 🟡 Кросс-платформенный арбитраж (Poly ↔ Kalshi)

**Зрелость кода: СРЕДНЯЯ**

**Что работает:**
- Keyword matching + Jaccard pre-filter (производительно)
- LLM verification пары в "серой зоне"
- Manual pairs support

**Критическая проблема:**
```python
# kalshi.py
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
# ↑ Это ТОЛЬКО elections endpoint.
# Все non-elections рынки (crypto, sports, science) — не охвачены.
```

**Реальные спреды по рынку:**
- Нишевые рынки: **2–8%** (интересно)
- Высоколиквидные (выборы, Fed): **0.5–2%** (после комиссий — почти ноль)
- Окно закрытия: **миллисекунды–минуты** (требует скорости)

**Главная проблема** — capital lock-up. Деньги заморожены до резолюции (дни/месяцы). Ротация капитала намного медленнее, чем в crypto арбитраже.

**Оценка потенциала: ⭐⭐⭐ (ограничен Kalshi API и capital lock)**

---

### 2.3 🟡 Mispricing / ScoutAgent (LLM-оценка вероятности)

**Зрелость кода: СРЕДНЯЯ**

**Как формируется edge:**
- LLM делает вызов с grounded context (RSS, Reddit, Wikipedia, Google Trends, HN)
- Edge = разница между `estimated_probability` (LLM) и `market price`
- Onchain scorer корректирует edge детерминированно

**Фундаментальная проблема:**
```
Нет backtesting. Нет Brier score. Нет исторической калибровки.
MIN_SCOUT_EDGE = 0.10 — пороговое значение без доказательной базы.
```

**Что говорит академическая наука:**
- Рынки систематически **недооценивают фаворитов** (favourite/longshot bias) → LLM, обученный на интернет-данных, унаследует тот же bias
- **Временной горизонт важен** — рынки плохо откалиброваны на дальних горизонтах, где LLM-edge теоретически больше

**Потенциал есть, но реализовать правильно — сложно:**
- Нужен backtesting pipeline (записывать предсказания → смотреть resolution → считать Brier score)
- Нужна fine-tuning/RLHF по исходам рынков
- Без этого — систематическая ошибка неизвестного знака и размера

**Оценка потенциала: ⭐⭐⭐ (высокий потенциал, но высокий риск без калибровки)**

---

### 2.4 🔴 Insider Detection / Whale Tracking

**Зрелость кода: НИЗКАЯ (MVP)**

**Что сделано:**
```
onchain_provider.py  → get_recent_trades() + get_top_positions()
smart_money.py       → analyze_smart_money() → топ-5 кошельков по объёму
onchain_scorer.py    → score -1.0..+1.0 (детерминированный)
wallet_tracker.py    → сохраняет сделки > $500
onchain_trend_alert  → volume spike x3 за 2ч → alert
whale_gate.py        → блокирует LLM если known whales против сигнала
```

**Что НЕ сделано:**
- Нет реального insider detection — только volume spikes (грубая эвристика)
- `known_whales` — только из своей БД по win_rate, нет внешнего источника
- Нет интеграции с Arkham, Nansen, Dune Analytics
- Нет wallet clustering (группировка связанных кошельков)
- Нет p-value анализа (нетипичное поведение перед событиями)

**Почему это всё равно ценно:**
Даже MVP whale-gate уже **полезен как фильтр** (блокирует сигналы против умных денег). Это вторичная роль, но она работает.

**Оценка потенциала: ⭐⭐⭐⭐ (при инвестиции в API-интеграцию Arkham + wallet clustering)**

---

## 3. Аудит архитектуры — ключевые проблемы

### 3.1 🔴 Критические проблемы

**God Object: `CoreEngine` (engine.py, 748 строк)**
- Создаёт агентов, управляет адаптерами, форматирует Telegram UI, вызывает ончейн-провайдер — всё в одном классе
- Метод `_run_team_discussion_inner` — **350 строк** — смесь бизнес-логики, fetching, UI, error handling
- Singleton паттерн → тестирование затруднено

**God Object: `workflow.py` (715 строк)**
- `process_consensus` — **200 строк**: принимает решение + форматирует Telegram + сохраняет аудит + сохраняет эпизоды

**3 bare `except:` (silent data loss):**
```python
# scripts/resolve_markets.py:33
except:  # без логирования

# services/notifications.py:290
except:  # без логирования

# telegram/bot.py:830
except:  # без логирования
```

### 3.2 🟠 Дублирование кода

| Где | Что |
|-----|-----|
| `engine.py:388-402` и `engine.py:438-451` | Создание `OrderbookSnapshot` — точный copy-paste |
| `polymarket.py:169-224` и `polymarket.py:249-302` | Парсинг рынков — 90% одинаковый код |
| `math_filter.py` и `market_matcher.py` | Два отдельных `STOPWORDS` — не синхронизированы |

### 3.3 🟠 Hardcoded значения (не в config.py)

| Файл | Строка | Значение |
|------|--------|----------|
| `wallet_tracker.py` | 9 | `_MIN_TRADE_USD = 500.0` |
| `onchain_provider.py` | 15 | `CACHE_TTL = 300` |
| `onchain_scorer.py` | 28 | `total < 200` (magic number) |
| `onchain_scorer.py` | 84 | `total / 50_000` (magic number) |
| `arbitrage_workflow.py` | 190 | `max_safe_size=100.0` |
| `engine.py` | 348 | `min_spread_pct=5.0` |

### 3.4 🟠 Concurrency проблемы

- `engine.py:218` — `self.state` (dict) не защищён блокировкой — race condition при параллельных вызовах
- `semantic_filter.py:12-14` — глобальные `_model_failed`, `_model_failed_at` без threading lock
- `onchain_provider.py` — `_cache` dict растёт бесконечно (нет TTL eviction)
- `engine.py:342-351` — `asyncio.new_event_loop()` создаётся каждый раз для Math Gate — утечка ресурсов

### 3.5 🟡 Конфигурация

- `.env.example` использует `GEMINI_API_KEY`, но `config.py` читает `GOOGLE_API_KEY` — **несоответствие имён!**
- `.env.example` неполный — отсутствуют `TELEGRAM_CHAT_ID`, `TG_API_ID/HASH/PHONE`, `ARB_*` параметры
- Kalshi API endpoint — только `elections` (api.**elections**.kalshi.com) — ограничивает покрытие

### 3.6 Покрытие тестами

| Модуль | Оценка |
|--------|--------|
| math_filter, workflow, engine | 🟢 Хорошее |
| arb_router, arb_scanner | 🟡 Среднее |
| market_matcher | 🔴 Минимальное |
| KalshiAdapter | 🔴 Нет тестов вообще |
| synthetic_corridor | 🔴 1 файл |
| wallet_tracker | 🔴 1 файл |

---

## 4. Итоговая матрица решений

| Направление | Потенциал прибыли | Зрелость кода | Инвест. в доработку | Конкуренция | **Приоритет** |
|-------------|-------------------|---------------|---------------------|-------------|---------------|
| **Внутренний арбитраж** | 🟢 Высокий (math-гарантия) | 🟢 Высокая | 🟢 Малая | 🟢 Низкая | **🥇 #1** |
| **Whale following** | 🟢 Высокий (публичные данные) | 🟡 MVP | 🟡 Средняя | 🟡 Средняя | **🥈 #2** |
| **Кросс-платформа** | 🟡 Средний (capital lock) | 🟡 Средняя | 🟡 Средняя | 🔴 Высокая (HFT) | **🥉 #3** |
| **Mispricing LLM** | 🟡 Средний (без калибровки — риск) | 🟡 Средняя | 🔴 Большая (backtesting) | 🟡 Средняя | **#4** |

---

## 5. Авторская рекомендация

### Основная идея: **"Math-first + Whale confirmation"**

Вместо того чтобы выбирать одно направление, предлагаю **гибридный сигнальный стек**:

```
┌─────────────────────────────────────────────────────┐
│  LAYER 1: Математический арбитраж (приоритет #1)    │
│  ──────────────────────────────────────────────────  │
│  Synthetic Corridors  →  нарушения монотонности      │
│  Temporal Corridors   →  early NO + late YES         │
│  Math-гарантированный PnL при исполнении             │
└─────────────────────────────────────────────────────┘
           ↓ если нет math-арбитража
┌─────────────────────────────────────────────────────┐
│  LAYER 2: Whale Signal Filter (приоритет #2)        │
│  ──────────────────────────────────────────────────  │
│  Мониторинг кошельков с win_rate > 60%               │
│  Copy-trading с задержкой (latency не критична)      │
│  Arkham API для идентификации "умных" кошельков      │
│  Алерты в Telegram при входе кита > $5k             │
└─────────────────────────────────────────────────────┘
           ↓ как дополнение
┌─────────────────────────────────────────────────────┐
│  LAYER 3: LLM Scout (приоритет #4 — только с        │
│  backtesting калибровкой)                            │
│  ──────────────────────────────────────────────────  │
│  Текущая реализация оставить как есть               │
│  Добавить Brier score tracking для каждого сигнала  │
│  Через 200+ резолюций → пересчитать пороги          │
└─────────────────────────────────────────────────────┘
```

### Почему не кросс-платформа?

1. **Kalshi API** ограничен только elections — нельзя покрыть crypto, sports и т.д. без платного доступа к полному API
2. **Capital lock-up** — деньги заморожены до резолюции: ротация намного медленнее
3. **Конкуренция HFT** на ликвидных рынках — спреды закрываются за секунды
4. Имеет смысл как **вспомогательный канал** при больших выборных циклах

---

## 6. Топ-10 технических задач (приоритизированные)

| # | Приоритет | Задача | Файл |
|---|-----------|--------|------|
| 1 | 🔴 | Разбить `_run_team_discussion_inner` (350 строк) на 5-6 методов | `core/engine.py` |
| 2 | 🔴 | Устранить 3 bare `except:` (потеря данных в тишине) | `bot.py`, `notifications.py`, `resolve_markets.py` |
| 3 | 🟠 | Retry при ошибке orderbook в corridor scanners | `synthetic/temporal_corridor_scanner.py` |
| 4 | 🟠 | Исправить `.env.example` + устранить GEMINI→GOOGLE несоответствие | `.env.example`, `config.py` |
| 5 | 🟠 | Вынести hardcoded значения в `config.py` | `wallet_tracker.py`, `onchain_provider.py`, `onchain_scorer.py` |
| 6 | 🟠 | Заменить `datetime.utcnow()` → `datetime.now(timezone.utc)` | `synthetic_corridor_scanner.py:110` |
| 7 | 🟠 | Thread safety для `self.state` в CoreEngine | `core/engine.py:218` |
| 8 | 🟡 | Добавить тесты для KalshiAdapter (0 тестов!) | новый файл `tests/` |
| 9 | 🟡 | Интеграция Arkham API для whale identification | новый `services/arkham_provider.py` |
| 10 | 🟡 | Backtesting framework: запись предсказаний ScoutAgent → Brier score | новый `core/backtesting.py` |

---

## 7. Конкретный следующий шаг

**Рекомендуемый sprint:**

1. **Неделя 1**: Исправить критические баги (#1-4 из таблицы выше)
2. **Неделя 2**: Улучшить Whale следующий — добавить Arkham API + wallet clustering
3. **Неделя 3**: Запустить corridor scanners в production, добавить retry + timeout
4. **Неделя 4**: Начать записывать Brier score для ScoutAgent (фоновый процесс)

---

*Источники: аудит кода NexusPolyBot (9687b0a5), веб-исследование по polymarket strategies/arbitrage/whale tracking (2024–2026), академические источники (arXiv, NBER, ResearchGate)*
