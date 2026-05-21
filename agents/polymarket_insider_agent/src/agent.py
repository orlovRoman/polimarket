import os
import requests
import json
from datetime import datetime
from typing import Optional, List
from agents.shared.python.models import Market, AgentOpinion
from agents.shared.python.db import get_connection

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
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        
        # Загружаем детальные системные инструкции из файла конфигурации агента
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base_path, "GEMINI.md"), "r", encoding="utf-8") as f:
            self.system_instruction = f.read()

    def analyze_idea(self, market: Market, scout_opinion: str, orderbook: dict = None, price_history: list = None) -> Optional[AgentOpinion]:
        """
        Проводит анализ торговой идеи на предмет ликвидности и аномалий.
        
        :param market: Данные о рынке
        :param scout_opinion: Гипотеза от агента SCOUT
        :param orderbook: Данные ордербука от CLOB API (bid/ask depth, spread)
        :param price_history: История цен [{price, recorded_at}, ...]
        :return: Мнение агента (AgentOpinion) или None
        """
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
        
        prompt = f"""
Сегодняшняя дата и время: {now_str}
Рынок: {market.title}
Текущая цена: {market.price}
Идея SCOUT: {scout_opinion}

{orderbook_str}

{price_history_str}

Проанализируй этот рынок на предмет рисков ликвидности и аномалий.
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
