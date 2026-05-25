import os
import json
from datetime import datetime
from typing import Optional
from agents.shared.python.models import Market, Signal
from agents.shared.python.db import get_memory
from agents.shared.utils.web_search import fetch_rss_news, fetch_reddit_news

class SwingAgent:
    """
    Агент SWING_TRADER — спекулянт, ищущий хайп-потенциал на сильно перекошенных рынках.
    Работает с "дешевыми" исходами и оценивает вероятность пампа на новостях.
    """
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model
        
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base_path, "GEMINI.md"), "r", encoding="utf-8") as f:
            self.system_instruction = f.read()

    def estimate_market(self, market: Market, news_titles: list = None, reddit_posts: list = None, price_history: list = None) -> Optional[Signal]:
        """
        Оценивает рынок на потенциал хайпа.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        try:
            from agents.shared.utils.rag import get_rag_context
            rag_context = get_rag_context(market.title, market.description)
        except Exception as e:
            print(f"[SWING] Ошибка загрузки RAG-памяти: {e}")
            rag_context = "В базе знаний Obsidian нет релевантных записей для этого рынка.\n"

        price_history_str = "История цен недоступна."
        if price_history:
            lines = [f"  {p['recorded_at']}: {p['price']:.4f}" for p in price_history[-6:]]
            if lines:
                price_history_str = "=== ИСТОРИЯ ЦЕНЫ ===\n" + "\n".join(lines)

        prompt = f"""
Сегодняшняя дата и время: {now_str}
Рынок: {market.title}
Описание: {market.description}
Исход: {market.outcome}
Текущая цена исхода (YES): {market.price}
Дата закрытия рынка: {market.close_time.strftime("%Y-%m-%d %H:%M:%S")}

{rag_context}

{price_history_str}

Последние заголовки RSS:
{chr(10).join(news_titles) if news_titles else "Нет свежих новостей."}

Последние посты с Reddit:
{chr(10).join(reddit_posts) if reddit_posts else "Нет постов на Reddit."}

Используй инструмент google_search, чтобы узнать актуальную тональность.
Затем выполни анализ хайп-потенциала.
Ответ верни строго в формате JSON.
"""
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": self.system_instruction}]},
            "tools": [{"google_search": {}}],
        }
        
        from agents.shared.utils.gemini_client import generate_content_with_fallback
        result, active_model = generate_content_with_fallback(
            api_key=self.api_key,
            payload=payload,
            default_model=self.model,
            agent_name="SWING"
        )
        
        if not result:
            return None
            
        try:
            content = result['candidates'][0]['content']['parts'][0]['text']
            
            import re
            json_match = re.search(r'\{[^{}]*"hype_potential"[^{}]*\}', content, re.DOTALL)
            if json_match:
                analysis = json.loads(json_match.group(), strict=False)
            else:
                print(f"[SWING] Не удалось распарсить JSON из ответа: {content[:100]}")
                return None
            
            recommendation = analysis.get("recommendation", "ignore").lower()
            hype_potential = float(analysis.get("hype_potential", 0))
            
            if recommendation == "buy" and hype_potential > 0.6:
                target_outcome = analysis.get("target_outcome", "YES")
                target_price = analysis.get("target_exit_price", 0.15)
                
                # Расчет ROI (Return on Investment)
                current_price = market.price if target_outcome == "YES" else (1.0 - market.price)
                if current_price <= 0: current_price = 0.01
                roi = ((target_price - current_price) / current_price) * 100
                
                signal = Signal(
                    id=f"sig-swing-{market.id}-{int(datetime.now().timestamp())}",
                    type="hype_pump",
                    market_id=market.id,
                    platform=market.platform,
                    edge=roi / 100.0,  # Записываем ROI вместо Edge для совместимости
                    confidence=analysis.get("confidence", 0.5),
                    priority="high" if hype_potential > 0.8 else "medium",
                    summary=f"🚀 Памп {target_outcome} (Хайп {hype_potential*100:.0f}%, Цель {target_price:.2f})",
                    details=f"Рекомендация: BUY {target_outcome} по ~{current_price:.2f}, выход по {target_price:.2f} (ROI ~{roi:.0f}%).\nОбоснование: {analysis.get('reasoning', '')}"
                )
                return signal
        except Exception as e:
            print(f"Ошибка при оценке рынка {market.id} агентом SWING: {e}")
            
        return None
