## Detailed Research Report: SWING and SHADOW Agent Implementations

**Note on naming:** The user asked about "swing" and "shadow" agents. In the codebase they are located at:
- **SWING** → `agents/polymarket_swing_agent/src/agent.py` (class `SwingAgent`)
- **SHADOW** → `agents/polymarket_insider_agent/src/agent.py` (class `ShadowAgent`)

---

### 1. FULL SYSTEM PROMPTS

Both agents load their system prompt from the GEMINI.md file in their agent directory at `__init__`:

#### SWING (GEMINI.md — 64 lines, 6657 bytes)
**Role:** "SWING_TRADER — хладнокровный спекулянт и охотник за волатильностью". Finds markets with high short-term hype ("pump") potential to buy cheap and sell on crowd emotions before the event resolves.

**Philosophy (4 rules):**
1. Fundamentals don't matter — only predict if the crowd BELIEVES something may happen
2. Bi-directional analysis (YES/NO) — buy whichever side the narrative favors
3. Price chart analysis — if pump already happened, don't buy; look for emerging hype
4. Sell-the-news — sell at hype peak

**Analysis criteria:**
1. Memability/virality — can media pick this up?
2. Timing — enough time before close for pump to form?
3. Price & chart — is the run starting? Does entry risk justify?

**Data sources the agent receives:**
1. Google Search results (last 48h)
2. Google Trends (0-100 interest index)
3. HackerNews (tech discussions)
4. RSS headlines with dates
5. Reddit posts (score indicates virality)

**Expected JSON output schema:**
```json
{
  "hype_potential": 0.85,      // 0.0-1.0
  "recommendation": "buy",     // "buy" or "ignore"
  "target_outcome": "YES",
  "target_exit_price": 0.35,
  "confidence": 0.9,
  "reasoning": "...",
  "catalyst": "...",            // only if buy
  "catalyst_absence_reason": "", // only if ignore
  "swing_risk": "...",          // always filled
  "swing_verdict": "..."        // concrete action or refusal
}
```

**Rules:** catalyst is mandatory when buy, catalyst_absence_reason is mandatory when ignore (no "N/A" allowed), swing_risk always filled. All text must be in Russian.

---

#### SHADOW (GEMINI.md — 120 lines, 12222 bytes)
**Role:** Expert on liquidity, orderbook, and trading volumes. Verifies SCOUT's ideas using real orderbook data (CLOB API) and price history.

**Critical micro-bank rule ($10-$100):** The user operates with $10-$100 stakes. Wide spreads (50%, 80%, 95%) are NOT reasons to reject. Thin orderbooks (5-10 levels) are normal. Slippage on $50 order against $500+ depth is negligible.

**Reject (`agree: false`) ONLY when:**
1. Orderbook is ABSOLUTELY EMPTY — less than $5 liquidity on entry side
2. Clear pump & dump signs — sharp price spike in 1-2h on thin orderbook without news

**Data received:**
- Orderbook (CLOB API): spread, bid/ask depth (5 levels), asymmetry, level count
- Price history (24h): trend, speed, anomalies
- Smart Money / Whale Alerts: total volume YES vs NO, win rates, concentration

**5-step algorithm:**
1. Liquidity assessment — at least $10 on entry side? → trade is possible
2. Orderbook asymmetry — Bid/Ask ratio >3.0 bullish, <0.3 bearish, 0.5-2.0 neutral
3. Smart money analysis — whale dominance in SCOUT direction ≥$10K = strong bullish (+0.15 confidence); counter ≥$20K = warning; high win-rate traders confirming = very strong
4. Price history trend — stable growth + deep bid = organic; sharp spike + thin book = pump (only reason to reject); decline + growing ask = caution
5. Final decision table:
   - Good liquidity + bullish asymmetry + SM confirm → agree, 0.85-0.95, low risk
   - Normal liquidity, neutral → agree, 0.55-0.70, medium
   - Thin book but $10+ → agree, 0.45-0.60, high
   - No data → agree, 0.30, medium
   - Pump signs → false, 0.75-0.90, high
   - Empty book (<$5) → false, 0.85-0.95, high

**Expected JSON output:**
```json
{
  "agree": true,
  "confidence": 0.65,
  "liquidity_risk": "medium",   // low/medium/high
  "opinion": "...",              // full analytical report (3+ paragraphs)
  "orderbook_facts": "...",     // 1-2 sentences with concrete numbers
  "risk_assessment": "...",     // max safe order size, risk source, SM factor
  "shadow_verdict": "..."       // type of order + max size + condition OR refusal reason
}
```

---

### 2. HOW EACH AGENT PROCESSES DATA

#### SWING Agent (`SwingAgent.estimate_market`)
**Input:** `MarketContext` + optional `price_history`
**Processing flow:**
1. Extract market data, news, reddit posts, wiki context from `MarketContext`
2. Load RAG context from Obsidian knowledge base (`get_rag_context`)
3. Format price history (last 6 points)
4. Load episodic memory — last 3 evaluations (`get_agent_episodes("SWING", "signal_evaluated", 3)`)
5. Load performance summary (`get_performance_summary("SWING", 10)`)
6. **STEP 1 — Google Search grounding call** (separate LLM call without JSON schema): sends a search query about the market to find latest news/viral activity in last 48h using `tools: [{"google_search": {}}]`
7. Build prompt with ALL data (date, market info, perf summary, RAG, wiki, price history, RSS, Reddit, Google Search results, Google Trends, HackerNews, episodic memory)
8. **STEP 2 — Analysis call**: sends to LLM with JSON schema enforced (`responseMimeType: "application/json"`, `responseSchema`)
9. Parse JSON, calculate ROI: `roi = ((target_exit_price - current_price) / current_price) * 100`
10. Build `SwingSignal` with all fields and return

**Return type:** `SwingSignal` (or None on failure)

#### SHADOW Agent (`ShadowAgent.analyze_idea`)
**Input:** `MarketContext` + `scout_opinion` (string) + optional `orderbook` (dict) + optional `price_history`
**Processing flow:**
1. Extract market and smart_money from context
2. Format orderbook data: spread, top bid/ask, depth at 5 levels, total levels, bid/ask asymmetry ratio
3. Format price history (last 6 points)
4. Format smart money block: total YES/NO USD volumes, YES dominance %, top wallets with aliases and win rates
5. Load RAG context from Obsidian
6. Load episodic memory — last 3 evaluations (`get_agent_episodes("SHADOW", "signal_evaluated", 3)`)
7. Load performance summary (`get_performance_summary("SHADOW", 10)`)
8. Build prompt with all data + SCOUT's opinion to verify
9. Send to LLM with JSON schema enforced
10. Parse JSON, combine opinion + verdict into `AgentOpinion`

**Return type:** `AgentOpinion` (or None on failure)

---

### 3. HOW SIGNALS ARE GENERATED AND STRUCTURED

#### SwingSignal (Pydantic model, `core/models.py:47-69`)
```python
class SwingSignal(BaseModel):
    id: str                       # "sig-swing-{market_id}-{timestamp}"
    market_id: str
    platform: str
    type: str = "SWING"
    edge: Optional[float] = 0.0
    priority: str = "medium"
    summary: str                  # e.g. "🚀 Памп YES (Хайп 85%, Цель 0.35)"
    details: str                  # Full recommendation text with ROI
    hype_potential: float         # 0.0-1.0
    recommendation: str           # "buy" | "ignore"
    target_outcome: str           # "YES" | "NO"
    target_exit_price: float
    confidence: float
    reasoning: str
    catalyst: str
    catalyst_absence_reason: str
    swing_risk: str
    swing_verdict: str
    created_at: datetime
```

#### AgentOpinion (for SHADOW, `core/models.py:71-84`)
```python
class AgentOpinion(BaseModel):
    agent_name: str               # "SHADOW"
    market_id: str
    opinion: str                  # Combined opinion + verdict
    confidence: float
    agree: bool                   # THE KEY FIELD for consensus
    orderbook_facts: str
    risk_assessment: str
    shadow_verdict: str
    liquidity_risk: str           # "low" | "medium" | "high"
    created_at: datetime
```

#### Signal (for SCOUT, `core/models.py:18-46`)
```python
class Signal(BaseModel):
    id: str
    type: str                     # "MISPRICING"
    market_id: str
    platform: str
    target_outcome: str = "YES"
    edge: Optional[float]        # clamped 0.0-1.0
    confidence: float             # clamped 0.0-1.0
    priority: Literal['low', 'medium', 'high']
    summary: str
    details: str
    signal_cause: str
    signal_risk: str
    signal_verdict: str
    oracle_risk: str
    status: Literal['PENDING', 'EXECUTED', 'REJECTED', 'ARCHIVED', 'EVALUATED']
    created_at: datetime
```

---

### 4. HOW CONSENSUS WORKS

#### `make_consensus()` (`core/workflow.py:212-234`)
Simple deterministic logic, no LLM involved:

```python
def make_consensus(context, signal, swing_signal, opinion_shadow) -> IdeaDecision:
    shadow_ok = opinion_shadow and opinion_shadow.agree  # SHADOW's agree field is THE gate
    
    valid_scout = signal is not None                      # SCOUT found mispricing
    valid_swing = swing_signal is not None and swing_signal.recommendation == 'buy'  # SWING says buy
    
    if (valid_scout or valid_swing) and shadow_ok:
        status = 'saved'          # CONSENSUS REACHED
    elif (valid_scout or valid_swing):
        status = 'no_consensus'   # Signal exists but SHADOW rejected
    else:
        status = 'no_signal'      # Neither SCOUT nor SWING found anything
    
    return IdeaDecision(market_id, status, scout_signal, swing_signal, shadow_opinion)
```

**Key insight:** SHADOW is the GATEKEEPER. Either SCOUT or SWING can propose an idea, but SHADOW must `agree: true` for it to be saved. The `liquidity_risk` field is explicitly NOT checked (comment says micro-bank $10-100 makes high liquidity_risk normal).

#### `process_consensus()` (`core/workflow.py:236-353`)
Handles the result of `make_consensus()`:

1. Calls `make_consensus()` → gets `IdeaDecision`
2. **If 'saved':** saves signal(s) to DB, increments ideas_found counter
3. **If 'no_consensus':** logs "SHADOW забраковал" (SHADOW rejected)
4. **If 'no_signal':** logs "Идей не найдено"
5. **Sends rich Telegram notification** via `summary_callback` with formatted HTML:
   - SCOUT section (cause, risk, oracle risk, verdict)
   - SWING section (catalyst or absence reason, risk, verdict)
   - SHADOW section (agree/reject, liquidity risk, orderbook facts, execution risk, verdict)
   - Final result line (consensus/no_consensus/no_signal + initiator agents)
6. **Saves idea audit** to DB: `save_idea_audit(market_id, title, audit_dict)`
7. **Saves checkpoint** + verifies it was written
8. **Saves episodic memory** for each agent that participated (SCOUT, SWING, SHADOW)

---

### 5. SMART MONEY ANALYSIS FLOW

**Full flow in `core/engine.py:220-228`:**

```python
# 1. Fetch raw onchain data
onchain_trades = get_recent_trades(m.condition_id)     # from CLOB API
onchain_positions = get_top_positions(m.condition_id)   # from Gamma API

# 2. Analyze and aggregate
smart_money = analyze_smart_money(onchain_trades, onchain_positions)

# 3. Inject into context
context.smart_money = smart_money
```

#### `services/onchain_provider.py` — Data fetching
- **CLOB API base:** `https://clob.polymarket.com`
- **Gamma API base:** `https://gamma-api.polymarket.com`
- **TTL cache:** 300 seconds (5 min), in-memory dict `_cache`
- **HTTP timeout:** 10 seconds
- `get_recent_trades(condition_id, limit=50)` → `GET /trades?condition_id={id}&limit=50`
- `get_top_positions(condition_id, min_usd=500)` → `GET /positions?conditionId={id}&sizeThreshold=500`

#### `core/smart_money.py` — Analysis (`analyze_smart_money`)
**Input:** trades list + positions list
**Processing:**
1. If both empty → returns `SmartMoneySummary(available=False)`
2. Aggregates by wallet address: sums YES_USD and NO_USD for each wallet
   - `outcome_index=0` → YES, `outcome_index=1` → NO
   - USD = `size * price`
3. Looks up known whales from DB (`get_known_whales()` → `{address: {alias, win_rate}}`)
4. Sorts top 5 wallets by total volume
5. For each top wallet: formats as `"alias (WR: 72%) → YES $15,000"`
6. Returns `SmartMoneySummary`:
   - `available: True`
   - `total_yes_usd`, `total_no_usd`
   - `yes_dominance` (0.0-1.0)
   - `top_wallets` (list of formatted strings)
   - `summary` (joined text)

#### `SmartMoneySummary` model (`core/context.py:5-11`)
```python
class SmartMoneySummary(BaseModel):
    available: bool
    total_yes_usd: float = 0.0
    total_no_usd: float = 0.0
    yes_dominance: float = 0.5
    top_wallets: List[str] = []
    summary: str = "Крупных сделок не найдено."
```

---

### 6. ORDERBOOK ANALYSIS

**Orderbook fetching** happens in `core/engine.py:209-215`:
```python
orderbook = None
target_outcome = getattr(active_signal, 'target_outcome', 'YES')
if m.tokens:
    token_idx = 1 if target_outcome.upper() == 'NO' and len(m.tokens) > 1 else 0
    orderbook = self.adapter.get_orderbook(m.tokens[token_idx])
```
- Uses correct token ID based on target outcome (YES=index 0, NO=index 1)
- Fetched via `PolymarketAdapter.get_orderbook()`

**Orderbook formatting** happens in SHADOW's `analyze_idea()` (lines 39-53):
```
=== ДАННЫЕ ОРДЕРБУКА (CLOB API) ===
Спред: {spread}
Top Bid: {top_bid} | Top Ask: {top_ask}
Глубина Bid (5 lvl): ${bid_depth_5} | Ask: ${ask_depth_5}
Всего уровней — Bids: {total_bids} | Asks: {total_asks}
Асимметрия Bid/Ask: {bid_depth_5 / ask_depth_5}x
```

**Orderbook is ONLY used by SHADOW.** SWING does NOT receive orderbook data.

---

### 7. TIMEOUTS, ERROR HANDLING, FALLBACKS

#### LLM Call Retry (`agents/shared/python/llm_wrapper.py`)
- **Decorator:** `@with_retry(max_attempts=3, initial_backoff=2.0)`
  - Both SWING and SHADOW are decorated with this
  - Exponential backoff: 2s → 4s → 8s
  - Checks `LLMHealthGate.check_availability()` before each call
  - On 429/503 errors → `llm_health_gate.record_error(status_code)`
  - On success → `llm_health_gate.record_success()`
  - After all attempts exhausted → raises `LLMUnavailableError`

#### LLM Health Gate (`core/guards.py`)
- **3 states:** HEALTHY → DEGRADED → DEAD
- **Thresholds within 60-second window:**
  - 3+ errors (429/503) → DEGRADED (pause 60s)
  - 5+ errors → DEAD (pause 300s / 5min)
- On DEAD → raises `LLMUnavailableError`, halts all scanning
- After pause expires → auto-resets to HEALTHY (circuit breaker pattern)

#### `generate_content_with_fallback()` (`agents/shared/utils/gemini_client.py:208-475`)
Multi-provider fallback chain:
1. **Cerebras** (if key set) — cycles through 4 models: `qwen-3-235b`, `gpt-oss-120b`, `zai-glm-4.7`, `llama3.1-8b`
   - 429 error → waits 20 seconds, then tries next model
2. **OpenRouter** (if key set) — configurable model per agent via env var `OPENROUTER_MODEL_{AGENT}`
3. **Gemini** — tries multiple models: default → `gemini-2.5-flash` → `gemini-2.0-flash` → `gemini-2.5-pro`
   - Tries primary + secondary API key (`GOOGLE_API_KEY_SECONDARY`)
   - Exponential backoff between Gemini attempts: `min(0.5 * 2^attempt, 5.0)` seconds
- Per-agent model override from DB: `get_memory(f"agent_config_{AGENT}")`
- **HTTP timeout:** 120 seconds for all providers
- After 3 consecutive total failures → sends Telegram alert with model-switch button
- All calls are logged via `LLMLogger.log_call()`

#### Inner JSON parsing retry (in both agents)
Both SWING and SHADOW have an inner `for attempt in range(2)` loop around JSON parsing:
- Attempt 1: call LLM, parse JSON
- Attempt 2: retry if JSON parsing failed
- Strips markdown code blocks (````json`, `````)
- Uses `json.loads(content, strict=False)` for lenient parsing

#### Error handling in `engine.py` per-market loop:
- `LLMUnavailableError` → logs, sends Telegram alert, **breaks the entire loop** (stops scanning)
- Any other Exception → logs with traceback, sends error to Telegram, **continues to next market**
- Finally block: removes market from `active_markets` dict

#### Checkpointing (`core/checkpoint.py`)
- In-memory dict + debounced file persistence (2-second timer)
- Saved at each phase: `screening`, `scout_{market_id}`, `swing_{market_id}`, `shadow_{market_id}`, `consensus_{market_id}`
- Verified after consensus: `verify_checkpoint()` checks status is "ok"/"success"
- File location: `vault/checkpoints.json`

#### Scan lock (`core/engine.py:77-83`)
- `threading.Lock` prevents concurrent scans
- Non-blocking acquire: if already running → raises `RuntimeError` with user-friendly message

#### RAG memory fallback (both agents)
```python
try:
    rag_context = get_rag_context(market.title, market.description)
except Exception:
    rag_context = "В базе знаний Obsidian нет релевантных записей для этого рынка.\n"
```

---

### KEY FILES REFERENCE
- SWING agent: `agents/polymarket_swing_agent/src/agent.py` (216 lines)
- SWING prompt: `agents/polymarket_swing_agent/GEMINI.md` (64 lines)
- SHADOW agent: `agents/polymarket_insider_agent/src/agent.py` (194 lines)
- SHADOW prompt: `agents/polymarket_insider_agent/GEMINI.md` (120 lines)
- Consensus logic: `core/workflow.py` (353 lines) — `make_consensus()` at line 212, `process_consensus()` at line 236
- Smart money: `core/smart_money.py` (59 lines)
- Onchain provider: `services/onchain_provider.py` (44 lines)
- Models: `core/models.py` (155 lines)
- Context: `core/context.py` (33 lines)
- Engine: `core/engine.py` (349 lines)
- LLM wrapper: `agents/shared/python/llm_wrapper.py` (63 lines)
- Gemini client: `agents/shared/utils/gemini_client.py` (475 lines)
- Guards/health gate: `core/guards.py` (77 lines)
- Checkpoint: `core/checkpoint.py` (68 lines)
