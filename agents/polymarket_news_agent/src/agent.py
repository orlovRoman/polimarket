import os
import requests
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import quote
from typing import Optional
from agents.shared.python.models import Market, AgentOpinion

class HeraldAgent:
    """
    Агент HERALD — новостной аналитик и фактчекер.
    Его основная задача — поиск подтверждений торговых идей в актуальных новостях, 
    а также выявление ситуаций арбитража, когда событие уже завершилось, 
    но цена на рынке еще не отыграла результат.
    """
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        """
        Инициализация агента HERALD.
        """
        self.api_key = api_key
        self.model = model
        
        # Загружаем детальные системные инструкции из файла конфигурации агента
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base_path, "GEMINI.md"), "r", encoding="utf-8") as f:
            self.system_instruction = f.read()

    def fetch_rss_news(self, query: str) -> list[str]:
        """
        Получает последние новости через Google News RSS для первичного ознакомления.
        
        :param query: Поисковый запрос (обычно название рынка)
        :return: Список заголовков новостей
        """
        try:
            url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
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
        """
        Анализирует идею, используя поиск в реальном времени и RSS-ленты.
        
        :param market: Объект анализируемого рынка
        :param scout_opinion: Гипотеза от SCOUT
        :return: Мнение агента (AgentOpinion) с учетом новостного фона
        """
        
        print(f"  HERALD ищет новости и проверяет статус по запросу: {market.title}...")
        news_titles = self.fetch_rss_news(market.title)
        
        # Загружаем RAG-память из Obsidian
        try:
            from agents.shared.utils.rag import get_rag_context
            rag_context = get_rag_context(market.title, market.description)
        except Exception as e:
            print(f"[HERALD] Ошибка загрузки RAG-памяти: {e}")
            rag_context = "В базе знаний Obsidian нет релевантных записей для этого рынка.\n"

        prompt = f"""
Сегодняшняя дата: {datetime.now().strftime('%Y-%m-%d')}
Рынок: {market.title}
Описание: {market.description}
Текущая цена: {market.price}
Идея SCOUT: {scout_opinion}

{rag_context}

Последние заголовки RSS (для справки):
{chr(10).join(news_titles) if news_titles else "RSS новостей не найдено."}

Задание:
1. Используй Google Search, чтобы узнать текущий статус этого события. Оно уже завершилось?
2. Если событие ЗАВЕРШИЛОСЬ и результат известен, сравни его с условиями рынка. 
3. Если результат уже есть, а цена на рынке не 1.0 (или не 0.0), отметь это как `is_arbitrage: true`.
"""
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": self.system_instruction}]},
            "tools": [{"google_search": {}}],
            # НЕ совмещаем google_search с response_mime_type: application/json (API 400)
        }
        
        from agents.shared.utils.gemini_client import generate_content_with_fallback
        result, active_model = generate_content_with_fallback(
            api_key=self.api_key,
            payload=payload,
            default_model=self.model,
            agent_name="HERALD"
        )
        
        if not result:
            return None
            
        try:
            raw_text = result['candidates'][0]['content']['parts'][0]['text']
            
            # Пытаемся извлечь JSON из ответа (может быть обёрнут в markdown)
            import re
            json_match = re.search(r'\{[^{}]*"agree"[^{}]*\}', raw_text, re.DOTALL)
            if json_match:
                analysis = json.loads(json_match.group())
            else:
                # Fallback: LLM не вернул JSON — используем текст как мнение
                analysis = {"agree": False, "confidence": 0.3, "opinion": raw_text[:300]}
            
            opinion = AgentOpinion(
                agent_name="HERALD",
                market_id=market.id,
                opinion=analysis.get("opinion", ""),
                confidence=float(analysis.get("confidence", 0.5)),
                agree=analysis.get("agree", False)
            )
            
            # Специальная обработка для ситуаций арбитража
            if analysis.get("is_arbitrage"):
                opinion.opinion = "🚨 [АРБИТРАЖ] " + opinion.opinion
                opinion.confidence = 1.0
                opinion.agree = True
            
            return opinion
        except Exception as e:
            print(f"Ошибка HERALD при анализе {market.id}: {e}")
        return None
