import os
import json
from datetime import datetime
from typing import Optional
from core.models import Market, AgentOpinion
from core.context import MarketContext
from agents.shared.python.db import get_agent_episodes, get_performance_summary

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

    from agents.shared.python.llm_wrapper import with_retry
    @with_retry(max_attempts=3, initial_backoff=2.0)
    def analyze_idea(self, context: 'MarketContext', scout_opinion: str, orderbook: Optional[dict] = None, price_history: list = None) -> Optional[AgentOpinion]:
        """
        Анализирует идею (от SCOUT) с точки зрения ликвидности и активности трейдеров.
        """
        market = context.market
        smart_money = context.smart_money
        
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Форматируем данные ордербука
        orderbook_str = "Данные ордербука недоступны."
        if orderbook:
            orderbook_str = (
                f"=== ДАННЫЕ ОРДЕРБУКА (CLOB API) ===\n"
                f"Спред: {orderbook.get('spread', 'N/A')}\n"
                f"Top Bid: {orderbook.get('top_bid', 'N/A')} | Top Ask: {orderbook.get('top_ask', 'N/A')}\n"
                f"Глубина Bid (5 lvl): ${orderbook.get('bid_depth_5', 0):,.0f} | Ask: ${orderbook.get('ask_depth_5', 0):,.0f}\n"
                f"Всего уровней — Bids: {orderbook.get('total_bids', 0)} | Asks: {orderbook.get('total_asks', 0)}"
            )
            # Добавляем асимметрию
            bid_d = orderbook.get('bid_depth_5', 0)
            ask_d = orderbook.get('ask_depth_5', 0)
            if ask_d > 0:
                ratio = bid_d / ask_d
                orderbook_str += f"\nАсимметрия Bid/Ask: {ratio:.1f}x"
        
        # Форматируем историю цен
        price_history_str = "История цен недоступна."
        if price_history:
            lines = []
            for point in price_history[-6:]:  # Последние 6 точек
                lines.append(f"  {point['recorded_at']}: {point['price']:.4f}")
            if lines:
                price_history_str = "=== ИСТОРИЯ ЦЕНЫ ===\n" + "\n".join(lines)
                
        # ОНЧЕЙН АКТИВНОСТЬ (Smart Money)
        sm_block = "Ончейн данные недоступны."
        if smart_money and smart_money.available:
            sm_block = f"""
=== ОНЧЕЙН АКТИВНОСТЬ (Smart Money) ===
Всего объём YES: ${smart_money.total_yes_usd:,.0f}
Всего объём NO:  ${smart_money.total_no_usd:,.0f}
YES dominance:   {smart_money.yes_dominance:.0%}

Топ кошельки:
{smart_money.summary}
"""
        
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
            
        perf_summary = get_performance_summary("SHADOW", 10)

        prompt = f"""
Сегодняшняя дата и время: {now_str}
Рынок: {market.title}
Текущая цена: {market.price}
Идея SCOUT: {scout_opinion}

[Твоя производительность и работа над ошибками]
{perf_summary}

{rag_context}

{orderbook_str}

{price_history_str}

{sm_block}

Твоя задача — проверить эту торговую идею:
1. Хватает ли ликвидности для безопасного входа и выхода? (Смотри ордербук и спред).
2. Поддерживают ли ставку крупные игроки (Smart Money)?
3. Согласен ли ты с идеей SCOUT?

Оценивай строго, но аргументированно.
ПИШИ ИСКЛЮЧИТЕЛЬНО НА РУССКОМ ЯЗЫКЕ! Запрещено использовать другие языки (китайский, французский и т.д.).
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
        
        analysis = None
        for attempt in range(2):
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
                break
            except json.JSONDecodeError as e:
                print(f"[SHADOW] Ошибка парсинга JSON (попытка {attempt+1}): {e}")
                analysis = None
        
        if not analysis:
            return None
            
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


