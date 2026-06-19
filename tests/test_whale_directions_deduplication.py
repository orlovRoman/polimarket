import pytest

@pytest.fixture(autouse=True)
def setup_test_db():
    from agents.shared.python.db import init_db, get_connection
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM whale_virtual_trades_history")
        conn.execute("DELETE FROM whale_stocks_monitoring")
        conn.execute("DELETE FROM markets")
    yield

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
        assert directions[0]['amount_usd'] == pytest.approx(3000.0)  # сумма

def test_whale_directions_volumes_and_confidence():
    from agents.shared.python.db import add_whale_stock_to_monitoring, get_connection
    import json
    
    with get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO markets (id, platform, title, url, outcome, price, close_time) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                     ("mkt_vol", "polymarket", "Test Vol", "url", "YES", 0.5))
                     
    # Кошелек 1: YES на 1000
    add_whale_stock_to_monitoring("mkt_vol", "Test Vol", "url", 0.5, "YES", amount_usd=1000.0, wallet_address="0xW1")
    # Кошелек 2: YES на 500
    add_whale_stock_to_monitoring("mkt_vol", "Test Vol", "url", 0.5, "YES", amount_usd=500.0, wallet_address="0xW2")
    # Кошелек 3: NO на 1000
    add_whale_stock_to_monitoring("mkt_vol", "Test Vol", "url", 0.5, "NO", amount_usd=1000.0, wallet_address="0xW3")
    
    with get_connection() as conn:
        row = conn.execute("SELECT whale_count, whale_directions, confidence FROM whale_stocks_monitoring WHERE market_id = 'mkt_vol'").fetchone()
        
        directions = json.loads(row['whale_directions'])
        yes_vol = sum(d.get('amount_usd', 0) for d in directions if d['side'] == 'YES')
        no_vol = sum(d.get('amount_usd', 0) for d in directions if d['side'] == 'NO')
        
        assert row['whale_count'] == 3
        assert yes_vol == pytest.approx(1500.0)
        assert no_vol == pytest.approx(1000.0)
        
        # dominant_vol = 1500, total_vol = 2500, balance = 1500 / 2500 = 0.6. new_conf = base_conf * 0.6.
        # base_conf is 0.5 (default). 0.5 * 0.6 = 0.3
        assert abs(row['confidence'] - 0.3) < 0.01
