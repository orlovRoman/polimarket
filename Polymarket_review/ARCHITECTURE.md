# ARCHITECTURE.md — Общая схема системы

> Краткая версия архитектуры из REQUIREMENTS.md, чтобы не таскать весь файл в контекст.

## Компоненты
- Telegram Bot (`telegram/`)
- Оркестратор NEXUS (`orchestrator/`)
- Агенты SCOUT, SHADOW, HERALD (`agents/`)
- Адаптеры платформ предсказаний (`platforms/`)
- Skills и промпты (`skills/`, `*/GEMINI.md`)
- Хранилище памяти и сигналов (SQLite в `vault/database.sqlite`, `output/`)
- Утилиты и скрипты (`scripts/`)

## Поток данных (высокоуровнево)
1. Пользователь → Telegram Bot → NEXUS
2. NEXUS → SCOUT/SHADOW/HERALD (через ADK multi-agent, shared state)
3. Агенты → `platforms/*` адаптеры → API рынков
4. Результаты анализа → NEXUS → Telegram
5. История и память → SQLite (`vault/database.sqlite`), `output/`
