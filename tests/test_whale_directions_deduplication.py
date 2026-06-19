
def test_whale_directions_deduplication():
    from agents.shared.python.db import add_whale_stock_to_monitoring, get_connection
    import json
    
    # Создаем рынок для теста
    with get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO markets (id, platform, title, url, outcome, price, close_time) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                     ("mkt_dedup", "polymarket", "Test Dedup", "url", "YES", 0.5))
        
    for _ in range(3):
        add_whale_stock_to_monitoring(
            market_id="mkt_dedup",
            title="Test Dedup",
            url="url",
            initial_price=0.5,
            predicted_outcome="YES",
            amount_usd=1000.0,
            wallet_address="0xAAA"
        )
        
    with get_connection() as conn:
        row = conn.execute("SELECT whale_count, whale_directions FROM whale_stocks_monitoring WHERE market_id = 'mkt_dedup'").fetchone()
        assert row is not None
        
        directions = json.loads(row['whale_directions'])
        assert len(directions) == 1           # один уникальный кошелёк
        assert row['whale_count'] == 1
        assert directions[0]['amount_usd'] == 3000.0  # сумма
