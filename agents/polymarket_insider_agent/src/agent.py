import os
import requests
import json
from datetime import datetime
from typing import Optional, List
from agents.shared.python.models import Market, AgentOpinion
from agents.shared.python.db import get_connection

class ShadowAgent:
    def __init__(self, api_key: str, model: str = "gemini-2.5-pro"):
        self.api_key = api_key
        self.model = model
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        
        # Инструкции для SHADOW
        self.system_instruction = """
# SHADOW — Агент мониторинга инсайдеров и объемов

## Роль
Ты — эксперт по анализу On-chain данных и рыночных аномалий. Твоя задача — подтверждать или опровергать идеи SCOUT, основываясь на активности крупных игроков (китов) и объемах торгов.

## Функции
1. **Анализ объемов**: Если на рынке низкая ликвидность, но внезапно зашел крупный объем — это признак инсайда.
2. **Верификация**: Проверь, не является ли движение цены манипуляцией (Pump & Dump).

## Формат ответа (JSON)
{
  "agree": bool,
  "confidence": float,
  "opinion": "Твое краткое обоснование: видишь ли ты аномальные объемы или активность инсайдеров."
}
"""

    def analyze_idea(self, market: Market, scout_opinion: str) -> Optional[AgentOpinion]:
        """SHADOW анализирует идею, найденную SCOUT"""
        
        # В реальной версии здесь будет вызов API Polymarket для получения истории торгов/ордербука.
        # Для MVP мы просим LLM оценить риск на основе описания и 'шума' вокруг темы.
        
        prompt = f"""
Рынок: {market.title}
Текущая цена: {market.price}
Идея SCOUT: {scout_opinion}

Проанализируй этот рынок на предмет инсайдерских рисков и аномалий.
"""
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": self.system_instruction + "\n\n" + prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"}
        }
        
        try:
            response = requests.post(self.api_url, json=payload, timeout=30)
            if response.status_code == 200:
                result = response.json()
                analysis = json.loads(result['candidates'][0]['content']['parts'][0]['text'])
                
                return AgentOpinion(
                    agent_name="SHADOW",
                    market_id=market.id,
                    opinion=analysis.get("opinion", ""),
                    confidence=analysis.get("confidence", 0.5),
                    agree=analysis.get("agree", False)
                )
        except Exception as e:
            print(f"Ошибка SHADOW при анализе {market.id}: {e}")
        return None

def save_opinion(opinion: AgentOpinion):
    from agents.shared.python.db import get_connection
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO agent_opinions (agent_name, market_id, opinion, confidence, agree)
            VALUES (?, ?, ?, ?, ?)
        """, (opinion.agent_name, opinion.market_id, opinion.opinion, opinion.confidence, opinion.agree))
        conn.commit()
