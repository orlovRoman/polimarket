import os
import requests
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional, List
from agents.shared.python.models import Market, AgentOpinion
from agents.shared.python.db import get_connection

class HeraldAgent:
    def __init__(self, api_key: str, model: str = "gemini-2.5-pro"):
        self.api_key = api_key
        self.model = model
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        
        self.system_instruction = """
# HERALD — Агент новостей и контекста

## Роль
Ты — экспертный новостной аналитик. Твоя задача — найти подтверждение или опровержение торговой идее в последних новостях и мировых событиях.

## Функции
1. **Анализ новостного фона**: Как последние новости влияют на вероятность исхода?
2. **Оценка хайпа**: Является ли движение цены результатом реальных новостей или это просто шум в соцсетях?

## Формат ответа (JSON)
{
  "agree": bool,
  "confidence": float,
  "opinion": "Твой краткий анализ новостей по теме рынка.",
  "news_sources": ["список ключевых заголовков"]
}
"""

    def fetch_rss_news(self, query: str) -> List[str]:
        """Получает новости через Google News RSS (без ключа)"""
        try:
            url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                return []
            
            root = ET.fromstring(response.content)
            news = []
            for item in root.findall(".//item")[:5]:
                title = item.find("title").text
                news.append(title)
            return news
        except Exception as e:
            print(f"Ошибка при получении RSS: {e}")
            return []

    def analyze_idea(self, market: Market, scout_opinion: str) -> Optional[AgentOpinion]:
        """HERALD ищет новости и анализирует идею"""
        
        print(f"  HERALD ищет новости по запросу: {market.title}...")
        news_titles = self.fetch_rss_news(market.title)
        
        prompt = f"""
Рынок: {market.title}
Описание: {market.description}
Идея SCOUT: {scout_opinion}

Последние заголовки новостей:
{chr(10).join(news_titles) if news_titles else "Новостей не найдено."}

Дай оценку этой идеи на основе новостного контекста.
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
                    agent_name="HERALD",
                    market_id=market.id,
                    opinion=analysis.get("opinion", ""),
                    confidence=analysis.get("confidence", 0.5),
                    agree=analysis.get("agree", False)
                )
        except Exception as e:
            print(f"Ошибка HERALD при анализе {market.id}: {e}")
        return None
