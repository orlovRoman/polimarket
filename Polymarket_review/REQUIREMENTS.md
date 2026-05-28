# REQUIREMENTS.md — Polymarket AI Agent Team

> Версия: 2.0 | Дата: 2026-05-19  
> Язык проекта: Python 3.11+  
> Модель: Gemini 2.5 Pro (Google AI Pro / Gemini API)  
> Дополнительные модели-критики: Claude Sonnet, китайские и другие LLM — для критики и верификации  
> Фреймворк: Google ADK (Agent Development Kit)  
> Интерфейс пользователя: Telegram Bot  
> Философия: агенты обсуждают идеи между собой, покупают недооценённые события до исполнения

---

## 1. Цель проекта

Создать команду AI-агентов, которая:
1. Мониторит Polymarket и **конкурирующие рынки предсказаний** (Kalshi, Metaculus, Manifold)
2. Ищет **недооценённые события** — рынки, где вероятность занижена относительно реального прогноза
3. Логика торговли — **купить дёшево, продать дороже до исполнения события** (momentum / price discovery)
4. Агенты **обсуждают торговые идеи между собой** через shared session state и A2A-сообщения (ADK)
5. Пользователь общается с командой **через Telegram** в реальном времени
6. В финальной фазе — **агенты торгуют автономно** (опциональный этап, в последнюю очередь)

---

## 2. Ключевые изменения версии 2.0

| # | Что изменилось |
|---|---|
| 1 | Агенты общаются между собой через ADK shared state + `transfer_to_agent` |
| 2 | Добавлена поддержка Kalshi, Metaculus, Manifold как источников данных |
| 3 | SCOUT и NEXUS сосредоточены на поиске НЕДООЦЕНЁННЫХ событий (купить дёшево → продать) |
| 4 | Twitter/X УБРАН — медленный и платный. Заменён на Reddit + бесплатные новостные ленты |
| 5 | Добавлен раздел Skills для Gemini и Claude |
| 6 | Агент новостей отменен, источник событий интегрирован в MarketContext |
| 7 | Архитектура расширена под будущий автономный трейдинг |

---

## 3. Философия торговли

> Мы НЕ ждём исполнения события. Мы ищем рынки, где толпа недооценивает вероятность,
> покупаем позицию дёшево и продаём её дороже по мере того, как рынок переоценивает событие.

Примеры сценариев:
- Рынок оценивает событие в 15%. Наши агенты (Gemini + Claude) считают, что реальная вероятность 40% → покупаем на 0.15, ждём движения к 0.30+ и продаём
- Два похожих рынка на Polymarket и Kalshi расходятся в 20 п.п. → арбитраж
- Крупный инсайдерский кошелёк зашёл на 0.20 → следим, входим параллельно

---

## 4. Межагентное взаимодействие (ADK Multi-Agent)

Агенты общаются через три механизма Google ADK:

### 4.1 Shared Session State
Каждый агент читает и пишет в `session.state`. Это основной канал передачи данных и обсуждений.

```python
# SCOUT пишет найденную идею
context.state["scout_idea"] = {
    "market_id": "abc123",
    "platform": "polymarket",
    "current_price": 0.18,
    "model_estimate": 0.42,
    "confidence": 0.8,
    "reasoning": "..."
}

# NEXUS читает и запрашивает мнение SHADOW
idea = context.state.get("scout_idea")
```

### 4.2 LLM-Driven Delegation (transfer_to_agent)
NEXUS как оркестратор может передавать управление субагентам:
```python
# NEXUS говорит: "SHADOW, проверь этот кошелёк"
transfer_to_agent(agent_name="SHADOW")
```

### 4.3 AgentTool (явный вызов)
NEXUS использует агентов как инструменты и получает ответы обратно:
```python
scout_tool = AgentTool(agent=scout_agent)
shadow_tool = AgentTool(agent=shadow_agent)
# NEXUS вызывает оба, агрегирует мнения, формирует решение
```

### 4.4 Процесс "обсуждения" идеи

```
1. SCOUT находит недооценённый рынок → пишет в state["pending_ideas"]
2. NEXUS видит новую идею → вызывает SHADOW для проверки инсайдерской активности
3. SHADOW пишет своё мнение в state["shadow_opinion_{market_id}"]
4. NEXUS агрегирует мнения SCOUT + SHADOW → формирует итоговый сигнал
5. NEXUS отправляет финальный сигнал в Telegram пользователю
6. Пользователь может ответить в Telegram → NEXUS переспрашивает агентов
```

---

## 5. Команда агентов

### 5.1 NEXUS — Оркестратор

| Параметр | Значение |
|---|---|
| Имя | NEXUS |
| Файл | `orchestrator/agent.py` |
| Инструкции | `orchestrator/GEMINI.md` |
| Модель | Gemini 2.5 Pro |
| Роль | Координатор, хранитель памяти, точка входа Telegram, финальный судья |

**Функции:**
- Принимает сообщения из Telegram, маршрутизирует к агентам
- Агрегирует мнения SCOUT и SHADOW перед отправкой сигнала
- **Фильтр Pump & Dump:** Блокирует сделки, если цена резко выросла, а SHADOW видит только неизвестные кошельки
- Ведёт "журнал обсуждений" (через SQLite в `vault/database.sqlite`)
- Хранит долгосрочную память команды (SQLite)
- Запускает субагентов по расписанию (APScheduler)
- **Сфокусирован**: ищет недооценённые события для покупки, а не просто двусмысленность

**Telegram-команды:**
- `/status` — состояние всех агентов
- `/ideas` — текущие торговые идеи в обсуждении
- `/signals` — финальные сигналы за 24 часа
- `/ask [вопрос]` — задать вопрос всей команде (NEXUS собирает мнения)
- `/discuss [market_url]` — запустить обсуждение конкретного рынка
- `/insiders` — последняя активность инсайдеров
- `/memory` — что помнит команда
- `/platforms` — статус подключённых платформ

---

### 5.2 SCOUT — Агент поиска недооценённых рынков

| Параметр | Значение |
|---|---|
| Имя | SCOUT |
| Файл | `agents/mispricing/agent.py` |
| Инструкции | `agents/mispricing/GEMINI.md` |
| Модель | Gemini 2.5 Pro (основная) + Claude Sonnet (второе мнение) |
| Роль | Поиск недооценённых событий на всех подключённых платформах |
| Расписание | Каждые 20 минут |

**Ключевая философия SCOUT:**
> Не ждать исполнения. Найти рынок, где рыночная цена < реальной вероятности.
> Оценить потенциал роста цены и момент входа.

**Функции:**
- Вытягивает рынки с Polymarket, Kalshi, Metaculus, Manifold через их API
- **Double-Blind тестирование:** LLM оценивает реальную вероятность исхода вслепую (0–1), НЕ зная текущей рыночной цены (во избежание Anchoring Bias)
- Сравнивает "слепую" LLM-оценку с реальной рыночной ценой → `edge = blind_model_prob - market_price`
- Ищет рынки с `edge > 0.10` (цена занижена на 10+ п.п.)
- Дополнительно: ищет похожие рынки на разных платформах, сравнивает цены
- Анализирует формулировки на двусмысленность (арбитраж резолюции)
- Пишет идеи в базу SQLite для обсуждения с NEXUS/SHADOW
- Использует Claude как "критика" своих оценок (Generator-Critic pattern)

**Сигналы SCOUT:**
- `[НЕДООЦЕНКА] Рынок X: рынок 18%, модель 42% → edge +24 п.п. → ПОКУПАТЬ`
- `[КРОСС-ПЛАТФОРМА] Polymarket 45% vs Kalshi 62% → арбитраж 17 п.п.`
- `[ДВУСМЫСЛЕННОСТЬ] Рынок Y: возможна спорная резолюция → риск`

---

### 5.3 SHADOW — Агент мониторинга инсайдеров

| Параметр | Значение |
|---|---|
| Имя | SHADOW |
| Файл | `agents/insider/agent.py` |
| Инструкции | `agents/insider/GEMINI.md` |
| Модель | Gemini 2.5 Pro |
| Роль | Мониторинг кошельков инсайдеров, подтверждение идей SCOUT |
| Расписание | Каждые 10 минут |

**Функции:**
- Ведёт профилирование кошельков в SQLite (историческая доходность / Win Rate)
- **Smart Money Tracker:** Мониторит кошельки с высоким Win Rate. Их вход в низколиквидный рынок — главный альфа-сигнал
- **Whale Alerts:** Отслеживает аномальные всплески объема (Volume Spikes) на рынках, даже от неизвестных кошельков
- При запросе от NEXUS: проверяет инсайдерскую активность по конкретному рынку и объем
- Пишет своё мнение в базу SQLite
- Ищет паттерны: крупный вход, разворот позиции, накопление

**Сигналы SHADOW:**
- `[ИНСАЙДЕР-ПОДТВЕРЖДЕНИЕ] Рынок X: 3 крупных кошелька зашли на стороне YES → подтверждает идею SCOUT`
- `[ИНСАЙДЕР-ПРЕДУПРЕЖДЕНИЕ] Рынок Y: кошелёк из watchlist продаёт → переоценить идею`
- `[РАЗВОРОТ] Известный кошелёк сменил позицию с NO на YES`

---



## 6. Подключаемые платформы предсказаний

Архитектура построена на `BaseMarketAdapter` — можно добавлять платформы без изменения логики агентов.

```
polymarket/
├── client.py           ← Polymarket CLOB + REST
├── kalshi.py           ← Kalshi API (CFTC-regulated, US markets)
├── metaculus.py        ← Metaculus API (бесплатные данные, 20k+ вопросов)
├── manifold.py         ← Manifold Markets API (бесплатный, 15k+ рынков)
└── base_adapter.py     ← BaseMarketAdapter (интерфейс для новых платформ)
```

| Платформа | Реальные деньги | API | Приоритет |
|---|---|---|---|
| Polymarket | ✅ USDC | ✅ CLOB + REST | 1 — основной |
| Kalshi | ✅ USD (CFTC) | ✅ REST | 2 — кросс-арбитраж |
| Metaculus | ❌ (репутация) | ✅ бесплатный | 3 — калибровка вероятностей |
| Manifold | ❌ (mana) | ✅ бесплатный | 4 — идеи и сентимент |

Добавление новой платформы: создать `polymarket/newplatform.py`, наследовать `BaseMarketAdapter`, зарегистрировать в `config.yaml`.

---

## 7. Skills для Gemini и Claude

### 7.1 Официальные Gemini Skills (Google)

| Skill | Путь | Применение в проекте |
|---|---|---|
| `google-gemini/gemini-api-dev` | Gemini API | Основной skill для всех агентов |
| `google-gemini/vertex-ai-api-dev` | Vertex AI | Деплой на Cloud Run / Vertex |
| `google-gemini/gemini-live-api-dev` | Real-time streaming | Будущий live-мониторинг рынков |
| `google-gemini/gemini-interactions-api` | Chat, text, streaming | Telegram-диалог с агентами |

### 7.2 Модели-критики и дополнительные провайдеры

Использовать можно не только Claude. В проекте допускаются несколько критиков одновременно: одна основная модель генерирует гипотезу, а набор критиков проверяет её с разных точек зрения. Подход должен быть provider-agnostic: модели подключаются через единый интерфейс, а выбор провайдера задаётся в `config.yaml`.

| Провайдер / модель | Роль | Примечание |
|---|---|---|
| Claude Sonnet | Критик | Сильный второй взгляд на рассуждения |
| DeepSeek / Qwen / GLM / Kimi | Критик | Подходит как альтернативный критик или дешёвый массовый ревьюер |
| Gemini | Генератор | Основная модель для NEXUS и SCOUT |
| Любая совместимая LLM | Критик | Подключается через стандартный adapter |

### 7.3 Официальные Claude Skills (Anthropic)

| Skill | Путь | Применение в проекте |
|---|---|---|
| `anthropics/mcp-builder` | MCP Server | Создать MCP-сервер для интеграции Polymarket API |
| `anthropics/skill-creator` | Skill creation | Создавать кастомные skills для агентов |
| `anthropics/webapp-testing` | Playwright | Тестировать web-интерфейсы платформ |
| `anthropics/pdf` | PDF extraction | Извлекать условия из PDF-документов рынков |
| `anthropics/xlsx` | Excel | Аналитика и отчёты по сигналам |

### 7.4 Community Skills (из awesome-agent-skills)

| Skill | Применение |
|---|---|
| `firecrawl/firecrawl-build-search` | Веб-скрейпинг страниц рынков без официального API |
| `firecrawl/firecrawl-build-scrape` | Парсинг страниц Polymarket, Kalshi |
| `composiohq/composio` | Подключение к 1000+ внешним API (Reddit, Telegram, etc.) |
| `neondatabase/neon-postgres` | Хранение истории рынков и сигналов в PostgreSQL |
| `clickhouse/chdb-sql` | Аналитика больших объёмов трейдинговых данных |
| `trailofbits/building-secure-contracts` | Безопасность смарт-контрактов при автотрейдинге |
| `getsentry/sentry-python-sdk` | Мониторинг ошибок агентов в продакшене |
| `cloudflare/agents-sdk` | Stateful агенты с scheduling на Cloudflare Workers |

### 7.5 Схема использования нескольких моделей

```
SCOUT использует несколько LLM:
├── Gemini 2.5 Pro → генерирует оценку вероятности + reasoning
├── Claude Sonnet → критикует оценку Gemini, ищет слабые места
└── Другие модели-критики → проверяют альтернативные сценарии и ловят ложные допущения

NEXUS использует:
├── Gemini 2.5 Pro → основная логика, маршрутизация, память
├── Один или несколько критиков → независимая верификация сигнала
└── Провайдеры могут меняться без переписывания логики

Паттерн: Generator (Gemini) → Critic Pool → Final (NEXUS/Gemini)
```

---

## 8. Архитектура и стек

### 8.1 Технологии

| Компонент | Технология |
|---|---|
| Язык | Python 3.11+ |
| AI-фреймворк | Google ADK |
| Основная модель | Gemini 2.5 Pro (Gemini API) |
| Модель-критик | Claude Sonnet 4.x (Anthropic API) |
| Telegram | python-telegram-bot v20 / aiogram v3 |
| Данные рынков | Polymarket CLOB API + Kalshi REST + Metaculus API + Manifold API |
| Новости | Google News RSS, Reuters RSS, Reddit API (бесплатный) |
| Расписание | APScheduler |
| БД (MVP) | SQLite (`vault/database.sqlite`) |
| БД (prod) | Neon PostgreSQL (serverless) |
| Деплой | Cloud Run |
| Мониторинг | Sentry (getsentry/sentry-python-sdk) |

### 8.2 Структура папок

```
polymarket-agent-team/
│
├── REQUIREMENTS.md          ← документ требований
├── README.md
├── config.yaml              ← параметры агентов, платформы, расписание
├── .env                     ← API ключи (не коммитить)
├── .env.example
├── main.py                  ← точка входа
├── requirements.txt
│
├── orchestrator/
│   ├── GEMINI.md            ← системный промпт NEXUS
│   ├── agent.py             ← логика NEXUS
│   ├── router.py            ← маршрутизация между агентами
│   ├── aggregator.py        ← сбор и агрегация мнений субагентов
│   └── memory.py            ← vault/memory.json management
│
├── agents/
│   ├── mispricing/          ← SCOUT
│   │   ├── GEMINI.md        ← системный промпт SCOUT
│   │   ├── agent.py
│   │   ├── estimator.py     ← LLM-оценка реальной вероятности
│   │   ├── comparator.py    ← кросс-платформенное сравнение
│   │   ├── critic.py        ← Claude-критик оценок
│   │   └── arb_scanner.py   ← поиск арбитража
│   │
│   ├── insider/             ← SHADOW
│   │   ├── GEMINI.md        ← системный промпт SHADOW
│   │   ├── agent.py
│   │   ├── tracker.py       ← мониторинг кошельков
│   │   ├── reddit_watcher.py← Reddit мониторинг
│   │   └── profiler.py      ← анализ профилей кошельков
│   │
├── platforms/               ← адаптеры рынков предсказаний
│   ├── base_adapter.py      ← BaseMarketAdapter (интерфейс)
│   ├── polymarket.py        ← Polymarket CLOB + REST
│   ├── kalshi.py            ← Kalshi API
│   ├── metaculus.py         ← Metaculus API
│   ├── manifold.py          ← Manifold Markets API
│   └── models.py            ← Market, Trade, Signal dataclasses
│
├── telegram/
│   ├── bot.py               ← инициализация
│   ├── handlers.py          ← /status, /ideas, /ask, /discuss
│   └── formatter.py         ← форматирование сигналов
│
├── skills/                  ← кастомные GEMINI.md и Claude skills
│   ├── polymarket-api/      ← MCP skill для Polymarket
│   ├── kalshi-api/          ← MCP skill для Kalshi
│   └── prediction-markets/  ← общий skill для рынков предсказаний
│
├── vault/
│   └── database.sqlite      ← SQLite БД: память, кошельки, обсуждения, сигналы
│
├── output/
│   ├── mispricing/          ← результаты SCOUT
│   └── insider/             ← результаты SHADOW
│
└── logs/
    ├── orchestrator.log
    ├── mispricing.log
    └── insider.log
```

---

## 9. Переменные окружения (.env)

```env
# Google AI
GOOGLE_API_KEY=your_gemini_api_key
GOOGLE_PROJECT_ID=your_gcp_project

# Anthropic / other providers (critics)
ANTHROPIC_API_KEY=your_anthropic_api_key
OTHER_LLM_API_KEY=optional

# Telegram
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Polymarket
POLYMARKET_API_KEY=your_polymarket_key
POLYMARKET_PRIVATE_KEY=your_private_key  # для автотрейдинга (фаза 3)

# Kalshi
KALSHI_API_KEY=your_kalshi_key
KALSHI_EMAIL=your_email

# Metaculus & Manifold (публичные API, ключ не нужен для чтения)
METACULUS_TOKEN=optional
MANIFOLD_API_KEY=optional

# Reddit (бесплатный tier)
REDDIT_CLIENT_ID=your_reddit_client_id
REDDIT_CLIENT_SECRET=your_reddit_secret
REDDIT_USER_AGENT=polymarket-agent/1.0

# Настройки
SCOUT_INTERVAL_MIN=20
SHADOW_INTERVAL_MIN=10
MIN_EDGE_THRESHOLD=0.10         # минимальный edge для сигнала (10 п.п.)
MIN_INSIDER_POSITION_USD=100    # минимальная позиция для сигнала
```

---

## 10. Критерии сигнала

### Сигнал недооценки (SCOUT) — ОСНОВНОЙ
- `edge = model_probability - market_price > 0.10`
- Confidence модели ≥ 0.65
- Рынок ещё активен (не истёк через 2+ часа)
- [Опционально] Claude-критик согласен с оценкой

### Кросс-платформенный арбитраж (SCOUT)
- Один и тот же ивент: разница цен между платформами > 10 п.п.
- Ликвидность на обеих платформах достаточная

### Сигнал инсайдера (SHADOW)
- Кошелёк из watchlist совершил транзакцию > $100
- Или 2+ кошелька зашли в одну сторону в течение часа



## 11. Типы уведомлений в Telegram

| Тип | Эмодзи | Приоритет | Условие |
|---|---|---|---|
| Недооценка | 🟢 | ВЫСОКИЙ | edge > 20 п.п., confidence > 0.75 |
| Арбитраж | 🔵 | ВЫСОКИЙ | разница платформ > 15 п.п. |
| Инсайдер | 🔴 | ВЫСОКИЙ | позиция > $500 |
| Обсуждение | 💬 | СРЕДНИЙ | SCOUT + SHADOW не согласны |
| Новость | 📰 | СРЕДНИЙ | impact > 7, лаг > 5 мин |
| Сводка | 📊 | НИЗКИЙ | каждые 6 часов |

---

## 12. Фазы разработки и шаги

### Фаза 1 — Ядро (MVP)

1. Создать Telegram Bot (`/BotFather`), получить токен
2. Создать Gemini API Key (Google AI Studio)
3. Настроить Reddit API (бесплатный, `praw` библиотека)
4. Реализовать `platforms/base_adapter.py` + `platforms/polymarket.py`
5. Реализовать SCOUT MVP:
   - fetch markets → LLM estimate → edge calculation → output
6. Реализовать NEXUS MVP:
   - Telegram handler → вызов SCOUT → формирование пула критиков → форматирование → ответ
7. Подключить бот: пользователь пишет `/ideas` → NEXUS → SCOUT → ответ

### Фаза 2 — Мультиагентность и инсайдеры

8. Реализовать `platforms/kalshi.py` + `platforms/metaculus.py`
9. Добавить кросс-платформенный арбитраж в SCOUT
10. Реализовать SHADOW: мониторинг кошельков + Reddit
11. Настроить межагентное обсуждение: NEXUS агрегирует SCOUT + SHADOW
12. Добавить Claude как критика (SCOUT → Gemini estimate → Claude critique → NEXUS)
13. Команда `/discuss [url]` — полное обсуждение конкретного рынка



17. Деплой на Google Cloud Run
18. Настроить Sentry для мониторинга ошибок
19. Переезд хранилища: JSON → Neon PostgreSQL
20. Добавить `platforms/manifold.py`

### Фаза 5 — Автономный трейдинг *(опционально, последний этап)*

21. Реализовать `trading/executor.py` — исполнение сделок через Polymarket CLOB
22. Добавить risk management: лимиты позиций, стоп-лоссы
23. NEXUS принимает решение о входе только при: edge > 20 п.п. + SCOUT + SHADOW согласны
24. Начать с малых позиций ($10–50), вести лог всех сделок
25. Подключить Kalshi исполнение (для US-рынков через посредника)

---

## 13. Ограничения и риски

- **Не финансовый советник** — все сигналы информационные, торговля на ваш риск
- **Polymarket ограничения** — API rate limits, необходим backoff и кэш
- **Claude API costs** — используется только как критик, не для каждого рынка
- **Reddit API** — бесплатный tier: 60 запросов/мин, достаточно для мониторинга
- **Kalshi** — требует US-аккаунт или работа через публичный API (только чтение)
- **Автотрейдинг** — начинать только после длительного тестирования на малых суммах


## 14. Дополнительные файлы для ускорения разработки

Чтобы веб-кодинг шёл быстрее и требовал меньше токенов, полезно вынести повторяющиеся решения, интерфейсы и шаблоны в отдельные файлы. Это снижает количество контекста, который нужно каждый раз объяснять модели, и соответствует подходу active context engineering и code-first MCP, где тяжёлую работу лучше держать в коде и файлах, а не в диалоге. [web:432][web:433]

### 14.1 Рекомендуемый набор файлов

| Файл | Назначение | Почему экономит токены |
|---|---|---|
| `ARCHITECTURE.md` | Общая схема системы и границы модулей | Не нужно пересказывать архитектуру в каждом чате |
| `AGENTS.md` | Роли агентов, входы, выходы, команды | Убирает длинные объяснения про NEXUS/SCOUT/SHADOW |
| `INTERFACES.md` | Контракты функций, схемы данных, dataclasses | Модель сразу видит сигнатуры и структуру данных |
| `ADAPTERS.md` | Как подключать новые рынки и провайдеров | Ускоряет интеграцию Kalshi/Metaculus/Manifold |
| `PROMPTS/` | Отдельные системные промпты агентов | Снижает раздувание основного файла требований |
| `DECISIONS.md` | Принятые решения и причины | Помогает не спорить повторно о том же |
| `CHANGELOG.md` | История изменений требований и кода | Быстро понять, что уже поменялось |
| `TASKS.md` | Очередь задач по приоритету | Делает планирование короче и яснее |
| `TEST_CASES.md` | Критичные тесты и сценарии | Сокращает пояснения при отладке |
| `EXAMPLES.md` | Минимальные примеры запросов и ответов | Модель опирается на эталонные форматы |
| `SCHEMAS/` | JSON Schema для рынков, сигналов, обсуждений | Меньше текста, больше формальной структуры |
| `SCRIPTS/` | Утилиты для сбора, фильтрации и отчётов | Тяжёлые операции выносятся из контекста в код |
| `MEMORY_POLICY.md` | Что хранить в памяти, что не хранить | Помогает контролировать разрастание state |

### 14.2 Файлы для token-efficient coding

Минимальный набор, который даст максимальный эффект:
1. `ARCHITECTURE.md`
2. `INTERFACES.md`
3. `ADAPTERS.md`
4. `DECISIONS.md`
5. `TEST_CASES.md`
6. `TASKS.md`

Эти файлы особенно полезны, если использовать паттерн "код как API": модель читает только нужные интерфейсы и делает вычисления в коде, а не переносит большие куски данных в контекст. [web:433]

### 14.3 Что держать отдельно от requirements

В `REQUIREMENTS.md` лучше не перегружать:
- большие примеры кода;
- длинные списки эндпоинтов;
- полные JSON-форматы;
- развернутые сценарии тестирования;
- исторические рассуждения о выборе технологий.

Вместо этого — ссылка на отдельный файл, чтобы модель каждый раз не перечитывала лишний объём. Такой подход согласуется с идеей контекстно-эффективных мультиагентных систем, где данные хранятся в артефактах и подгружаются только по необходимости. [web:432]
