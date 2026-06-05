# Polimarket — План рефакторинга: «Агенты генерируют мало идей»

**Репозиторий:** [orlovRoman/polimarket](https://github.com/orlovRoman/polimarket)  
**Дата анализа:** 2026-05-25  
**Статус:** Готов к реализации

---

## Исполнительное резюме

Проблема «мало идей» — это не баг одного места, а результат 5 независимых фильтров, стоящих в последовательном pipeline. Каждый из них по отдельности разумен, но вместе они дают воронку с конверсией ~0.1% от исходного числа рынков. На вход подаётся ~1000 рынков, на выход — 0–1 идея за 30-минутный цикл.

Все правки сгруппированы в 3 итерации: **MVP (0 рисков)** → **Итерация 1 (повышение recall)** → **Итерация 2 (качество scoring)**. Каждый шаг независим и тестируем.

---

## Диагностика: реальная воронка

| Шаг | Входных рынков | Выходных | Потеря | Файл |
|---|---|---|---|---|
| Polymarket API | ~1000 | — | — | `adapters/polymarket.py` |
| NEXUS Top-N | 1000 | 30 | −97% | `agents/orchestrator/src/agent.py` |
| `scan_limit` (дефолт 10) | 30 | 10 | −67% | `run_team.py:~L167` |
| `_filter` (cooldown + price-dead) | 10 | 2–4 | −60–80% | `market_selector.py:_filter()` |
| Cooldown + price-diff < 3% в run_team | 2–4 | 1–3 | −25–50% | `run_team.py:~L232` |
| SCOUT Edge > min_edge (дефолт 10%) | 1–3 | 0–2 | −33–100% | `polymarket_mispricing_agent/src/agent.py` |
| Двойной консенсус AND (SHADOW + HERALD) | 0–2 | 0–1 | −50–100% | `run_team.py:~L295` |

**Итог:** 0–1 идея при 1000 рынках на входе — математически ожидаемый результат.

---

## Корневые причины

### Причина 1 — `scan_limit` = 10 (КРИТИЧНО)

**Файл:** `run_team.py`, строка ~167

```python
scan_limit = int(db.get_memory("scan_limit") or 10)
```

При дефолте в 10 рынков и работающем NEXUS (Top-30) берутся первые 10 из 30 кандидатов. После cooldown-фильтра остаётся 2–4. Это фундаментальное ограничение throughput.

**Решение:** Поднять дефолт до 30 и добавить переменную окружения.

---

### Причина 2 — Двойной `cooldown` на одном рынке (КРИТИЧНО)

**Файл:** `run_team.py`, строка ~232; `market_selector.py`, метод `_filter()`

Рынок фильтруется **дважды** независимо:
1. `MarketSelector._filter()` убирает рынки по `MARKET_COOLDOWN_HOURS` (4 ч).
2. `run_team.py` снова проверяет `is_cooldown` + `price_diff < 3%`.

В итоге рынок, который прошёл `_filter()`, может быть ещё раз пропущен в `run_team.py`. Дублирующая логика запутывает поведение и создаёт неожиданные скипы.

**Решение:** Убрать дублирующую проверку из `run_team.py`, оставить единственную точку фильтрации в `MarketSelector._filter()`.

---

### Причина 3 — `price_diff < 0.03` блокирует стабильно недооценённые рынки (ВАЖНО)

**Файл:** `run_team.py`, строка ~232

```python
if price_diff < 0.03 and is_cooldown:
    continue  # Пропускаем
```

Рынок с неизменной ценой и реальным Edge 8% будет пропускаться каждые 6 часов. Именно такие рынки (стабильно неправильно оценённые рынком) — самые ценные для prediction market trading.

**Решение:** Убрать `price_diff` из условия скипа. Cooldown по времени достаточен.

---

### Причина 4 — `min_edge` = 10% отсекает реальные неэффективности (ВАЖНО)

**Файл:** `agents/polymarket_mispricing_agent/src/agent.py`, строка ~99

```python
min_edge = get_memory("min_edge")
if min_edge is None:
    min_edge = 0.10
```

На Polymarket устойчивые неэффективности обычно 4–8%. Порог 10% отсекает большинство из них. Это объясняет, почему SCOUT нередко возвращает `None` даже при правильной оценке модели.

**Решение:** Снизить дефолт до 0.05. Добавить настройку через `.env` `MIN_EDGE`.

---

### Причина 5 — Консенсус AND без градаций (УМЕРЕННО)

**Файл:** `run_team.py`, строка ~295

```python
if opinion_shadow and opinion_herald and \
   opinion_shadow.agree and opinion_herald.agree and \
   opinion_shadow.confidence > 0.6 and opinion_herald.confidence > 0.6:
```

Логика `AND` с порогом уверенности 0.6 — строгая, но разумная. Проблема не в самом пороге, а в том, что промпты SHADOW и HERALD не оптимизированы под высокий `recall`. Если модели смещены в сторону осторожности («лучше пропустить, чем одобрить»), они будут генерировать `agree=False` даже при хорошей идее.

**Решение:** Добавить логирование причин отказа (`opinion`) для последующей eval-метрики, рассмотреть логику OR-fallback для одного из агентов.

---

### Причина 6 — Нет логов rejection-причин (ВАЖНО для диагностики)

Сейчас при скипе рынка или отказе консенсуса в лог пишется только краткое сообщение. Нет структурированных данных о том, **почему** идея была отклонена: у SHADOW agree=False или confidence < 0.6?

**Решение:** Добавить структурированный лог в формате `rejection_reason` в таблицу `signals` или отдельную таблицу `idea_audit`.

---

## Пошаговый план (для агента-программиста)

### Итерация MVP — «Без изменений кода, только конфиг» (30 минут)

Цель: 3–5× больше идей без единой правки кода.

**Шаг MVP-1.** Через Telegram-бот выполнить:
```
/settings scan_limit 30
/settings min_edge 0.06
```
Это сразу поднимает throughput воронки с 10 до 30 рынков и снижает Edge-порог.

**Шаг MVP-2.** Убедиться, что `.env` содержит:
```env
MARKET_COOLDOWN_HOURS=4
MARKET_OFFSET_MAX=200
```
Проверить файл `config.py` — дефолты уже выставлены корректно.

**Ожидаемый результат:** 2–5 идей за цикл вместо 0–1.

---

### Итерация 1 — «Убрать дублирующую фильтрацию» (2–3 часа)

**Шаг 1-1. Рефакторинг `run_team.py` — убрать дублирующий cooldown**

Файл: `run_team.py`

Найти блок (~строки 220–245):
```python
last_price = get_last_analyzed_price(m.id)
if last_price is not None and not market_id:
    price_diff = abs(last_price - m.price)
    is_cooldown = m.id in cooldown_markets
    if price_diff < 0.03 and is_cooldown:
        log(f"...пропускаем")
        save_price_point(m.id, m.price)
        update_state(scout_status="⚪️ Пропущен (Кулдаун)", ...)
        continue
    elif not is_cooldown:
        log(f"...Истек 6-часовой кулдаун...")
    else:
        log(f"...Цена изменилась...")
```

Заменить на:
```python
last_price = get_last_analyzed_price(m.id)
if last_price is not None and not market_id:
    # Только логируем причину re-анализа, фильтрация уже сделана в MarketSelector
    price_diff = abs(last_price - m.price)
    if price_diff >= 0.03:
        log(f"\n[РЫНОК]: {m.title} (Цена изменилась: {last_price:.4f} -> {m.price:.4f})")
    else:
        log(f"\n[РЫНОК]: {m.title} (Плановый пересмотр, цена стабильна)")
else:
    log(f"\n[РЫНОК]: {m.title} ({'Точечный анализ' if market_id else 'Новый рынок'})")
```

**Шаг 1-2. Добавить дефолт `scan_limit` в `config.py`**

Файл: `config.py`

```python
# Стратегия скана
SCAN_LIMIT_DEFAULT = int(os.getenv("SCAN_LIMIT", "30"))  # Было хардкод 10 в run_team.py
MIN_EDGE_DEFAULT = float(os.getenv("MIN_EDGE", "0.05"))   # Было хардкод 0.10 в agent.py
```

Файл: `run_team.py`, строка ~167

```python
# Было:
scan_limit = int(db.get_memory("scan_limit") or 10)
# Стало:
from config import SCAN_LIMIT_DEFAULT
scan_limit = int(db.get_memory("scan_limit") or SCAN_LIMIT_DEFAULT)
```

Файл: `agents/polymarket_mispricing_agent/src/agent.py`, строка ~99

```python
# Было:
min_edge = get_memory("min_edge")
if min_edge is None:
    min_edge = 0.10
# Стало:
from config import MIN_EDGE_DEFAULT
min_edge = float(get_memory("min_edge") or MIN_EDGE_DEFAULT)
```

**Шаг 1-3. Написать тесты**

Файл: `tests/test_pipeline_filters.py` (новый)

```python
"""Unit-тесты для логики фильтрации рынков."""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from agents.shared.python.models import Market
from agents.shared.python.market_selector import MarketSelector


def make_market(price=0.5, days_to_close=7, market_id="test-1"):
    return Market(
        id=market_id,
        platform="polymarket",
        title="Test Market",
        description="desc",
        url="https://polymarket.com/test",
        outcome="YES",
        price=price,
        close_time=datetime.now(timezone.utc) + timedelta(days=days_to_close)
    )


def test_filter_removes_expired_markets():
    """Рынки с close_time в прошлом должны быть отфильтрованы."""
    adapter = MagicMock()
    selector = MarketSelector(adapter)
    
    expired = make_market(days_to_close=-1)
    active = make_market(days_to_close=7)
    
    with patch("agents.shared.python.market_selector.get_markets_on_cooldown", return_value=set()):
        result = selector._filter([expired, active])
    
    assert len(result) == 1
    assert result[0].id == active.id


def test_filter_removes_cooldown_markets():
    """Рынки на кулдауне должны быть отфильтрованы."""
    adapter = MagicMock()
    selector = MarketSelector(adapter)
    
    market = make_market()
    
    with patch("agents.shared.python.market_selector.get_markets_on_cooldown", return_value={market.id}):
        result = selector._filter([market])
    
    assert len(result) == 0


def test_filter_removes_dead_prices():
    """Рынки с ценой < 0.01 или > 0.99 должны быть отфильтрованы."""
    adapter = MagicMock()
    selector = MarketSelector(adapter)
    
    sure_yes = make_market(price=0.995)
    sure_no = make_market(price=0.003, market_id="test-2")
    active = make_market(price=0.5, market_id="test-3")
    
    with patch("agents.shared.python.market_selector.get_markets_on_cooldown", return_value=set()):
        result = selector._filter([sure_yes, sure_no, active])
    
    assert len(result) == 1
    assert result[0].id == "test-3"


def test_score_market_favors_uncertainty():
    """Рынки в зоне максимальной неопределённости должны иметь высший скор."""
    adapter = MagicMock()
    selector = MarketSelector(adapter)
    
    uncertain = make_market(price=0.50)
    biased = make_market(price=0.80)
    
    assert selector._score_market(uncertain) >= selector._score_market(biased)
```

---

### Итерация 2 — «Диагностика отказов и eval-метрики» (4–6 часов)

**Шаг 2-1. Добавить `idea_audit` таблицу в БД**

Файл: `agents/shared/python/db.py`

Добавить при `init_db()`:

```sql
CREATE TABLE IF NOT EXISTS idea_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    market_title TEXT,
    scout_edge REAL,
    swing_found INTEGER,
    shadow_agree INTEGER,
    shadow_confidence REAL,
    shadow_reason TEXT,
    herald_agree INTEGER,
    herald_confidence REAL,
    herald_reason TEXT,
    final_outcome TEXT,  -- 'saved' | 'no_consensus' | 'no_signal' | 'skipped_cooldown'
    created_at TEXT DEFAULT (datetime('now'))
);
```

Добавить функцию:
```python
def save_idea_audit(market_id: str, market_title: str, audit_data: dict):
    """Сохраняет аудит-запись о прохождении идеи через pipeline."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO idea_audit 
            (market_id, market_title, scout_edge, swing_found, shadow_agree, shadow_confidence,
             shadow_reason, herald_agree, herald_confidence, herald_reason, final_outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            market_id, market_title,
            audit_data.get("scout_edge"),
            audit_data.get("swing_found", 0),
            audit_data.get("shadow_agree"),
            audit_data.get("shadow_confidence"),
            audit_data.get("shadow_reason", ""),
            audit_data.get("herald_agree"),
            audit_data.get("herald_confidence"),
            audit_data.get("herald_reason", ""),
            audit_data.get("final_outcome", "unknown")
        ))
        conn.commit()
```

**Шаг 2-2. Добавить audit-вызовы в `run_team.py`**

В конце каждого рынка (перед `mark_market_analyzed`), добавить:

```python
# Сохраняем аудит прохождения идеи
from agents.shared.python.db import save_idea_audit
audit = {
    "scout_edge": signal.edge if signal else None,
    "swing_found": 1 if swing_signal else 0,
    "shadow_agree": int(opinion_shadow.agree) if opinion_shadow else None,
    "shadow_confidence": opinion_shadow.confidence if opinion_shadow else None,
    "shadow_reason": (opinion_shadow.opinion or "")[:200] if opinion_shadow else "",
    "herald_agree": int(opinion_herald.agree) if opinion_herald else None,
    "herald_confidence": opinion_herald.confidence if opinion_herald else None,
    "herald_reason": (opinion_herald.opinion or "")[:200] if opinion_herald else "",
    "final_outcome": "saved" if ideas_saved else ("no_consensus" if (signal or swing_signal) else "no_signal")
}
save_idea_audit(m.id, m.title, audit)
```

**Шаг 2-3. Добавить `/audit` команду в Telegram-бот**

Файл: `telegram/telegrambot.py`

```python
@dp.message(Command("audit"))
async def cmd_audit(message: Message):
    """Показывает статистику прохождения идей через pipeline за последние 24 часа."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT final_outcome, COUNT(*) as cnt
            FROM idea_audit
            WHERE created_at >= datetime('now', '-24 hours')
            GROUP BY final_outcome
        """).fetchall()
        
        shadow_rejection = conn.execute("""
            SELECT COUNT(*) FROM idea_audit
            WHERE shadow_agree = 0 AND scout_edge IS NOT NULL
            AND created_at >= datetime('now', '-24 hours')
        """).fetchone()[0]
    
    text = "📊 <b>Audit Pipeline (24ч):</b>\n\n"
    for row in rows:
        outcome_icons = {
            "saved": "✅", "no_consensus": "🛑", 
            "no_signal": "⚪️", "skipped_cooldown": "⏭"
        }
        icon = outcome_icons.get(row["final_outcome"], "❓")
        text += f"{icon} {row['final_outcome']}: <b>{row['cnt']}</b>\n"
    
    text += f"\n🔴 Отклонено SHADOW: <b>{shadow_rejection}</b>"
    await message.answer(text, parse_mode="HTML")
```

---

### Итерация 3 — «Оптимизация промптов агентов» (2–4 часа, после сбора данных)

Выполнять **только после** того, как будут данные из `idea_audit` за 3–5 дней работы.

**Шаг 3-1.** Проанализировать `/audit`: если `shadow_agree=0` встречается > 60% случаев — читать `shadow_reason` из аудит-таблицы.

**Шаг 3-2.** Если SHADOW систематически отказывает по одной причине (например, «недостаточная ликвидность»), скорректировать `GEMINI.md` агента SHADOW — добавить явное правило о минимальном bid_depth для принятия идеи.

**Шаг 3-3.** Добавить eval-тест:
```python
# tests/eval/test_shadow_recall.py
def test_shadow_approve_rate_above_threshold():
    """SHADOW должен одобрять >= 30% идей с Edge > 0.05."""
    # Запускаем на фиксированном наборе тест-кейсов из idea_audit
    ...
```

---

## Ожидаемый эффект по итерациям

| Итерация | Действие | Ожидаемые идеи/цикл |
|---|---|---|
| До правок | — | 0–1 |
| MVP (конфиг) | scan_limit=30, min_edge=0.06 | 1–3 |
| Итерация 1 | Убрать двойной cooldown, правильные дефолты | 2–5 |
| Итерация 2 | audit-таблица (диагностика, не влияет на выход) | 2–5 + видимость |
| Итерация 3 | Правка промптов по audit-данным | 3–8 |

---

## Риски и ограничения

- **Стоимость API:** Увеличение `scan_limit` с 10 до 30 = 3× больше LLM-вызовов за цикл. При 30-минутном интервале — 48 циклов/сутки × 30 рынков × 3 агента = ~4320 вызовов/сутки. Измерить текущий расход через `/stats` перед увеличением.
- **Снижение min_edge:** Порог 0.05 даст больше сигналов, но часть из них будет ложными. Рекомендуется включить режим paper trading для оценки точности перед изменением реальных ставок.
- **NEXUS top_n:** Сейчас захардкожен `top_n=30` в `run_team.py`. При `scan_limit=30` NEXUS отдаёт ровно столько кандидатов, сколько берётся в работу — запаса нет. Рекомендуется поднять `top_n` до 50–60.

---

## Чеклист для агента-программиста

### MVP (без PR, только конфиг)
- [ ] Выполнить `/settings scan_limit 30` в боте
- [ ] Выполнить `/settings min_edge 0.06` в боте
- [ ] Запустить `/scan` и проверить лог — должно быть > 5 рынков дошедших до SCOUT

### Итерация 1
- [ ] Удалить блок `if price_diff < 0.03 and is_cooldown: continue` из `run_team.py`
- [ ] Добавить `SCAN_LIMIT_DEFAULT` и `MIN_EDGE_DEFAULT` в `config.py`
- [ ] Обновить `run_team.py` и `agent.py` (ScoutAgent) на использование новых констант
- [ ] Написать тесты `tests/test_pipeline_filters.py`
- [ ] Прогнать `pytest tests/test_pipeline_filters.py -v`
- [ ] Проверить: нет ошибок импорта, бот стартует

### Итерация 2
- [ ] Добавить миграцию `idea_audit` таблицы в `db.py`
- [ ] Добавить `save_idea_audit()` функцию
- [ ] Встроить вызовы audit в `run_team.py`
- [ ] Добавить `/audit` команду в Telegram-бот
- [ ] Дать системе поработать 3–5 дней, собрать данные

### Итерация 3
- [ ] Проанализировать `idea_audit`: топ-причины отказов
- [ ] Скорректировать `GEMINI.md` агентов по данным
- [ ] Добавить eval-тест `tests/eval/test_shadow_recall.py`
- [ ] Проверить `approve_rate >= 30%` на тестовом наборе
