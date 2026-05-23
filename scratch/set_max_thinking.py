import sqlite3
import os
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "vault" / "polymarket.db"

def set_max_thinking():
    print(f"Connecting to database: {DB_PATH.absolute()}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Создаем таблицу memory, если её нет
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memory (
            key TEXT PRIMARY KEY,
            value TEXT,
            category TEXT DEFAULT 'general',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 2. Прописываем максимальные настройки размышления
    settings = {
        "scan_limit": "30",
        "min_edge": "0.01",  # Порог математического преимущества 1% (выдает почти все идеи)
        "rag_level": "3",    # Глубокий RAG L3
        "selected_model": "openrouter/owl-alpha"  # Оставляем бесплатную Owl Alpha или с возможностью смены
    }
    
    for key, val in settings.items():
        cursor.execute("""
            INSERT OR REPLACE INTO memory (key, value, category)
            VALUES (?, ?, 'settings')
        """, (key, val))
        print(f"Set: {key} = {val}")
        
    conn.commit()
    conn.close()
    print("Database settings updated successfully!")

if __name__ == "__main__":
    set_max_thinking()
