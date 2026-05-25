import sys
import os
import requests
import json
sys.path.append(os.getcwd())

from agents.shared.python.db import get_connection, update_episode_accuracy

def resolve_markets():
    with get_connection() as conn:
        cursor = conn.execute("""
            SELECT DISTINCT market_id FROM agent_episodes WHERE is_correct IS NULL
        """)
        market_ids = [r['market_id'] for r in cursor.fetchall()]
        
    if not market_ids:
        print("No unresolved episodes.")
        return
        
    for m_id in market_ids:
        try:
            resp = requests.get(f"https://gamma-api.polymarket.com/markets/{m_id}")
            if resp.status_code != 200:
                continue
                
            data = resp.json()
            if data.get("closed") or data.get("active") == False:
                # В Polymarket исход часто определяется ценой 1 или 0
                prices = data.get("outcomePrices")
                if isinstance(prices, str):
                    try:
                        prices = json.loads(prices)
                    except:
                        prices = []
                
                if prices:
                    try:
                        yes_price = float(prices[0])
                        won = (yes_price >= 0.99)
                    except (ValueError, TypeError):
                        won = False
                else:
                    won = False
                    
                with get_connection() as conn:
                    # fetch episodes for this market
                    episodes = conn.execute("SELECT id, opinion, agent FROM agent_episodes WHERE market_id = ? AND is_correct IS NULL", (m_id,)).fetchall()
                    for ep in episodes:
                        op = str(ep['opinion']).lower()
                        is_buy = "buy" in op or "agree" in op or "согласен" in op or "true" in op
                        
                        # Если агент советовал входить и исход сыграл -> он прав (или отговорил и исход не сыграл)
                        # Это базовая эвристика. В реальности SCOUT/SWING могут торговать NO-контракты.
                        is_correct = (is_buy == won)
                        update_episode_accuracy(ep['id'], is_correct)
                        print(f"[{ep['agent']}] Market {m_id} resolved (YES won? {won}). Decision was {op}. Correct? {is_correct}")
        except Exception as e:
            print(f"Error resolving {m_id}: {e}")

if __name__ == "__main__":
    resolve_markets()
