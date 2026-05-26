import os
import json
from dataclasses import dataclass
from core.models import Market

@dataclass
class ArbitrageSignal:
    has_arbitrage: bool
    arbitrage_type: str
    spread_percent: float
    reasoning: str
    trade_instruction: str

class ArbitrageAgent:
    """
    Агент ARBITRAGE — ищет математические и логические противоречия между рынками.
    """
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model
        
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base_path, "GEMINI.md"), "r", encoding="utf-8") as f:
            self.system_instruction = f.read()

    def analyze_correlation(self, market_a: Market, market_b: Market, correlation_type: str, score: int) -> ArbitrageSignal:
        """
        Анализирует пару связанных рынков на наличие арбитража.
        """
        prompt = f"""
Оцени следующую пару рынков на предмет кросс-рыночного арбитража.
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

Есть ли здесь логическое или математическое противоречие (расхождение) в ценах?
"""
        
        schema = {
            "type": "OBJECT",
            "properties": {
                "has_arbitrage": {"type": "BOOLEAN"},
                "arbitrage_type": {"type": "STRING"},
                "spread_percent": {"type": "NUMBER"},
                "reasoning": {"type": "STRING"},
                "trade_instruction": {"type": "STRING"}
            },
            "required": ["has_arbitrage", "arbitrage_type", "spread_percent", "reasoning", "trade_instruction"]
        }

        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": self.system_instruction}]},
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema
            }
        }
        
        from agents.shared.utils.gemini_client import generate_content_with_fallback, extract_response_text
        
        result, active_model = generate_content_with_fallback(
            api_key=self.api_key,
            payload=payload,
            default_model=self.model,
            agent_name="ARBITRAGE"
        )
        
        if not result:
            return None
            
        try:
            content = extract_response_text(result)
            content = content.replace("```json", "").replace("```", "").strip()
            data = json.loads(content, strict=False)
            
            return ArbitrageSignal(
                has_arbitrage=data.get("has_arbitrage", False),
                arbitrage_type=data.get("arbitrage_type", "none"),
                spread_percent=float(data.get("spread_percent", 0.0)),
                reasoning=data.get("reasoning", ""),
                trade_instruction=data.get("trade_instruction", "")
            )
        except Exception as e:
            print(f"[ARBITRAGE] Ошибка парсинга: {e}")
            return None
