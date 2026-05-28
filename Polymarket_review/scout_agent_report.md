## SCOUT Agent — Complete Research Report

**Note:** The "scout" agent directory doesn't exist at the path you specified. The actual SCOUT agent lives at `agents/polymarket_mispricing_agent/` with the class `ScoutAgent` in `src/agent.py`. The `GEMINI.md` system prompt is at `agents/polymarket_mispricing_agent/GEMINI.md`.

---

### 1. FULL SYSTEM PROMPT (GEMINI.md)

**File:** `c:\Users\orlov\.gemini\antigravity-ide\scratch\polimarket\agents\polymarket_mispricing_agent\GEMINI.md` (60 lines, 7340 bytes)

Key elements:
- **Role:** "экспертный аналитик рынков предсказаний" — gives independent probability estimate (0.0–1.0)
- **Philosophy — "Honest Double-Blind":** The market price is **intentionally NOT shown** to the LLM to avoid anchoring bias. The LLM's job is to give an honest probability; Edge is computed in Python.
- **RAG (Obsidian):** The prompt tells the agent to use the `=== ИЗВЛЕЧЕННАЯ ПАМЯТЬ ИЗ ОБСИДИАН (RAG) ===` block for: finding analogies, calibrating estimates, leveraging saved facts.
- **Algorithm:** 4 steps — Analyze conditions → Analyze RAG memory → Form hypothesis → Critical self-analysis for cognitive biases.
- **Deep Thinking mode:** Agent is instructed to use maximum tokens, exhaustive Chain-of-Thought, atomic breakdown of problems, step-by-step mathematical calibration.
- **Output schema:** JSON with fields: `estimate_probability`, `confidence`, `priority`, `reasoning`, `signal`, `cause`, `risk`, `oracle_risk`, `verdict`.
- **Critical rule:** `edge` field is FORBIDDEN — edge is calculated only in Python.
- **Migration note:** Planned migration to GPT 5.5 for better analytics.

---

### 2. HOW GROUNDING WORKS (Google Search Integration)

**File:** `agent.py` lines 130–156

Grounding is a **2-step process** within `estimate_market()`:

**Step 1: Grounding search call (lines 130-156)**
```python
search_payload = {
    "contents": [{"role": "user", "parts": [{"text": (
        f"Search for the latest news, fundamental data, statistics... about: '{search_query_llm}'."
    )}]}],
    "tools": [{"google_search": {}}]   # <-- Gemini's native Google Search tool
}
```
- Uses `build_search_query()` from `web_search.py` which strips stopwords from market title, keeps top 6 keywords
- Sends this to Gemini via `generate_content_with_fallback()` **without JSON schema** (free-form text output)
- If it works → `grounded_context` is the extracted text
- On error → fallback string `"Google Search: ошибка ({e})"`
- If empty → `"Google Search: результатов не найдено."`

**Step 2: Main analysis call (lines 158-256)**
- The `grounded_context` is injected into the main prompt under `[Результаты Google Search (grounding)]`
- This second call uses the full system instruction (GEMINI.md) + JSON response schema

**Important:** The Gemini client (`gemini_client.py` line 407-414) automatically **removes** `responseMimeType` and `responseSchema` from the payload when `tools` are present (because Gemini API doesn't support both simultaneously). This is why the grounding call is a separate step.

---

### 3. HOW EDGE IS CALCULATED

**File:** `agent.py` lines 258-310

Edge is computed **purely in Python** after the LLM returns its estimate:

```python
est_prob = float(analysis.get("estimate_probability", 0.5))

# Edge for YES and NO sides
edge_yes = est_prob - market.price
edge_no = (1.0 - est_prob) - (1.0 - market.price)

edge = max(edge_yes, edge_no)
target_outcome = "YES" if edge_yes > edge_no else "NO"
```

**Note:** `edge_no = (1 - est_prob) - (1 - market.price)` simplifies to `market.price - est_prob`, which means `edge_no = -edge_yes`. So `max(edge_yes, edge_no) = abs(edge_yes)`. The edge is always positive — it's the absolute deviation.

**Minimum threshold:** Read from DB memory key `"min_edge"`, defaults to `MIN_EDGE_DEFAULT` from config. Signal is only generated if `edge > min_edge`.

**Double-blind integrity:** The market price IS included in the prompt (line 166: `Текущая цена исхода (YES): {market.price}`) — this contradicts the GEMINI.md philosophy which says "тебе намеренно не сообщается текущая рыночная цена". **This is a design inconsistency** — the price IS passed to the LLM.

---

### 4. HOW EPISODIC MEMORY WORKS

**Two types of episodic memory are used:**

#### A. Episodic Episodes (agent_episodes table)
- **Writing:** In `workflow.py` `process_consensus()` (lines 325-352), after each market analysis, episodes are saved via `save_agent_episode()` with:
  - `agent_name="SCOUT"`, `event_type="signal_evaluated"`
  - `summary=f"Opinion: {verdict} | Reason: {cause}"`
  - `market_id`, `market_title`

- **Reading:** In `agent.py` line 113:
  ```python
  episodes = get_agent_episodes("SCOUT", event_type="signal_evaluated", limit=3)
  ```
  Returns last 3 episodes, formatted as bullet points and injected into prompt under `[Недавний опыт (Эпизодическая память)]`.

#### B. Performance Summary (signal_resolved episodes)
- **Reading:** `agent.py` line 118:
  ```python
  perf_summary = get_performance_summary("SCOUT", 10)
  ```
  Queries `agent_episodes` where `event_type='signal_resolved'` and `outcome IN ('correct','incorrect')`.
  Returns accuracy stats + last 10 results with ✅/❌ icons.
  Injected into prompt under `[Твоя производительность и работа над ошибками]`.

- The prompt explicitly tells the LLM: "Ознакомься со своими недавними предсказаниями и их реальным исходом. Сделай поправку на свою результативность."

#### C. Obsidian RAG Memory
- **File:** `rag.py` — keyword-based search over Obsidian vault files indexed in `vault_index` table.
- Scoring: title match (+10), tag match (+8), content occurrence (+1.5, max 5), multi-keyword bonus (+3 per kw).
- Category weights: `durable` ×1.25, `market-patterns` ×1.15, `daily` ×0.90.
- 3 levels: L1 (2 files, 15 lines), L2 (4 files, 30 lines), L3 (8 files, 60 lines). Default L2.
- Output wrapped in `<archival_memory>` XML tags.

---

### 5. FULL FLOW OF `estimate_market()`

`agent.py` lines 37–310, step by step:

1. **Extract context** (lines 44-48): market, news_titles, reddit_posts, wiki_context from `MarketContext`
2. **Load RAG memory** (lines 50-56): `get_rag_context(title, description)` → Obsidian search → formatted string. On error → fallback empty string.
3. **Load correlations** (lines 58-108):
   - `get_market_correlations(market.id)` from DB
   - For each correlation: fetch related market via PolymarketAdapter, get fresh price
   - Run `math_pre_filter(market, related_market)` → returns `CONFIRMED_ARBITRAGE`, `CONFIRMED_NO_ARBI`, or `AMBIGUOUS` with spread %
   - Format as text block with emoji indicators
4. **Load episodic memory** (lines 112-118): last 3 episodes + performance summary
5. **Build correlation hint** (lines 121-128): from `context.correlation_hint` if exists
6. **STEP 1 — Grounding search** (lines 130-156): Separate Gemini call with `google_search` tool, no schema
7. **Build main prompt** (lines 158-201): Assembles massive prompt with: date, market info, performance, price, RAG, Wikipedia, RSS, Reddit, Google Trends, HackerNews, grounding results, episodes, correlation hints, oracle risk instructions, Russian language requirement
8. **Define JSON schema** (lines 204-218): 9 required fields
9. **STEP 2 — Main LLM call** (lines 220-253):
   - Payload includes systemInstruction (GEMINI.md), JSON schema, prompt
   - **2 attempts** (`for attempt in range(2)`) to get valid JSON
   - Uses `generate_content_with_fallback()` with multi-provider fallback
   - Parse JSON, strip markdown fences
10. **Compute edge** (lines 258-269): Python-side double-blind calculation
11. **Filter by min_edge** (lines 272-274): Read threshold from DB
12. **Build Signal object** (lines 276-306): If edge > threshold, create Signal with all fields
13. **Return** Signal or None

---

### 6. TIMEOUTS, ERROR HANDLING, FALLBACKS

#### A. `@with_retry` decorator (llm_wrapper.py)
- Applied to `estimate_market()` with `max_attempts=3, initial_backoff=2.0`
- **Exponential backoff:** 2s → 4s → 8s between retries
- Checks `LLMHealthGate` before each call
- On 429/503 errors → records error in health gate
- After all attempts exhausted → raises `LLMUnavailableError`

#### B. `LLMHealthGate` (guards.py)
- 3 states: `HEALTHY`, `DEGRADED`, `DEAD`
- Window: 60 seconds
- 3 errors in 60s → DEGRADED (60s pause)
- 5 errors in 60s → DEAD (300s / 5 min pause)
- On success → resets to HEALTHY

#### C. `generate_content_with_fallback()` (gemini_client.py)
- **Provider cascade order:** Cerebras → OpenRouter → gemini-2.5-flash → gemini-2.0-flash → gemini-2.5-pro
- Can be overridden via DB config (`agent_config_SCOUT`)
- **Timeout:** 120 seconds per HTTP request
- **Cerebras specifics:** Cycles through 4 models (qwen-3-235b, gpt-oss-120b, zai-glm-4.7, llama3.1-8b) with round-robin. On 429 → 20 second sleep then next model.
- **Gemini specifics:** Tries primary + secondary API key. Exponential backoff between models: `min(0.5 * 2^attempt, 5.0)` seconds.
- **OpenRouter:** Single attempt per call.
- **Consecutive failures tracking:** After 3 consecutive failures for an agent → sends Telegram alert to switch model.
- All calls logged via `LLMLogger.log_call()`

#### D. Internal `estimate_market()` retry
- Lines 234-253: Inner loop of 2 attempts for JSON parsing
- If JSON parse fails → retry with same model cascade

#### E. Workflow-level error handling (engine.py)
- `LLMUnavailableError` → checkpoint saved, scan loop breaks, user notified via Telegram
- Generic exceptions → logged with traceback, sent to Telegram, market skipped, loop continues
- `_scan_lock` prevents concurrent scans (non-blocking acquire)

#### F. Data source fallbacks
- RSS/Reddit/Wikipedia/HackerNews: All wrapped in try/except → return empty list on failure
- Google Trends: returns "недоступно" string on failure
- RAG memory: returns "нет релевантных записей" on failure
- All news sources cached for 15 minutes (TTL cache with thread-safe locking)

---

### 7. HOW CORRELATION ANALYSIS WORKS

**3-layer system:**

#### Layer 1: Detection (NEXUS screening)
- `NexusAgent.screen_markets()` (called in `workflow.py` `run_screening()`) detects correlations between markets during screening
- Correlations saved to `correlations` table with: `market_id_a`, `market_id_b`, `correlation_type`, `description`, `confidence`

#### Layer 2: Enrichment in `run_agent_evaluation()` (workflow.py lines 162-178)
- Before analysis, `get_market_correlations(m.id)` fetches up to 3 related markets
- Formats as `correlation_hint` with type (IMPLICATION, MUTUAL_EXCLUSION, etc.) and confidence %
- This hint is added to `MarketContext` and injected into the prompt

#### Layer 3: Math Pre-Filter in `estimate_market()` (agent.py lines 58-108 + math_filter.py)
For each correlated market:
1. Fetch fresh price via `PolymarketAdapter.get_market()`
2. Run `math_pre_filter(market_a, market_b)` which checks:
   - **Monotonicity violation:** If higher threshold costs more than lower threshold → arbitrage (e.g., "$1.8T" vs "$500B"). Checks if `P(higher) > P(lower)` and spread ≥ 5%.
   - **Complementary overpriced:** If complementary outcomes (e.g., Democrat + Republican) sum > 1.0 and spread ≥ 5% → confirmed arbitrage
   - **Complementary underpriced:** Sum < 1.0 → AMBIGUOUS (favorable bet, needs LLM)
   - **Cross-platform price divergence:** Different platforms, spread ≥ 5% → AMBIGUOUS
   - **Fallback:** AMBIGUOUS with 0% spread
3. Results formatted with emojis: ⚡ CONFIRMED_ARBITRAGE, ✅ NO_ARBITRAGE, ⚠️ AMBIGUOUS
4. Injected into prompt for the LLM to interpret

The prompt tells SCOUT: "Используй известные корреляции и их математический анализ (см. блок MATH-FILTER выше) как фактическую основу. Числа спреда и тип арбитража уже посчитаны — тебе нужно интерпретировать их смысл."

---

### Key Files Read:
- `agents/polymarket_mispricing_agent/src/agent.py` (370 lines)
- `agents/polymarket_mispricing_agent/GEMINI.md` (60 lines)
- `core/workflow.py` (353 lines)
- `core/engine.py` (349 lines)
- `core/math_filter.py` (168 lines)
- `core/guards.py` (77 lines)
- `agents/shared/python/llm_wrapper.py` (63 lines)
- `agents/shared/utils/gemini_client.py` (475 lines)
- `agents/shared/utils/web_search.py` (180 lines)
- `agents/shared/utils/rag.py` (209 lines)
- `agents/shared/python/db.py` (lines 609-1408 — correlations, episodes, performance summary)

No `prompts.py` file exists — all prompts are inline in `agent.py`.