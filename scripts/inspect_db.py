# scripts/inspect_db.py
"""Скрипт для инспекции структуры таблицы penny_stocks_monitoring в БД
и вывода списка активных рынков без прогноза.
"""
import sqlite3
import sys
from pathlib import Path

# Добавляем корневую директорию проекта в path, чтобы импортировать config
sys.path.append(str(Path(__file__).parent.parent))
import config

def inspect_active_penny_stocks():
    print(f"DB_PATH: {config.DB_PATH}")
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 1. Схема таблицы penny_stocks_monitoring
    cursor.execute("PRAGMA table_info(penny_stocks_monitoring)")
    cols = cursor.fetchall()
    print("\nColumns in penny_stocks_monitoring:")
    for col in cols:
        print(f"  {col['name']}: {col['type']}")
        
    # 2. Активные penny stocks с NULL в predicted_outcome
    cursor.execute("""
        SELECT market_id, title, initial_price, current_price, predicted_outcome, virtual_bought_price
        FROM penny_stocks_monitoring
        WHERE status = 'ACTIVE' AND predicted_outcome IS NULL
    """)
    rows = cursor.fetchall()
    print(f"\nActive penny stocks without prediction (total {len(rows)}):")
    for r in rows[:10]:
        print(f"  ID: {r['market_id']}, Title: {r['title']}, Init: {r['initial_price']}, Curr: {r['current_price']}, Bought: {r['virtual_bought_price']}")
        
    # 3. Записи с null ценами
    cursor.execute("""
        SELECT COUNT(*) as cnt FROM penny_stocks_monitoring
        WHERE initial_price IS NULL OR current_price IS NULL
    """)
    null_prices = cursor.fetchone()['cnt']
    print(f"  Markets with NULL prices: {null_prices}")
    
    conn.close()

if __name__ == "__main__":
    inspect_active_penny_stocks()
