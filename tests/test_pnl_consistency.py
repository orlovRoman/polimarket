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
        
        conn.execute("INSERT INTO whale_virtual_trades_history (market_id, title, url, outcome, bought_price, bought_outcome_price, sold_price, sold_outcome_price, pnl_points, pnl_percent, bet_size_usdc) VALUES ('m1', 'Test1', 'url1', 'YES', 0.5, 0.5, 0.75, 0.75, 25, 50, 100)")
        conn.execute("INSERT INTO whale_virtual_trades_history (market_id, title, url, outcome, bought_price, bought_outcome_price, sold_price, sold_outcome_price, pnl_points, pnl_percent, bet_size_usdc) VALUES ('m1', 'Test1', 'url1', 'YES', 0.5, 0.5, 0.65, 0.65, 15, 30, 100)")
        conn.execute("INSERT INTO whale_virtual_trades_history (market_id, title, url, outcome, bought_price, bought_outcome_price, sold_price, sold_outcome_price, pnl_points, pnl_percent, bet_size_usdc) VALUES ('m2', 'Test2', 'url2', 'NO', 0.5, 0.5, 0.40, 0.40, -10, -20, 100)")
        
    stats = get_whale_stocks_stats()
    dashboard = get_whale_stocks_dashboard()
    
    # Суммарный PnL в обоих случаях должен быть 80 - 20 = 60
    assert abs(stats['total_pnl_usd'] - dashboard['stats']['total_trades_pnl']) < 0.01
    assert abs(stats['total_pnl_usd'] - 60.0) < 0.01

def test_pnl_dates_consistency():
    from datetime import datetime, timedelta, timezone
    
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d %H:%M:%S+00:00")
    ten_days_ago_str = (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S+00:00")
    
    with get_connection() as conn:
        conn.execute("INSERT INTO markets (id, platform, title, url, outcome, price, close_time) VALUES (?, 'test', 'Test1', 'url1', 'YES', 0.5, datetime('now'))", ("m_today",))
        conn.execute("INSERT INTO markets (id, platform, title, url, outcome, price, close_time) VALUES (?, 'test', 'Test2', 'url2', 'NO', 0.5, datetime('now'))", ("m_10d",))
        
        # Сделка сегодня: PnL 50
        conn.execute(f"INSERT INTO whale_stocks_monitoring (market_id, title, url, initial_price, current_price, max_price_seen, min_price_seen, status, resolved_at) VALUES ('m_today', 'Test1', 'url1', 0.5, 0.75, 0.75, 0.5, 'RESOLVED', '{today_str}')")
        conn.execute(f"INSERT INTO whale_virtual_trades_history (market_id, title, url, outcome, bought_price, bought_outcome_price, sold_price, sold_outcome_price, pnl_points, pnl_percent, bet_size_usdc, sold_at) VALUES ('m_today', 'Test1', 'url1', 'YES', 0.5, 0.5, 0.75, 0.75, 25, 50, 100, '{today_str}')")
        
        # Сделка 10 дней назад: PnL 30
        conn.execute(f"INSERT INTO whale_stocks_monitoring (market_id, title, url, initial_price, current_price, max_price_seen, min_price_seen, status, resolved_at) VALUES ('m_10d', 'Test2', 'url2', 0.5, 0.65, 0.65, 0.5, 'RESOLVED', '{ten_days_ago_str}')")
        conn.execute(f"INSERT INTO whale_virtual_trades_history (market_id, title, url, outcome, bought_price, bought_outcome_price, sold_price, sold_outcome_price, pnl_points, pnl_percent, bet_size_usdc, sold_at) VALUES ('m_10d', 'Test2', 'url2', 'NO', 0.5, 0.5, 0.65, 0.65, 15, 30, 100, '{ten_days_ago_str}')")
        
    dashboard = get_whale_stocks_dashboard()
    
    # 50 pnl_today, 50 + 30 = 80 pnl_30d, 50 pnl_7d
    assert abs(dashboard['stats']['pnl_today'] - 50.0) < 0.01
    assert abs(dashboard['stats']['pnl_7d'] - 50.0) < 0.01
    assert abs(dashboard['stats']['pnl_30d'] - 80.0) < 0.01
