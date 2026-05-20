import os
import requests
import json
from datetime import datetime
from typing import Optional, List
from agents.shared.python.models import Market, AgentOpinion
from agents.shared.python.db import get_connection

class ShadowAgent:
    """
    Агент SHADOW — эксперт по анализу On-chain данных и рыночных аномалий.
    Его задача — верифицировать идеи SCOUT, основываясь на объемах торгов 
    и возможной активности крупных игроков (инсайдеров).
    """
    def __init__(self, api_key: str, model: str = "gemini-2.5-pro"):
        """
        Инициализация агента SHADOW.
        """
        self.api_key = api_key
        self.model = model
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        
        # Системные инструкции, определяющие логику поведения агента
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
        """
        Проводит анализ торговой идеи на предмет аномалий.
        
        :param market: Данные о рынке
        :param scout_opinion: Гипотеза от агента SCOUT
        :return: Мнение агента (AgentOpinion) или None
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Формируем контекст для модели. В MVP LLM анализирует описание и общий фон.
        prompt = f"""
Сегодняшняя дата и время: {now_str}
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
    """
    Сохраняет вынесенное мнение эксперта в БД SQLite.
    """
    from agents.shared.python.db import get_connection
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO agent_opinions (agent_name, market_id, opinion, confidence, agree)
            VALUES (?, ?, ?, ?, ?)
        """, (opinion.agent_name, opinion.market_id, opinion.opinion, opinion.confidence, opinion.agree))
        conn.commit()
