import os
import requests
import json
from datetime import datetime
from typing import Optional, List
from agents.shared.python.models import Market, Signal
from agents.shared.python.db import save_signal, get_connection

class ScoutAgent:
    def __init__(self, api_key: str, model: str = "gemini-2.5-pro"):
        self.api_key = api_key
        self.model = model
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        
        # Путь к инструкциям
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base_path, "GEMINI.md"), "r") as f:
            self.system_instruction = f.read()

    def estimate_market(self, market: Market) -> Optional[Signal]:
        prompt = f"""
Рынок: {market.title}
Описание: {market.description}
Исход: {market.outcome}
Текущая цена: {market.price} (вероятность {market.price * 100}%)

Выполни анализ согласно своим инструкциям.
"""
        
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": self.system_instruction + "\n\n" + prompt}]}
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
            }
        }
        
        try:
            response = requests.post(self.api_url, json=payload, timeout=30)
            if response.status_code != 200:
                print(f"Ошибка Gemini API: {response.text}")
                return None
                
            result = response.json()
            if 'candidates' not in result or not result['candidates']:
                print(f"Gemini не вернул кандидатов: {result}")
                return None

            content = result['candidates'][0]['content']['parts'][0]['text']
            analysis = json.loads(content)
            
            # Проверяем edge
            est_prob = analysis.get("estimate_probability", 0)
            edge = est_prob - market.price
            
            # Порог из REQUIREMENTS.md: edge > 0.10
            if edge > 0.10:
                signal = Signal(
                    id=f"sig-{market.id}-{int(datetime.now().timestamp())}",
                    type="undervaluation",
                    market_id=market.id,
                    platform=market.platform,
                    edge=edge,
                    confidence=analysis.get("confidence", 0.5),
                    priority=analysis.get("priority", "medium"),
                    summary=f"Недооценка на {edge*100:.1f}%: {market.title}",
                    details=analysis.get("reasoning", "")
                )
                return signal
        except Exception as e:
            print(f"Ошибка при оценке рынка {market.id}: {e}")
            
        return None

    def run_scan(self, limit: int = 10):
        """Сканирует рынки из БД и ищет недооцененные"""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM markets ORDER BY updated_at DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        
        print(f"Запуск сканирования {len(rows)} рынков...")
        
        for row in rows:
            market = Market(
                id=row['id'],
                platform=row['platform'],
                title=row['title'],
                description=row['description'],
                url=row['url'],
                outcome=row['outcome'],
                price=row['price'],
                close_time=datetime.fromisoformat(row['close_time'])
            )
            
            print(f"Анализируем: {market.title} (Цена: {market.price})...")
            signal = self.estimate_market(market)
            if signal:
                print(f"!!! НАЙДЕН СИГНАЛ: {signal.summary} (Edge: {signal.edge:.2f}, Conf: {signal.confidence})")
                save_signal(signal)
            else:
                print("--- Сигнал не найден.")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        print("GOOGLE_API_KEY не найден в .env")
    else:
        scout = ScoutAgent(api_key=key)
        scout.run_scan()
