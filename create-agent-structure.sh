#!/usr/bin/env bash
set -e

BASE="$HOME"

mkdir -p \
  "$BASE/.gemini/hooks/post-run" \
  "$BASE/.gemini/hooks/summaries" \
  "$BASE/.gemini/shared/prompts" \
  "$BASE/.gemini/shared/templates" \
  "$BASE/.gemini/shared/scripts" \
  "$BASE/agents/orchestrator/src" \
  "$BASE/agents/orchestrator/scripts" \
  "$BASE/agents/orchestrator/logs" \
  "$BASE/agents/orchestrator/output" \
  "$BASE/agents/orchestrator/state" \
  "$BASE/agents/orchestrator/notes" \
  "$BASE/agents/polymarket-mispricing-agent/src" \
  "$BASE/agents/polymarket-mispricing-agent/scripts" \
  "$BASE/agents/polymarket-mispricing-agent/logs" \
  "$BASE/agents/polymarket-mispricing-agent/output" \
  "$BASE/agents/polymarket-mispricing-agent/state" \
  "$BASE/agents/polymarket-mispricing-agent/data" \
  "$BASE/agents/polymarket-news-agent/src" \
  "$BASE/agents/polymarket-news-agent/scripts" \
  "$BASE/agents/polymarket-news-agent/logs" \
  "$BASE/agents/polymarket-news-agent/output" \
  "$BASE/agents/polymarket-news-agent/state" \
  "$BASE/agents/polymarket-news-agent/feeds" \
  "$BASE/agents/polymarket-insider-agent/src" \
  "$BASE/agents/polymarket-insider-agent/scripts" \
  "$BASE/agents/polymarket-insider-agent/logs" \
  "$BASE/agents/polymarket-insider-agent/output" \
  "$BASE/agents/polymarket-insider-agent/state" \
  "$BASE/agents/polymarket-insider-agent/watchlists" \
  "$BASE/agents/shared/python" \
  "$BASE/agents/shared/js" \
  "$BASE/agents/shared/prompts" \
  "$BASE/agents/shared/adapters" \
  "$BASE/agents/shared/utils" \
  "$BASE/vault/inbox/telegram" \
  "$BASE/vault/inbox/links" \
  "$BASE/vault/inbox/voice" \
  "$BASE/vault/daily" \
  "$BASE/vault/team/orchestrator" \
  "$BASE/vault/team/mispricing" \
  "$BASE/vault/team/news" \
  "$BASE/vault/team/insiders" \
  "$BASE/vault/projects/polymarket" \
  "$BASE/vault/memory/durable" \
  "$BASE/vault/memory/entities" \
  "$BASE/vault/memory/market-patterns" \
  "$BASE/vault/memory/source-profiles" \
  "$BASE/vault/archive" \
  "$BASE/services/systemd" \
  "$BASE/services/cron" \
  "$BASE/services/telegram" \
  "$BASE/backups"

: > "$BASE/.gemini/GEMINI.md"
: > "$BASE/agents/orchestrator/GEMINI.md"
: > "$BASE/agents/polymarket-mispricing-agent/GEMINI.md"
: > "$BASE/agents/polymarket-news-agent/GEMINI.md"
: > "$BASE/agents/polymarket-insider-agent/GEMINI.md"
: > "$BASE/agents/orchestrator/.env"
: > "$BASE/agents/polymarket-mispricing-agent/.env"
: > "$BASE/agents/polymarket-news-agent/.env"
: > "$BASE/agents/polymarket-insider-agent/.env"

echo "Structure created under $BASE"
