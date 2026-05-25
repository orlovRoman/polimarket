import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.shared.python.db import get_connection

def analyze_discussions():
    with get_connection() as conn:
        cursor = conn.cursor()
        
        print("--- Последние 5 сигналов от SCOUT/SWING ---")
        cursor.execute("SELECT id, market_id, platform, type, edge, confidence, summary, created_at FROM signals ORDER BY created_at DESC LIMIT 5")
        for row in cursor.fetchall():
            print(dict(row))
            
        print("\n--- Последние 5 мнений SHADOW/HERALD ---")
        cursor.execute("SELECT market_id, agent_name, agree, confidence, opinion, created_at FROM agent_opinions ORDER BY created_at DESC LIMIT 5")
        for row in cursor.fetchall():
            try:
                print(dict(row))
            except Exception:
                pass
            
        print("\n--- Последние 5 корреляций ---")
        cursor.execute("SELECT market_id_a, market_id_b, correlation_type, title_a, detected_at FROM correlations ORDER BY detected_at DESC LIMIT 5")
        for row in cursor.fetchall():
            print(dict(row))
            
        print("\n--- Кол-во рынков ---")
        cursor.execute("SELECT COUNT(*) as cnt FROM markets")
        print(cursor.fetchone()[0])

if __name__ == "__main__":
    analyze_discussions()
