Here is the detailed research report on the Polymarket bot's infrastructure and supporting systems. All files have been read completely.

---

## 1. Market Selection Logic
**File:** `agents/shared/python/market_selector.py` (201 lines)

`MarketSelector` class combines multiple strategies to pick markets for analysis:

### Selection Strategies (`_fetch_mixed`):
1. **Mid-volume with offset rotation** — pages through markets sorted by volume using a rotating offset (stored in DB via `save_memory("market_scan_offset")`). Offset increments by `per_strategy` each scan, wraps at `MARKET_OFFSET_MAX` (default 200).
2. **Ending soon** — markets closing soon (high volatility potential) via `adapter.list_markets_ending_soon()`.
3. **Category rotation** — cycles through `SCAN_CATEGORIES = ["politics", "crypto", "sports", "science", "business"]` using a rotation index stored in DB.
4. **Top volume (fallback)** — standard `adapter.list_markets()`.

Each strategy fetches `max(total_limit*3 // 4, 5)` markets. Manual scans can target a specific category.

### Special mode: `penny_stocks`
Fetches top 1000 markets by volume and filters for prices in [0.01, 0.05] or [0.95, 0.99].

### Filtering (`_filter`):
- Removes expired markets (close_time in past)
- Removes dead prices (<0.01 or >0.99), except in penny_stocks mode
- Removes markets on cooldown (analyzed < `MARKET_COOLDOWN_HOURS`=4h ago) UNLESS price changed ≥3% since last analysis

### Scoring (`_score_market`):
- Price in uncertainty zone [0.10, 0.90] → +10 points
- Strong skew (≤0.10 or ≥0.90) → +3 points (interesting for SWING)
- Closing within 1 day → +3, within 1 week → +2, within 1 month → +1
- Very far markets (>180 days) → −1
- Penny stocks mode: matching penny prices → +10

Results are deduplicated, sorted by score descending, and truncated to `total_limit`.

---

## 2. Data Fetching (RSS, Reddit, Wiki, HN, Google Trends)
**File:** `agents/shared/utils/web_search.py` (180 lines)

All data sources share a **thread-safe in-memory TTL cache** (15 minutes, keyed by MD5 hash of query). Expired entries are garbage-collected on each cache write.

### Sources:
| Function | Source | API | Limit | Notes |
|---|---|---|---|---|
| `fetch_rss_news(query)` | Google News RSS | `news.google.com/rss/search` | 5 items | Returns `[date] title` |
| `fetch_reddit_news(query)` | Reddit search API | `reddit.com/search.json` | 5 items | Returns `[r/sub, ↑score] title` |
| `fetch_wikipedia_context(query)` | Wikipedia API | `en.wikipedia.org/w/api.php` | 3 items | Strips HTML from snippets |
| `fetch_google_trends(query)` | pytrends library | Google Trends | 7-day window | Returns interest level, peak, trend direction (📈/📉/➡️) |
| `fetch_hackernews(query)` | HN Algolia API | `hn.algolia.com/api/v1/search` | 3 items | Returns `[HN, ↑points] title` |

### `build_search_query(market_title)`
Strips stopwords (English + Russian) and punctuation from market title, takes first 6 keywords to form a compact search query.

---

## 3. Screening Logic and Prefilter (NEXUS Screener)
**File:** `agents/orchestrator/src/agent.py` (638 lines) — `NexusAgent.screen_markets()`

### How it works:
1. Receives a compact list of markets (truncated to max **120 markets**)
2. Formats each as: `- id:{id} | q:{question} | p:{price} | vol:{volume} | end:{close_time}`
3. Sends a **single LLM call** to Gemini with a structured JSON schema requesting:
   - `top_candidates`: list of IDs (top N most promising markets)
   - `correlations`: array of detected relationships between markets (causal, inverse, arbitrage, thematic)
4. Uses `generate_content_with_fallback()` for resilient LLM access
5. Cleans market IDs (strips prefixes like `id:`, dashes)
6. Saves detected correlations to DB via `save_correlation()`

### Correlation types: `causal`, `inverse`, `arbitrage`, `thematic`
Each correlation has: market_a_id, market_b_id, titles, type, description, confidence.

### NEXUS also provides:
- **12 function tools** for Telegram chat interactions: read/write/search Obsidian vault, save/delete memory facts, query DB (SELECT only), manage signals, write daily summaries, promote insights to long-term memory
- **Multi-step function calling** loop (max 8 iterations) for complex user queries
- Dynamic system prompt with: current date, active facts from Layer 1, last 3 system episodes

---

## 4. Guards and Error Handling
**File:** `core/guards.py` (77 lines) — `LLMHealthGate` class

### Circuit breaker pattern with 3 states:
| State | Trigger | Behavior |
|---|---|---|
| **HEALTHY** | Default / after retry_after expires | All requests pass |
| **DEGRADED** | ≥3 errors (429/503) within 60s window | Pause 60s, returns `False` from `check_availability()` |
| **DEAD** | ≥5 errors within 60s window | Pause 300s (5 min), raises `LLMUnavailableError` |

### Key mechanics:
- Only tracks HTTP 429 (rate limit) and 503 (service unavailable) errors
- Error timestamps are kept in a sliding 60-second window
- `record_success()` resets to HEALTHY and clears all error history
- After pause period expires, transitions back to HEALTHY (partial open / half-open circuit)
- Thread-safe via `threading.Lock`

### Additional resilience (in `gemini_client.py`):
- **Multi-provider fallback**: Cerebras → OpenRouter → Gemini (multiple models)
- **Dual Gemini API keys**: primary + secondary `GOOGLE_API_KEY_SECONDARY`
- **Exponential backoff** between Gemini attempts: `min(0.5 * 2^attempt, 5.0)` seconds
- **Consecutive failure tracking**: after 3 consecutive failures for an agent, sends a Telegram notification with inline keyboard to switch models
- **Cerebras rate limit**: 20-second wait on 429, round-robin through 4 models: `qwen-3-235b`, `gpt-oss-120b`, `zai-glm-4.7`, `llama3.1-8b`

---

## 5. Checkpoint System
**File:** `core/checkpoint.py` (68 lines)

### Design:
- In-memory dict `_checkpoints_cache` for speed, backed by JSON file persistence (`vault/checkpoints.json`)
- **Debounced writes**: uses a `threading.Timer(2.0, _save_sync)` — batches rapid updates, writes to disk at most once every 2 seconds

### API:
| Function | Purpose |
|---|---|
| `save_checkpoint(phase, **kwargs)` | Saves pipeline phase state with timestamp + arbitrary kwargs |
| `get_checkpoint(phase)` | Retrieves saved checkpoint for a phase |
| `verify_checkpoint(phase, market_id=None)` | Returns `True` if checkpoint exists and has status "ok" or "success". Key format: `{phase}_{market_id}` |

### Usage: Tracks progress through the analysis pipeline phases, allowing restart/resume after crashes.

---

## 6. Notification System
**File:** `services/notifications.py` (336 lines)

### Core functions:
| Function | Purpose |
|---|---|
| `send_telegram(text, parse_mode, reply_markup)` | Base Telegram send to default chat. Falls back to plain text if HTML parsing fails (400 error). |
| `send_telegram_to_chat(text, chat_id, ...)` | Send to specific chat (event-driven group) |

### Alert types with dedicated formatters:

**1. Correlation Alerts** (`send_correlation_alerts`):
- Fetches new un-notified correlations from DB
- For each (up to 5), fetches fresh market data and runs `ArbitrageAgent.analyze_correlation()`
- If arbitrage is detected → formats and sends rich HTML alert with market links, spread, reasoning, trade instruction
- Marks correlations as notified regardless of arbitrage result (anti-spam)

**2. Cross-Platform Arbitrage** (`send_cross_arbitrage_alerts`):
- Reads from `cross_arbitrage_signals` table (Polymarket ↔ Kalshi etc.)
- Filters by `min_spread` (default 5%)
- Rich format includes: platforms, prices in cents, spread %, trade instruction, recommended actions (BUY_YES/BUY_NO/SKIP), expected P&L, risk level emoji

**3. Synthetic Corridor Alerts** (`send_synthetic_corridor_alerts`):
- Monotonicity violations within Polymarket events (higher threshold cheaper than lower)
- Includes execution strategy (buy YES on lower, buy NO on upper), math on budget, guaranteed PnL, max profit, order book depth
- Deduplication via `sent_alerts` table with `is_alert_already_sent()`

**4. Temporal Corridor Alerts** (`send_temporal_corridor_alerts`):
- Calendar spread arbitrage between different expiry dates
- Shows probability of corridor, date gap, quality score, expected value, 3 PnL scenarios, exit rule

---

## 7. Database Schema and Key Tables
**File:** `agents/shared/python/db.py` (1408 lines)

SQLite database with WAL journal mode and 5000ms busy timeout. Thread-safe via context manager. Single-initialization with double-check locking.

### Tables (19 total):

| Table | Purpose | Key columns |
|---|---|---|
| `markets` | Market data cache | id, platform, title, url, price, close_time, tokens, volume |
| `signals` | Trading signals | id, type (MISPRICING/SWING), market_id, edge, confidence, priority, status, target_outcome, estimated_probability |
| `agent_opinions` | Agent consensus votes | agent_name, market_id, opinion, confidence, agree |
| `analyzed_markets` | Cooldown tracking | market_id, analyzed_at, last_price |
| `memory` | Key-value long-term store | key, value (JSON), category, ttl, priority, expires_at |
| `correlations` | Inter-market relationships | market_id_a/b, correlation_type, confidence, notified |
| `price_history` | Price time series | market_id, price, recorded_at |
| `cross_arbitrage_signals` | Cross-platform arb | market_a/b info, spread, arbitrage_type, actions, risk |
| `synthetic_corridors` | Monotonicity violations | event info, lower/upper legs, PnL calculations, ROI |
| `temporal_corridors` | Calendar spread arb | early/late legs, date_gap, EV, quality_score, exit_rule |
| `sent_alerts` | Dedup notifications | alert_key, alert_type, sent_at |
| `wallets` | Smart money profiles | address, alias, win_rate, total_profit, is_insider |
| `known_whales` | Known whale addresses | address, alias, win_rate, total_won, total_vol |
| `trader_transactions` | Large trades | wallet_address, market_id, outcome, amount_usd, price |
| `chat_history` | Telegram conversations | chat_id, role, content, timestamp |
| `telegram_posts` | Inbound Telegram messages | chat_id, message_id, text, status |
| `agent_episodes` | Episodic agent memory | agent_name, event_type, market_id, summary, context, outcome |
| `agent_episodes_fts` | FTS5 full-text search | episode_id, agent_name, summary, context |
| `llm_calls` | LLM usage logging | agent_name, model_name, input/output/total tokens, latency_ms, prompt_version, had_performance_ctx |
| `vault_index` | Obsidian file index | path, category, title, tags, content_hash |
| `idea_audit` | Pipeline audit trail | market_id, scout_edge, swing_found, shadow_agree/confidence, final_outcome |

### Key functions:
- **Memory system**: `save_memory()` / `get_memory()` with TTL, categories (config/fact/preference/cache/general), priority ranking
- **Relevant facts**: `get_relevant_facts()` loads config+preferences first, then keyword-matched facts for context-aware system prompts
- **Performance tracking**: `get_performance_summary()` and `get_learning_impact()` compare accuracy with/without performance context
- **Chat archiving**: `compress_and_cleanup_chat_history()` archives old messages to memory before deletion
- **Cleanup**: `cleanup_stale_signals()` archives expired markets, deletes signals >1 year old; `cleanup_old_price_history()` removes prices >7 days; `cleanup_expired_memory()` removes expired TTL entries

### Indexes: 12 indexes for performance on frequent queries (signals status/created, opinions by market, chat history, price history, correlations, episodes, etc.)

---

## 8. Config and Environment
**File:** `config.py` (121 lines)

### Project structure:
- `PROJECT_ROOT` = directory containing config.py
- `VAULT_PATH` = `vault/` (Obsidian + DB storage)
- `DB_PATH` = `vault/database.sqlite`
- `.env` loaded via `python-dotenv`

### Key configuration groups:

**Market Selection:**
| Setting | Default | Purpose |
|---|---|---|
| `MARKET_COOLDOWN_HOURS` | 4 | Hours before re-analyzing a market |
| `MARKET_OFFSET_MAX` | 200 | Max offset for volume-based paging rotation |
| `PRICE_RANGE_MIN/MAX` | 0.10 / 0.90 | "Interesting" price zone for scoring |
| `SCAN_CATEGORIES` | politics, crypto, sports, science, business | Category rotation list |

**Scanning:**
| Setting | Default | Purpose |
|---|---|---|
| `SCAN_LIMIT_DEFAULT` | 30 | Default market scan limit |
| `MIN_EDGE_DEFAULT` | 0.05 | Minimum edge for signals |
| `WHALE_ALERT_MIN_USD` | 10000 | Min USD for whale alerts |

**Cross-Platform Arbitrage:**
| Setting | Default | Purpose |
|---|---|---|
| `ARB_POLY_LIMIT` / `ARB_KALSHI_LIMIT` | 100 | Markets fetched per platform |
| `ARB_MIN_MATCH_SCORE` | 0.50 | Min similarity for pairing |
| `ARB_MIN_SPREAD_ALERT` | 5.0% | Min spread to trigger alert |
| `ARB_MAX_DAYS_DIFF` | 30 | Max close date difference |
| `CORRIDOR_BUDGET_PER_TRADE` | $200 | Budget per arbitrage trade |

**Telegram:**
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — primary bot + default chat
- `TG_API_ID`, `TG_API_HASH`, `TG_PHONE` — Telethon userbot for listening
- `TELEGRAM_GROUP2_SOURCE`, `TELEGRAM_GROUP2_TARGET_ID` — event-driven group pair

**API & Integrations:**
- `GOOGLE_API_KEY` — Gemini LLM API
- `GOOGLE_API_KEY_SECONDARY` — fallback Gemini key (referenced in gemini_client.py)
- `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` — OpenRouter fallback
- `CEREBRAS_API_KEY` — Cerebras fallback
- `MEMORY_FACTS_LIMIT` = 30 — max facts injected into system prompt

**Locking & Scheduling:**
- `LOCK_FILE` = `vault/scan.lock`
- `LOCK_TIMEOUT_SEC` = 600 (10 min)
- `SCREENING_INTERVAL_SEC` = 1800 (30 min between auto-scans)

**Logging:**
- `RotatingFileHandler` at `logs/main.log`, 5MB max, 3 backups
- Console handler also attached
- Logger name: `NexusPolyBot`

**Startup validation:**
`startup_check()` raises `RuntimeError` if `GOOGLE_API_KEY`, `TELEGRAM_BOT_TOKEN`, or `TELEGRAM_CHAT_ID` are missing.

### LLM Health Gate:
`llm_health_gate = LLMHealthGate()` is instantiated at module level in config.py (singleton pattern).

---

## 9. Gemini Client (Multi-Provider LLM Router)
**File:** `agents/shared/utils/gemini_client.py` (475 lines)

### `generate_content_with_fallback()` — Central LLM entry point:

**Provider priority** (configurable per-agent via DB key `agent_config_{AGENT_NAME}`):
1. **Cerebras** (if key present) — round-robin across 4 models, 20s wait on 429
2. **OpenRouter** (if key present) — configurable model per agent via env `OPENROUTER_MODEL_{AGENT}`
3. **Gemini** (always available) — tries: default_model → gemini-2.5-flash → gemini-2.0-flash → gemini-2.5-pro, each with primary + secondary API key

### Protocol conversion:
- `convert_gemini_to_openai()` — converts Gemini payload format (contents/systemInstruction/tools/generationConfig) to OpenAI format (messages/tools/response_format) for Cerebras/OpenRouter compatibility
- `convert_openai_to_gemini()` — converts OpenAI response back to Gemini format for uniform downstream processing
- Handles function calling (tool_calls) conversion in both directions with UUID-based tool_call_id tracking

### Observability:
- Every LLM call is logged to `llm_calls` table via `LLMLogger.log_call()` with: agent_name, model, prompt, response, input/output/total tokens, latency_ms, errors
- Consecutive failure counter per agent → Telegram alert after 3 failures with model-switch button

---

All file paths for reference:
- `c:\Users\orlov\.gemini\antigravity-ide\scratch\polimarket\agents\shared\python\market_selector.py`
- `c:\Users\orlov\.gemini\antigravity-ide\scratch\polimarket\agents\shared\utils\web_search.py`
- `c:\Users\orlov\.gemini\antigravity-ide\scratch\polimarket\core\guards.py`
- `c:\Users\orlov\.gemini\antigravity-ide\scratch\polimarket\core\checkpoint.py`
- `c:\Users\orlov\.gemini\antigravity-ide\scratch\polimarket\services\notifications.py`
- `c:\Users\orlov\.gemini\antigravity-ide\scratch\polimarket\agents\shared\python\db.py`
- `c:\Users\orlov\.gemini\antigravity-ide\scratch\polimarket\config.py`
- `c:\Users\orlov\.gemini\antigravity-ide\scratch\polimarket\agents\orchestrator\src\agent.py`
- `c:\Users\orlov\.gemini\antigravity-ide\scratch\polimarket\agents\shared\utils\gemini_client.py`
- `c:\Users\orlov\.gemini\antigravity-ide\scratch\polimarket\core\models.py`
