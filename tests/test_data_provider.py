# tests/test_data_provider.py
import pytest
import config
import agents.shared.python.db as db_module
from web import data_provider
from core.models import Market
from datetime import datetime, timezone

@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Изолированная база данных для теста."""
    db_path = tmp_path / "test_data_provider.db"
    db_path_str = str(db_path)
    
    # Патчим DB_PATH в config и db_module
    monkeypatch.setattr(config, "DB_PATH", db_path_str)
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_db_initialized", False)
    
    db_module.init_db()
    return db_path

def test_get_overview_stats_empty(isolated_db):
    stats = data_provider.get_overview_stats()
    assert isinstance(stats, dict)
    assert 'scout' in stats
    assert stats['scout']['win_rate'] is None
    assert stats['scout']['pnl_7d'] == 0.0

def test_get_overview_stats_with_data(isolated_db):
    # Создаем тестовый рынок
    market = Market(
        id="market_1",
        platform="polymarket",
        title="Bitcoin to 100k",
        description="...",
        url="https://polymarket.com/1",
        outcome="YES",
        price=0.6,
        close_time=datetime.now(timezone.utc),
        tokens=[],
        volume=1000.0,
        condition_id="cond_1"
    )
    db_module.save_market(market)

    # Вставляем фейковый сигнал
    with db_module.get_connection() as conn:
        conn.execute("""
            INSERT INTO signals (id, type, market_id, platform, edge, confidence, priority, summary, details, status, strategy_type, pnl_realized, was_profitable, resolved_at)
            VALUES ('sig_1', 'scout', 'market_1', 'polymarket', 0.1, 0.8, 'HIGH', 'summary', 'details', 'WIN', 'scout', 15.0, 1, datetime('now'))
        """)
        conn.execute("""
            INSERT INTO strategy_metrics (strategy_type, period_start, period_end, total_signals, resolved_signals, profitable_signals, win_rate, sharpe_ratio)
            VALUES ('scout', datetime('now', '-30 days'), datetime('now'), 1, 1, 1, 1.0, 2.5)
        """)

    stats = data_provider.get_overview_stats()
    assert stats['scout']['win_rate'] == 1.0
    assert stats['scout']['sharpe'] == 2.5
    assert stats['scout']['pnl_7d'] == 15.0
    assert stats['scout']['pnl_30d'] == 15.0
    assert stats['scout']['signals_count'] == 1
    assert stats['scout']['status_emoji'] == "🟢"

def test_get_equity_curve(isolated_db):
    # Пустой
    curve = data_provider.get_equity_curve('scout', days=30)
    assert curve == []

    # Вставляем сигналы на разные даты
    with db_module.get_connection() as conn:
        conn.execute("""
            INSERT INTO signals (id, type, market_id, platform, edge, confidence, priority, summary, details, status, strategy_type, pnl_realized, was_profitable, resolved_at)
            VALUES 
            ('sig_a', 'scout', 'm1', 'poly', 0.1, 0.8, 'HIGH', 's', 'd', 'WIN', 'scout', 10.0, 1, datetime('now', '-2 days')),
            ('sig_b', 'scout', 'm1', 'poly', 0.1, 0.8, 'HIGH', 's', 'd', 'LOSS', 'scout', -5.0, 0, datetime('now', '-1 days'))
        """)

    curve = data_provider.get_equity_curve('scout', days=30)
    assert len(curve) == 2
    assert curve[0]['cumulative_pnl'] == 10.0
    assert curve[1]['cumulative_pnl'] == 5.0

    # Проверяем режим 'all'
    all_curves = data_provider.get_equity_curve('all', days=30)
    assert isinstance(all_curves, dict)
    assert 'scout' in all_curves
    assert len(all_curves['scout']) == 2

def test_get_penny_stocks_dashboard(isolated_db):
    # Пустой
    data = data_provider.get_penny_stocks_dashboard()
    assert data['active'] == []
    assert data['resolved'] == []
    assert data['stats']['active_count'] == 0

    # Вставляем Penny Stocks
    with db_module.get_connection() as conn:
        conn.execute("""
            INSERT INTO penny_stocks_monitoring (market_id, title, url, initial_price, current_price, max_price_seen, min_price_seen, status)
            VALUES 
            ('penny_a', 'Penny A', 'http://a', 0.03, 0.04, 0.05, 0.03, 'ACTIVE'),
            ('penny_b', 'Penny B', 'http://b', 0.08, 0.02, 0.08, 0.02, 'RESOLVED')
        """)
        conn.execute("""
            INSERT INTO signals (id, type, market_id, platform, edge, confidence, priority, summary, details, status, strategy_type, pnl_realized, was_profitable, resolved_at)
            VALUES ('sig_penny', 'penny', 'penny_b', 'poly', 0.1, 0.8, 'HIGH', 's', 'd', 'LOSS', 'penny_stocks', -10.0, 0, datetime('now'))
        """)

    data = data_provider.get_penny_stocks_dashboard()
    assert len(data['active']) == 1
    assert data['active'][0]['title'] == 'Penny A'
    assert len(data['resolved']) == 1
    assert data['resolved'][0]['title'] == 'Penny B'
    assert data['resolved'][0]['pnl_realized'] == -10.0
    assert data['stats']['active_count'] == 1
    assert data['stats']['resolved_count'] == 1
    assert data['price_distribution']['1-5¢'] == 1
    assert data['price_distribution']['5-10¢'] == 1

def test_get_auto_disable_candidates(isolated_db):
    with db_module.get_connection() as conn:
        conn.execute("""
            INSERT INTO strategy_metrics (strategy_type, period_start, period_end, total_signals, resolved_signals, profitable_signals, win_rate, sharpe_ratio)
            VALUES ('scout', datetime('now', '-30 days'), datetime('now'), 5, 5, 1, 0.2, -1.5)
        """)

    candidates = data_provider.get_auto_disable_candidates()
    assert len(candidates) == 1
    assert candidates[0]['strategy_type'] == 'scout'
    assert candidates[0]['sharpe_ratio'] == -1.5

def test_get_corridors_dashboard(isolated_db):
    with db_module.get_connection() as conn:
        conn.execute("""
            INSERT INTO synthetic_corridors (signal_id, event_title, event_url, lower_level, lower_price_yes, upper_level, upper_price_yes, theoretical_cost, theoretical_spread_pct, real_cost, real_spread_pct, created_at)
            VALUES ('sig_synth', 'Synthetic Title', 'http://synth', 10.0, 0.2, 20.0, 0.3, 0.5, 0.1, 0.6, 0.08, datetime('now'))
        """)
        conn.execute("""
            INSERT INTO temporal_corridors (signal_id, event_title, event_url, theoretical_cost, theoretical_spread_pct, real_cost, real_spread_pct, status, created_at)
            VALUES ('sig_temp', 'Temporal Title', 'http://temp', 0.4, 0.12, 0.5, 0.1, 'ACTIVE', datetime('now'))
        """)
        conn.execute("""
            INSERT INTO cross_arbitrage_signals (id, market_a_id, market_a_title, market_a_platform, market_a_price, market_a_url, market_b_id, market_b_title, market_b_platform, market_b_price, market_b_url, has_arbitrage, arbitrage_type, spread_percent, match_score, status, created_at)
            VALUES ('sig_cross', 'a_id', 'Title A', 'polymarket', 0.45, 'http://a', 'b_id', 'Title B', 'kalshi', 0.52, 'http://b', 1, 'CROSS_PLATFORM', 0.07, 0.9, 'new', datetime('now'))
        """)

    data = data_provider.get_corridors_dashboard()
    assert len(data['synthetic']) == 1
    assert data['synthetic'][0]['event_title'] == 'Synthetic Title'
    assert len(data['temporal']) == 1
    assert data['temporal'][0]['event_title'] == 'Temporal Title'
    assert len(data['cross']) == 1
    assert data['cross'][0]['market_a_title'] == 'Title A'
