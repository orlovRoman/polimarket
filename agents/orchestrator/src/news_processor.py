import os
import json
from typing import Optional, List
from agents.shared.utils.gemini_client import generate_content_with_fallback, extract_response_text
from agents.shared.adapters.polymarket import PolymarketAdapter
from core.models import Market

class NewsProcessor:
    """
    Модуль для обработки сырых текстовых новостей (например, из Telegram-каналов)
    с помощью NEXUS (gemini) и поиска релевантных рынков на Polymarket.
    """
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model
        self.adapter = PolymarketAdapter()

    def find_relevant_markets(self, text: str) -> List[Market]:
        """
        Анализирует текст новости, извлекает ключевые слова и ищет подходящие рынки.
        Возвращает список подходящих объектов Market.
        """
        prompt = f"""
Ты — финансовый аналитик. Прочитай следующий пост из новостного канала и выдели 1-3 самых релевантных ключевых слова или коротких фраз на английском языке, по которым можно найти связанные рынки предсказаний (на платформе Polymarket).

Текст поста:
"{text}"

Твоя задача — вернуть ТОЛЬКО JSON с массивом строк "keywords". 
Если пост — просто спам или не содержит конкретной фактуры для ставок, верни пустой массив.

Пример:
Текст: "США планируют ввести санкции против TSMC к концу 2026 года."
Ответ: {{"keywords": ["TSMC", "US sanctions"]}}
"""

        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": prompt}]}
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
            }
        }
        
        result, _ = generate_content_with_fallback(
            api_key=self.api_key,
            payload=payload,
            default_model=self.model,
            agent_name="NEXUS_NEWS"
        )
        
        if not result:
            print("[NewsProcessor] Ошибка LLM при извлечении ключевых слов.")
            return []
            
        try:
            content = extract_response_text(result)
            data = json.loads(content)
            keywords = data.get("keywords", [])
            
            if not keywords:
                print("[NewsProcessor] Новость не содержит релевантных ключевых слов.")
                return []
                
            print(f"[NewsProcessor] Извлечены ключевые слова: {keywords}")
            
            all_found = []
            seen_ids = set()
            
            for kw in keywords:
                # Ищем по каждому ключевому слову
                markets = self.adapter.search_markets(kw, limit=3)
                for m in markets:
                    if m.id not in seen_ids:
                        seen_ids.add(m.id)
                        all_found.append(m)
                        
            return all_found
            
        except Exception as e:
            print(f"[NewsProcessor] Ошибка обработки ответа LLM: {e}")
            return []
