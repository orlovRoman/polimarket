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

    def estimate_market(self, market: Market) -> Optional[Signal]:
        """
        Оценивает рынок на потенциал хайпа.
        В отличие от SCOUT, получает текущую цену для принятия решения о покупке.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        try:
            from agents.shared.utils.rag import get_rag_context
            rag_context = get_rag_context(market.title, market.description)
        except Exception as e:
            print(f"[SWING] Ошибка загрузки RAG-памяти: {e}")
            rag_context = "В базе знаний Obsidian нет релевантных записей для этого рынка.\n"

        try:
            print(f"  SWING ищет новости (RSS + Reddit) для оценки хайпа: {market.title}...")
        except UnicodeEncodeError:
            print(f"  SWING ищет новости (RSS + Reddit) для оценки хайпа: {market.title.encode('ascii', 'replace').decode('ascii')}...")
        news_titles = fetch_rss_news(market.title)
        reddit_posts = fetch_reddit_news(market.title)

        prompt = f"""
Сегодняшняя дата и время: {now_str}
Рынок: {market.title}
Описание: {market.description}
Исход: {market.outcome}
Текущая цена исхода: {market.price}
Дата закрытия рынка: {market.close_time.strftime("%Y-%m-%d %H:%M:%S")}

{rag_context}

Последние заголовки RSS (для справки):
{chr(10).join(news_titles) if news_titles else "RSS новостей не найдено."}

Последние посты с Reddit (для справки):
{chr(10).join(reddit_posts) if reddit_posts else "Постов на Reddit не найдено."}

Используй инструмент google_search, чтобы узнать актуальную тональность и последние новости в сети.
Затем выполни анализ хайп-потенциала согласно своим инструкциям.
Ответ верни строго в формате JSON: {{"recommendation": "buy", "hype_potential": 0.8, "target_exit_price": 0.5, "confidence": 0.7, "reasoning": "..."}}
"""
        
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": prompt}]}
            ],
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
                target_price = analysis.get("target_exit_price", market.price + 0.10)
                edge_proxy = target_price - market.price # Имитация edge для совместимости
                
                signal = Signal(
                    id=f"sig-swing-{market.id}-{int(datetime.now().timestamp())}",
                    type="hype_pump",
                    market_id=market.id,
                    platform=market.platform,
                    edge=edge_proxy,
                    confidence=analysis.get("confidence", 0.5),
                    priority="high" if hype_potential > 0.8 else "medium",
                    summary=f"🚀 Ожидание пампа (Хайп {hype_potential*100:.0f}%): {market.title}",
                    details=f"Вход по {market.price}, выход по {target_price}.\nОбоснование: {analysis.get('reasoning', '')}"
                )
                return signal
        except Exception as e:
            print(f"Ошибка при оценке рынка {market.id} агентом SWING: {e}")
            
        return None
