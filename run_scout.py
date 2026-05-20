import os
import sys
from dotenv import load_dotenv

# Добавляем корень проекта в sys.path
sys.path.append(os.getcwd())

from agents.shared.adapters.polymarket import PolymarketAdapter
from agents.shared.python.db import save_market, init_db
from agents.polymarket_mispricing_agent.src.agent import ScoutAgent

def main():
    load_dotenv()
    init_db()
    
    # 1. Сначала обновляем рынки
    adapter = PolymarketAdapter()
    print("--- Шаг 1: Получение свежих рынков с Polymarket ---")
    markets = adapter.list_markets(limit=10)
    for m in markets:
        save_market(m)
    print(f"Обновлено рынков: {len(markets)}")
    
    # 2. Запускаем SCOUT
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        print("Ошибка: GOOGLE_API_KEY не найден!")
        return
        
    print("\n--- Шаг 2: Анализ рынков агентом SCOUT ---")
    # Переименуем папку для корректного импорта (уберем тире)
    # Или используем прямой импорт если структура позволяет
    from agents.polymarket_mispricing_agent.src.agent import ScoutAgent
    scout = ScoutAgent(api_key=key)
    scout.run_scan(limit=10)

if __name__ == "__main__":
    main()
