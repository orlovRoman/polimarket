import os
import json
from datetime import datetime
from typing import Optional
from core.models import Market, AgentOpinion
from core.context import MarketContext
from agents.shared.python.db import get_agent_episodes, get_performance_summary
from agents.shared.python.llm_wrapper import with_retry

class ShadowAgent:
    """
    Агент SHADOW — эксперт по анализу ликвидности, ордербука и объёмов.
    Его задача — верифицировать идеи SCOUT, основываясь на реальных данных
    ордербука (CLOB API) и истории цен.
    """
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        """
        Инициализация агента SHADOW.
        """
        self.api_key = api_key
        self.model = model
        
        # Загружаем детальные системные инструкции из файла конфигурации агента
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base_path, "GEMINI.md"), "r", encoding="utf-8") as f:
            self.system_instruction = f.read()

    @with_retry(max_attempts=3, initial_backoff=2.0)
    def analyze_idea(self, context: 'MarketContext', scout_opinion: str, orderbook: Optional[dict] = None, price_history: list = None) -> Optional[AgentOpinion]:
        """
        Анализирует идею (от SCOUT) с точки зрения ликвидности и активности трейдеров.
        """
        market = context.market
        smart_money = context.smart_money
        
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        from agents.shared.utils.prompt_guards import guard_orderbook, guard_smart_money
        orderbook_str = guard_orderbook(orderbook)
        
        # Форматируем историю цен
        price_history_str = "История цен недоступна."
        if price_history:
            lines = []
            for point in price_history[-6:]:  # Последние 6 точек
                lines.append(f"  {point['recorded_at']}: {point['price']:.4f}")
            if lines:
                price_history_str = "=== ИСТОРИЯ ЦЕНЫ ===\n" + "\n".join(lines)
                
        target_outcome = "NO" if "NO" in scout_opinion else "YES"
        sm_block = guard_smart_money(smart_money, target_outcome)
        
        # Загружаем RAG-память из Obsidian
        try:
            from agents.shared.utils.rag import get_rag_context
            rag_context = get_rag_context(market.title, market.description)
        except Exception as e:
            print(f"[SHADOW] Ошибка загрузки RAG-памяти: {e}")
            rag_context = "В базе знаний Obsidian нет релевантных записей для этого рынка.\n"

        # Загружаем эпизодическую память (последние оценки)
        episodes = get_agent_episodes("SHADOW", event_type="signal_evaluated", limit=3)
        episodes_text = "Нет недавних оценок."
        if episodes:
            episodes_text = "\n".join([f"- {ep['summary']}" for ep in episodes])
            
        perf_summary = get_performance_summary("SHADOW", 10) or "История оценок пуста — первые прогнозы."

        orderbook_unavailable_note = (
            "\n⛔ ОРДЕРБУК ПУСТОЙ: confidence ОБЯЗАН быть ≤ 0.30. "
            "agree=true ТОЛЬКО если пустой стакан не означает отсутствие $10 ликвидности.\n"
        ) if not orderbook else ""

        prompt = f"""
Сегодняшняя дата и время: {now_str}
Рынок: {market.title}
Текущая цена: {market.price}
Идея SCOUT: {scout_opinion}

[Твоя производительность и работа над ошибками]
{perf_summary}

{rag_context}

{orderbook_str}
{orderbook_unavailable_note}
{price_history_str}

{sm_block}

Твоя задача — проверить эту торговую идею:
1. Хватает ли ликвидности для безопасного входа и выхода? (Смотри ордербук и спред).
2. Поддерживают ли ставку крупные игроки (Smart Money)?
3. Согласен ли ты с идеей SCOUT?

Оценивай строго, но аргументированно.
ПИШИ ИСКЛЮЧИТЕЛЬНО НА РУССКОМ ЯЗЫКЕ! Запрещено использовать другие языки (китайский, французский и т.д.).
Ограничения на английские слова: если существует синоним на русском языке, запрещено использовать английские слова и фразы (например, не пиши 'Estimate probability', 'current price', пиши по-русски 'оценочная вероятность', 'текущая цена').
"""
        
        schema = {
            "type": "OBJECT",
            "properties": {
                "opinion": {"type": "STRING"},
                "confidence": {"type": "NUMBER"},
                "agree": {"type": "BOOLEAN"},
                "orderbook_facts": {"type": "STRING"},
                "risk_assessment": {"type": "STRING"},
                "shadow_verdict": {"type": "STRING"},
                "liquidity_risk": {"type": "STRING"}
            },
            "required": ["opinion", "confidence", "agree", "orderbook_facts", "risk_assessment", "shadow_verdict", "liquidity_risk"]
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
        
        from agents.shared.utils.language_guard import validate_russian_fields
        TEXT_FIELDS = ["opinion", "orderbook_facts", "risk_assessment", "shadow_verdict", "liquidity_risk"]
        
        analysis = None
        for attempt in range(1):
            result, active_model = generate_content_with_fallback(
                api_key=self.api_key,
                payload=payload,
                default_model=self.model,
                agent_name="SHADOW",
                market_id=market.id
            )
            
            if not result:
                continue
                
            try:
                content = extract_response_text(result)
                content = content.replace("```json", "").replace("```", "").strip()
                analysis = json.loads(content, strict=False)
                
                # FIX #1: проверяем язык — если нарушение, повторяем запрос
                bad_field = validate_russian_fields(analysis, TEXT_FIELDS)
                if bad_field:
                    print(f"[SHADOW] Попытка {attempt+1}: поле '{bad_field}' содержит запрещённые символы, повторяем запрос...")
                    analysis = None
                    continue
                
                break
            except json.JSONDecodeError as e:
                print(f"[SHADOW] Ошибка парсинга JSON (попытка {attempt+1}): {e}")
                analysis = None
        
        if not analysis:
            return None

        # === POST-VALIDATION GUARDS ===
        import logging
        logger = logging.getLogger("shadow_agent")

        # Гард 1: ордербук недоступен → confidence не может быть > 0.40
        if not orderbook:
            declared = float(analysis.get("confidence", 0.5))
            if declared > 0.40:
                logger.warning(
                    f"[SHADOW] Ордербук недоступен, но confidence={declared:.2f}. "
                    f"Принудительно снижаем до 0.30."
                )
                analysis["confidence"] = 0.30
                analysis["liquidity_risk"] = "medium"

        # Гард 2: нет данных Smart Money → убрать упоминание из текстов
        if not (smart_money and getattr(smart_money, "available", False)):
            hallucination_phrases = [
                "smart money подтверждают",
                "киты подтверждают",
                "крупные трейдеры подтверждают",
                "институциональные покупки",
            ]
            for field in ["opinion", "risk_assessment", "shadow_verdict"]:
                text = analysis.get(field, "")
                if any(p in text.lower() for p in hallucination_phrases):
                    logger.warning(f"[SHADOW] Поле '{field}' содержит упоминание Smart Money без данных.")
                    analysis[field] = text + " [⚠️ данные по крупным трейдерам недоступны]"
            
        try:
            op_text = analysis.get("opinion", "").strip()
            verdict = analysis.get("shadow_verdict", "").strip()
            if not op_text:
                op_text = verdict
            elif verdict and verdict not in op_text:
                op_text += f"\nВердикт: {verdict}"
            
            return AgentOpinion(
                agent_name="SHADOW",
                market_id=market.id,
                opinion=op_text,
                confidence=float(analysis.get("confidence", 0.5)),
                agree=analysis.get("agree", False),
                # Новые поля
                orderbook_facts=analysis.get("orderbook_facts", ""),
                risk_assessment=analysis.get("risk_assessment", ""),
                shadow_verdict=verdict,
                liquidity_risk=analysis.get("liquidity_risk", "medium")
            )
        except Exception as e:
            print(f"Ошибка SHADOW при анализе {market.id}: {e}")
        return None


