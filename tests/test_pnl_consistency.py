import pytest
from datetime import datetime, timezone
from agents.shared.python.db import init_db, get_connection, save_market, get_whale_stocks_stats
from web.data_provider import get_whale_stocks_dashboard

@pytest.fixture(autouse=True)
def setup_test_db():
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM whale_virtual_trades_history")
        conn.execute("DELETE FROM whale_stocks_monitoring")
        conn.execute("DELETE FROM markets")
    yield

def test_pnl_consistency_between_stats_and_dashboard():
    # Создать 2 рынка, купить+продать виртуально каждый
    with get_connection() as conn:
        conn.execute("INSERT INTO markets (id, platform, title, url, outcome, price, close_time) VALUES (?, 'test', 'Test1', 'url1', 'YES', 0.5, datetime('now'))", ("m1",))
        conn.execute("INSERT INTO markets (id, platform, title, url, outcome, price, close_time) VALUES (?, 'test', 'Test2', 'url2', 'NO', 0.5, datetime('now'))", ("m2",))
        
        conn.execute("INSERT INTO whale_virtual_trades_history (market_id, title, url, outcome, bought_price, bought_outcome_price, sold_price, sold_outcome_price, pnl_cents, pnl_percent, bet_size_usdc) VALUES ('m1', 'Test1', 'url1', 'YES', 0.5, 0.5, 0.75, 0.75, 25, 50, 100)")
        conn.execute("INSERT INTO whale_virtual_trades_history (market_id, title, url, outcome, bought_price, bought_outcome_price, sold_price, sold_outcome_price, pnl_cents, pnl_percent, bet_size_usdc) VALUES ('m1', 'Test1', 'url1', 'YES', 0.5, 0.5, 0.65, 0.65, 15, 30, 100)")
        conn.execute("INSERT INTO whale_virtual_trades_history (market_id, title, url, outcome, bought_price, bought_outcome_price, sold_price, sold_outcome_price, pnl_cents, pnl_percent, bet_size_usdc) VALUES ('m2', 'Test2', 'url2', 'NO', 0.5, 0.5, 0.40, 0.40, -10, -20, 100)")
        
    stats = get_whale_stocks_stats()
    dashboard = get_whale_stocks_dashboard()
    
    # Суммарный PnL в обоих случаях должен быть 80 - 20 = 60
    assert abs(stats['total_pnl_usd'] - dashboard['stats']['total_trades_pnl']) < 0.01
    assert abs(stats['total_pnl_usd'] - 60.0) < 0.01
