import os
import json
from core.models import Market, CrossArbitrageSignal
from agents.shared.utils.gemini_client import generate_content_with_fallback, extract_response_text
from core.math_filter import math_pre_filter, FilterDecision


class ArbitrageAgent:
    """
    Агент ARBITRAGE — ищет математические и логические противоречия между рынками.
    """

    ARBITRAGE_TYPES = {
        "price_divergence":      "💰 Прямое ценовое расхождение",
        "logical_contradiction": "🧠 Логическое противоречие",
        "pair_trade":            "🔗 Парный трейд",
        "none":                  "Нет арбитража",
    }

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model

        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base_path, "GEMINI.md"), "r", encoding="utf-8") as f:
            self.system_instruction = f.read()

    # ── Общая часть схемы для обоих методов ──────────────────────────────
    _TRADE_SCHEMA_EXTRA = {
        "action_a": {
            "type": "STRING",
            "description": "Действие по рынку A: BUY_YES | BUY_NO | SELL_YES | SKIP"
        },
        "action_b": {
            "type": "STRING",
            "description": "Действие по рынку B: BUY_YES | BUY_NO | SELL_YES | SKIP"
        },
        "entry_price_a_cents": {
            "type": "NUMBER",
            "description": "Рекомендуемая цена входа для рынка A в центах (0-100)"
        },
        "entry_price_b_cents": {
            "type": "NUMBER",
            "description": "Рекомендуемая цена входа для рынка B в центах (0-100)"
        },
        "expected_pnl_pct": {
            "type": "NUMBER",
            "description": "Ожидаемый P&L в % при вложении $100, с учётом комиссии 2%"
        },
        "risk_level": {
            "type": "STRING",
            "description": "LOW (математически гарантировано) | MEDIUM (вероятностное) | HIGH (спекулятивное)"
        },
    }

    _TRADE_PROMPT_SUFFIX = """

Дополнительно рассчитай торговую рекомендацию:
- action_a / action_b: что делать с каждым рынком (BUY_YES / BUY_NO / SELL_YES / SKIP)
- entry_price_a_cents / entry_price_b_cents: оптимальная цена входа в центах
- expected_pnl_pct: ожидаемый P&L в % при бюджете $100 (учти комиссию Polymarket 2%)
- risk_level:
    LOW — математически гарантированный исход (inverse pair, causal implication)
    MEDIUM — вероятностный арбитраж (pair_trade)
    HIGH — спекулятивная идея без гарантий

Если арбитража нет: action_a = action_b = "SKIP", expected_pnl_pct = 0, risk_level = "HIGH"."""

    _TRADE_REQUIRED_EXTRA = [
        "action_a", "action_b",
        "entry_price_a_cents", "entry_price_b_cents",
        "expected_pnl_pct", "risk_level",
    ]

    # ─── Режим 1: Внутриплатформенный арбитраж (по корреляции) ──────────────

    def analyze_correlation(
        self,
        market_a: Market,
        market_b: Market,
        correlation_type: str,
        score: int,
    ) -> CrossArbitrageSignal | None:
        """Анализирует пару связанных рынков с одной платформы."""
        # ── Math pre-filter: экономим LLM-вызов ────────────────────────────────
        mf = math_pre_filter(
            market_a,
            market_b,
            check_logical_implication=(correlation_type == "logical_implication")
        )
        
        if mf.decision == FilterDecision.CONFIRMED_NO_ARBI:
            print(f"[MATH-FILTER] [REJECTED] ({mf.arbitrage_type}): {mf.reasoning}")
            return None
        
        if mf.decision == FilterDecision.CONFIRMED_ARBITRAGE:
            print(f"[MATH-FILTER] [CONFIRMED] ({mf.arbitrage_type}): spread={mf.spread_pct:.1f}%")
            
            if mf.arbitrage_type in ("complementary_overpriced",):
                action_a, action_b = "BUY_NO", "BUY_NO"
            elif mf.arbitrage_type in ("complementary_underpriced",):
                action_a, action_b = "BUY_YES", "BUY_YES"
            elif mf.arbitrage_type == "monotonicity_violation":
                action_a, action_b = "BUY_YES", "BUY_NO"
            else:
                action_a, action_b = "BUY_YES", "SELL_YES"

            return CrossArbitrageSignal(
                market_a_id=market_a.id,
                market_a_platform=market_a.platform,
                market_a_title=market_a.title,
                market_a_price=market_a.price,
                market_a_url=market_a.url,
                market_b_id=market_b.id,
                market_b_platform=market_b.platform,
                market_b_title=market_b.title,
                market_b_price=market_b.price,
                market_b_url=market_b.url,
                has_arbitrage=True,
                arbitrage_type="logical_contradiction" if mf.arbitrage_type in ["monotonicity_violation", "complementary_overpriced", "complementary_underpriced"] else mf.arbitrage_type,
                spread_percent=mf.spread_pct,
                reasoning=mf.reasoning,
                trade_instruction=mf.trade_instruction,
                match_score=float(score) / 100.0 if score > 1 else float(score),
                action_a=action_a,
                action_b=action_b,
                entry_price_a_cents=round(market_a.price * 100, 1),
                entry_price_b_cents=round(market_b.price * 100, 1),
                expected_pnl_pct=round(mf.spread_pct - 2.0, 1),
                risk_level="LOW",
            )
        # mf.decision == AMBIGUOUS → продолжаем в LLM ниже

        from agents.shared.utils.prompt_guards import guard_description
        desc_a = guard_description(market_a.description) if (hasattr(market_a, "description") and market_a.description) else (
            "⚠️ description_a ОТСУТСТВУЕТ — logical_contradiction невозможно."
        )
        desc_b = guard_description(market_b.description) if (hasattr(market_b, "description") and market_b.description) else (
            "⚠️ description_b ОТСУТСТВУЕТ — logical_contradiction невозможно."
        )

        prompt = f"""Оцени следующую пару рынков на предмет кросс-рыночного арбитража.
Тип корреляции, обнаруженный системой: {correlation_type} (score: {score})

=== РЫНОК A: {market_a.title} ===
Цена YES: {market_a.price}
{desc_a}

=== РЫНОК B: {market_b.title} ===
Цена YES: {market_b.price}
{desc_b}

Есть ли здесь логическое или математическое противоречие (расхождение) в ценах?"""

        schema = {
            "type": "OBJECT",
            "properties": {
                "has_arbitrage":     {"type": "BOOLEAN"},
                "arbitrage_type":    {"type": "STRING"},
                "spread_percent":    {"type": "NUMBER"},
                "reasoning":         {"type": "STRING"},
                "trade_instruction": {"type": "STRING"},
                **self._TRADE_SCHEMA_EXTRA,
            },
            "required": ["has_arbitrage", "arbitrage_type", "spread_percent",
                         "reasoning", "trade_instruction",
                         *self._TRADE_REQUIRED_EXTRA],
        }

        prompt += self._TRADE_PROMPT_SUFFIX

        result, _ = self._call_llm(prompt, schema, agent_name="ARBITRAGE")
        if not result:
            return None

        try:
            content = extract_response_text(result)
            content = content.replace("```json", "").replace("```", "").strip()
            data = json.loads(content, strict=False)

            # Гард: logical_contradiction без description — понижаем автоматически
            if data.get("arbitrage_type") == "logical_contradiction":
                desc_a_missing = not (hasattr(market_a, "description") and market_a.description)
                desc_b_missing = not (hasattr(market_b, "description") and market_b.description)
                if desc_a_missing or desc_b_missing:
                    print(
                        "[ARBITRAGE] logical_contradiction при отсутствующем description. "
                        "Автопонижение до statistical_pair_trade."
                    )
                    data["arbitrage_type"] = "statistical_pair_trade"
                    data["reasoning"] = (
                        "[AUTO-DOWNGRADE] Тип изменён с logical_contradiction на statistical_pair_trade: "
                        "описание одного из рынков недоступно, проверка оракулов невозможна.\n"
                    ) + data.get("reasoning", "")

            spread_val = float(data.get("spread_percent") or mf.spread_pct)
                
            return CrossArbitrageSignal(
                market_a_id=market_a.id,
                market_a_platform=market_a.platform,
                market_a_title=market_a.title,
                market_a_price=market_a.price,
                market_a_url=market_a.url,
                market_b_id=market_b.id,
                market_b_platform=market_b.platform,
                market_b_title=market_b.title,
                market_b_price=market_b.price,
                market_b_url=market_b.url,
                has_arbitrage=data.get("has_arbitrage", False),
                arbitrage_type=data.get("arbitrage_type", "none"),
                spread_percent=round(spread_val, 2),
                reasoning=data.get("reasoning", ""),
                trade_instruction=data.get("trade_instruction", ""),
                match_score=float(score)/100.0 if score > 1 else float(score),
                action_a=data.get("action_a", "SKIP"),
                action_b=data.get("action_b", "SKIP"),
                entry_price_a_cents=data.get("entry_price_a_cents"),
                entry_price_b_cents=data.get("entry_price_b_cents"),
                expected_pnl_pct=data.get("expected_pnl_pct"),
                risk_level=data.get("risk_level", "MEDIUM"),
            )
        except Exception as e:
            print(f"[ARBITRAGE] Ошибка парсинга (correlation): {e}")
            return None

    # ─── Режим 2: Кросс-платформенный арбитраж ──────────────────────────────

    from agents.shared.python.llm_wrapper import with_retry
    @with_retry(max_attempts=3, initial_backoff=2.0)
    def analyze_cross_platform(
        self,
        market_a: Market,
        market_b: Market,
        match_score: float,
        orderbook_b: dict = None,
    ) -> CrossArbitrageSignal | None:
        """
        Анализирует пару рынков с РАЗНЫХ платформ.
        Определяет тип арбитража: ценовое расхождение, логическое противоречие или парный трейд.
        """
        # ── Math pre-filter: экономим LLM-вызов ────────────────────────────────
        mf = math_pre_filter(market_a, market_b)
        
        if mf.decision == FilterDecision.CONFIRMED_NO_ARBI:
            print(f"[MATH-FILTER] [REJECTED] ({mf.arbitrage_type}): {mf.reasoning}")
            return None
        
        if mf.decision == FilterDecision.CONFIRMED_ARBITRAGE:
            print(f"[MATH-FILTER] [CONFIRMED] ({mf.arbitrage_type}): spread={mf.spread_pct:.1f}%")

            if mf.arbitrage_type in ("complementary_overpriced",):
                action_a, action_b = "BUY_NO", "BUY_NO"
            elif mf.arbitrage_type in ("complementary_underpriced",):
                action_a, action_b = "BUY_YES", "BUY_YES"
            elif mf.arbitrage_type == "monotonicity_violation":
                action_a, action_b = "BUY_YES", "BUY_NO"
            else:
                action_a, action_b = "BUY_YES", "SELL_YES"

            return CrossArbitrageSignal(
                market_a_id=market_a.id,
                market_a_platform=market_a.platform,
                market_a_title=market_a.title,
                market_a_price=market_a.price,
                market_a_url=market_a.url,
                market_b_id=market_b.id,
                market_b_platform=market_b.platform,
                market_b_title=market_b.title,
                market_b_price=market_b.price,
                market_b_url=market_b.url,
                has_arbitrage=True,
                arbitrage_type="logical_contradiction" if mf.arbitrage_type in ["monotonicity_violation", "complementary_overpriced", "complementary_underpriced"] else mf.arbitrage_type,
                spread_percent=mf.spread_pct,
                reasoning=mf.reasoning,
                trade_instruction=mf.trade_instruction,
                match_score=match_score,
                action_a=action_a,
                action_b=action_b,
                entry_price_a_cents=round(market_a.price * 100, 1),
                entry_price_b_cents=round(market_b.price * 100, 1),
                expected_pnl_pct=round(mf.spread_pct - 2.0, 1),
                risk_level="LOW",
            )
        # mf.decision == AMBIGUOUS → продолжаем в LLM ниже

        direct_spread = abs(market_a.price - market_b.price)
        spread_percent = round(direct_spread * 100, 2)
        
        # Интеграция стакана заявок (RISK-10)
        book_info = ""
        if orderbook_b:
            book_info = (f"\n  Стакан (Best Bid: {orderbook_b.get('top_bid', 'N/A')}, "
                         f"Best Ask: {orderbook_b.get('top_ask', 'N/A')})\n")

        # BUG-03: Убрано дублирующее "Отвечай строго JSON." 
        from agents.shared.utils.prompt_guards import guard_description
        desc_a = guard_description(market_a.description) if (hasattr(market_a, "description") and market_a.description) else (
            "⚠️ description_a ОТСУТСТВУЕТ — logical_contradiction невозможно."
        )
        desc_b = guard_description(market_b.description) if (hasattr(market_b, "description") and market_b.description) else (
            "⚠️ description_b ОТСУТСТВУЕТ — logical_contradiction невозможно."
        )

        prompt = f"""Ты — аналитик кросс-платформенного арбитража рынков предсказаний.

Два рынка с РАЗНЫХ платформ, которые, по всей видимости, описывают ОДНО событие:

Рынок A ({market_a.platform.upper()}):
  Название: {market_a.title}
  Цена YES: {market_a.price:.3f} ({int(market_a.price * 100)}¢)
  URL: {market_a.url}
  Закрытие: {market_a.close_time.strftime('%Y-%m-%d')}

{desc_a}

Рынок B ({market_b.platform.upper()}):
  Название: {market_b.title}
  Цена YES (mid-price): {market_b.price:.3f} ({int(market_b.price * 100)}¢){book_info}
  URL: {market_b.url}
  Закрытие: {market_b.close_time.strftime('%Y-%m-%d')}

{desc_b}

Вычисленный keyword-match: {match_score:.2f}
Прямой спред цен YES: {spread_percent:.1f}¢

Твоя задача:
1. Убедись, что оба рынка действительно об одном событии (учитывай нюансы формулировок).
2. Определи тип арбитража:
   - price_divergence: YES на A стоит значительно дешевле/дороже YES на B без причины.
   - logical_contradiction: Рынки логически несовместимы — оба не могут быть верными.
   - pair_trade: Коррелированные рынки, где ставка на одном хеджирует другой.
   - none: Нет значимого арбитража.
3. Дай конкретную инструкцию по торговле (например: "Купить YES на Polymarket (45¢), продать YES на Kalshi (62¢)").
   ВАЖНО: Если для Рынка B указан Стакан (Best Bid/Ask), учитывай, что покупать нужно по Best Ask, а продавать по Best Bid. Если спред в стакане съедает выгоду, арбитража нет.

Учитывай комиссии: Polymarket берёт ~2%, Kalshi ~7% от объёма.
Минимальный порог прибыльности после комиссий: ~5% от вложений."""

        schema = {
            "type": "OBJECT",
            "properties": {
                "has_arbitrage":     {"type": "BOOLEAN"},
                "arbitrage_type":    {"type": "STRING"},
                "spread_percent":    {"type": "NUMBER", "description": "Спред в процентах (0-100)"},
                "reasoning":         {"type": "STRING"},
                "trade_instruction": {"type": "STRING"},
                **self._TRADE_SCHEMA_EXTRA,
            },
            "required": ["has_arbitrage", "arbitrage_type", "spread_percent",
                         "reasoning", "trade_instruction",
                         *self._TRADE_REQUIRED_EXTRA],
        }

        prompt += self._TRADE_PROMPT_SUFFIX

        result, _ = self._call_llm(prompt, schema, agent_name="ARBITRAGE")
        if not result:
            return None

        try:
            data = json.loads(extract_response_text(result).strip())
            
            # Гард: logical_contradiction без description — понижаем автоматически
            if data.get("arbitrage_type") == "logical_contradiction":
                desc_a_missing = not (hasattr(market_a, "description") and market_a.description)
                desc_b_missing = not (hasattr(market_b, "description") and market_b.description)
                if desc_a_missing or desc_b_missing:
                    print(
                        "[ARBITRAGE] logical_contradiction при отсутствующем description. "
                        "Автопонижение до statistical_pair_trade."
                    )
                    data["arbitrage_type"] = "statistical_pair_trade"
                    data["reasoning"] = (
                        "[AUTO-DOWNGRADE] Тип изменён с logical_contradiction на statistical_pair_trade: "
                        "описание одного из рынков недоступно, проверка оракулов невозможна.\n"
                    ) + data.get("reasoning", "")

            spread_val = float(data.get("spread_percent") or mf.spread_pct)

            return CrossArbitrageSignal(
                market_a_id=market_a.id,
                market_a_platform=market_a.platform,
                market_a_title=market_a.title,
                market_a_price=market_a.price,
                market_a_url=market_a.url,
                market_b_id=market_b.id,
                market_b_platform=market_b.platform,
                market_b_title=market_b.title,
                market_b_price=market_b.price,
                market_b_url=market_b.url,
                has_arbitrage=bool(data.get("has_arbitrage", False)),
                arbitrage_type=data.get("arbitrage_type", "none"),
                spread_percent=round(spread_val, 2),
                reasoning=data.get("reasoning", ""),
                trade_instruction=data.get("trade_instruction", ""),
                match_score=match_score,
                action_a=data.get("action_a", "SKIP"),
                action_b=data.get("action_b", "SKIP"),
                entry_price_a_cents=data.get("entry_price_a_cents"),
                entry_price_b_cents=data.get("entry_price_b_cents"),
                expected_pnl_pct=data.get("expected_pnl_pct"),
                risk_level=data.get("risk_level", "MEDIUM"),
            )
        except Exception as e:
            print(f"[ARBITRAGE] Ошибка парсинга (cross_platform): {e}")
            return None

    # ─── Вспомогательные методы ─────────────────────────────────────────────

    def _call_llm(self, prompt: str, schema: dict, agent_name: str = "ARBITRAGE"):
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": self.system_instruction}]},
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "temperature": 0.1,
            },
        }
        return generate_content_with_fallback(
            api_key=self.api_key,
            payload=payload,
            default_model=self.model,
            agent_name=agent_name,
        )
