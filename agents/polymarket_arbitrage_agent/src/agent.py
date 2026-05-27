import os
import json
from core.models import Market, CrossArbitrageSignal
from agents.shared.utils.gemini_client import generate_content_with_fallback, extract_response_text


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

    # ─── Режим 1: Внутриплатформенный арбитраж (по корреляции) ──────────────

    def analyze_correlation(
        self,
        market_a: Market,
        market_b: Market,
        correlation_type: str,
        score: int,
    ) -> CrossArbitrageSignal | None:
        """Анализирует пару связанных рынков с одной платформы."""
        prompt = f"""Оцени следующую пару рынков на предмет кросс-рыночного арбитража.
Тип корреляции, обнаруженный системой: {correlation_type} (score: {score})

=== Рынок A ===
ID: {market_a.id}
Название: {market_a.title}
Описание: {market_a.description}
Цена YES: {market_a.price}

=== Рынок B ===
ID: {market_b.id}
Название: {market_b.title}
Описание: {market_b.description}
Цена YES: {market_b.price}

Есть ли здесь логическое или математическое противоречие (расхождение) в ценах?"""

        schema = {
            "type": "OBJECT",
            "properties": {
                "has_arbitrage":     {"type": "BOOLEAN"},
                "arbitrage_type":    {"type": "STRING"},
                "spread_percent":    {"type": "NUMBER"},
                "reasoning":         {"type": "STRING"},
                "trade_instruction": {"type": "STRING"},
            },
            "required": ["has_arbitrage", "arbitrage_type", "spread_percent",
                         "reasoning", "trade_instruction"],
        }

        result, _ = self._call_llm(prompt, schema, agent_name="ARBITRAGE")
        if not result:
            return None

        try:
            content = extract_response_text(result)
            content = content.replace("```json", "").replace("```", "").strip()
            data = json.loads(content, strict=False)

            # Normalize spread
            spread_val = float(data.get("spread_percent", 0.0))
            if 0 < spread_val < 1:
                spread_val *= 100
                
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
            )
        except Exception as e:
            print(f"[ARBITRAGE] Ошибка парсинга (correlation): {e}")
            return None

    # ─── Режим 2: Кросс-платформенный арбитраж ──────────────────────────────

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
        direct_spread = abs(market_a.price - market_b.price)
        spread_percent = round(direct_spread * 100, 2)
        
        # Интеграция стакана заявок (RISK-10)
        book_info = ""
        if orderbook_b:
            book_info = (f"\n  Стакан (Best Bid: {orderbook_b.get('top_bid', 'N/A')}, "
                         f"Best Ask: {orderbook_b.get('top_ask', 'N/A')})\n")

        # BUG-03: Убрано дублирующее "Отвечай строго JSON." 
        prompt = f"""Ты — аналитик кросс-платформенного арбитража рынков предсказаний.

Два рынка с РАЗНЫХ платформ, которые, по всей видимости, описывают ОДНО событие:

Рынок A ({market_a.platform.upper()}):
  Название: {market_a.title}
  Цена YES: {market_a.price:.3f} ({int(market_a.price * 100)}¢)
  URL: {market_a.url}
  Закрытие: {market_a.close_time.strftime('%Y-%m-%d')}

Рынок B ({market_b.platform.upper()}):
  Название: {market_b.title}
  Цена YES (mid-price): {market_b.price:.3f} ({int(market_b.price * 100)}¢){book_info}
  URL: {market_b.url}
  Закрытие: {market_b.close_time.strftime('%Y-%m-%d')}

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
            },
            "required": ["has_arbitrage", "arbitrage_type", "spread_percent",
                         "reasoning", "trade_instruction"],
        }

        result, _ = self._call_llm(prompt, schema, agent_name="ARBITRAGE")
        if not result:
            return None

        try:
            data = json.loads(extract_response_text(result).strip())
            
            # RISK-08: Нормализация spread_percent
            spread_val = float(data.get("spread_percent", spread_percent))
            if 0 < spread_val < 1.0: # Модель вернула долю вместо процентов
                spread_val *= 100
            elif spread_val == 0.0 and data.get("has_arbitrage"):
                spread_val = spread_percent # Модель вернула 0, используем расчетный

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
