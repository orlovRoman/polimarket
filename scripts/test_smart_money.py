import sys
import os
import argparse
sys.path.append(os.getcwd())

from services.onchain_provider import get_recent_trades, get_top_positions
from core.smart_money import analyze_smart_money
from agents.shared.adapters.polymarket import PolymarketAdapter

def main(condition_id: str):
    print(f"Тестирование ончейн-аналитики для Condition ID: {condition_id}\n")
    
    print("1. Получение сделок из CLOB API...")
    trades = get_recent_trades(condition_id, limit=50)
    print(f"   Найдено сделок: {len(trades)}")
    
    print("2. Получение позиций из Gamma API...")
    positions = get_top_positions(condition_id)
    print(f"   Найдено позиций: {len(positions)}")
    
    print("\n3. Анализ Smart Money...")
    result = analyze_smart_money(trades, positions)
    
    if not result.available:
        print("   Ончейн данные недоступны.")
        return
        
    print(f"   Total YES USD: ${result.total_yes_usd:,.0f}")
    print(f"   Total NO USD:  ${result.total_no_usd:,.0f}")
    print(f"   YES Dominance: {result.yes_dominance*100:.1f}%")
    
    print("\n=== Топ Кошельки ===")
    print(result.summary)
    print("\nТест успешно завершён!")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Тест Smart Money")
    parser.add_argument("--condition", type=str, help="Condition ID рынка", default=None)
    args = parser.parse_args()
    
    if args.condition:
        main(args.condition)
    else:
        print("Получение случайного активного рынка...")
        adapter = PolymarketAdapter()
        markets = adapter.list_markets_paged(limit=5)
        
        found = False
        for m in markets:
            if m.condition_id:
                print(f"Выбран рынок: {m.title}")
                main(m.condition_id)
                found = True
                break
                
        if not found:
            print("Не удалось найти рынок с condition_id для теста.")
