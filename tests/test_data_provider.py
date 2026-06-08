# tests/test_data_provider.py
import pytest
import config
import agents.shared.python.db as db_module
from web import data_provider
from core.models import Market
from datetime import datetime, timezone

@pytest.fixture(autouse=False)
def isolated_db(tmp_path, monkeypatch):
    """Изолированная база данных для теста."""
    db_path = tmp_path / "test_data_provider.db"
    
    # Патчим DB_PATH в config и db_module
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_db_initialized", False)
    
    db_module.init_db()
    yield db_path
    db_module._db_initialized = False

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

    # Тестируем валидацию и клэмпинг days в get_equity_curve
    curve_neg = data_provider.get_equity_curve('scout', days=-10)
    assert len(curve_neg) > 0  # не должно падать
    curve_huge = data_provider.get_equity_curve('scout', days=99999)
    assert len(curve_huge) == 2  # клэмпится к 365 и возвращает 2 найденные точки

def test_get_penny_stocks_dashboard(isolated_db):
    # Пустой
    data = data_provider.get_penny_stocks_dashboard()
    assert data['active'] == []
    assert data['resolved'] == []
    assert data['stats']['active_count'] == 0

    # Вставляем Penny Stocks
    with db_module.get_connection() as conn:
        conn.execute("""
            INSERT INTO penny_stocks_monitoring (market_id, title, url, initial_price, current_price, max_price_seen, min_price_seen, status, predicted_outcome)
            VALUES 
            ('penny_a', 'Penny A', 'http://a', 0.03, 0.04, 0.05, 0.03, 'ACTIVE', 'YES'),
            ('penny_b', 'Penny B', 'http://b', 0.08, 0.02, 0.08, 0.02, 'RESOLVED', 'YES'),
            ('penny_c', 'Penny C', 'http://c', 0.02, 0.02, 0.02, 0.02, 'ACTIVE', NULL)
        """)
        conn.execute("""
            INSERT INTO signals (id, type, market_id, platform, edge, confidence, priority, summary, details, status, strategy_type, pnl_realized, was_profitable, resolved_at, market_price_at_signal, target_outcome)
            VALUES ('sig_penny', 'penny', 'penny_b', 'poly', 0.1, 0.8, 'HIGH', 's', 'd', 'LOSS', 'penny_stocks', -10.0, 0, datetime('now'), 0.08, 'YES')
        """)

    data = data_provider.get_penny_stocks_dashboard()
    # Теперь все активные (с прогнозом и без) попадают в active
    active_titles = [x['title'] for x in data['active']]
    assert 'Penny A' in active_titles
    assert 'Penny C' in active_titles  # NULL-рынок тоже в active
    assert data['stats']['active_count'] == 2
    assert data['stats']['active_predicted_count'] == 1  # только Penny A с прогнозом

    # penny_c без прогноза — должен иметь cheap_outcome
    penny_c = next(x for x in data['active'] if x['title'] == 'Penny C')
    assert penny_c['cheap_outcome'] == 'YES'
    assert penny_c['predicted_outcome'] is None

    # penny_a с прогнозом YES
    penny_a = next(x for x in data['active'] if x['title'] == 'Penny A')
    assert penny_a['initial_price_outcome'] == pytest.approx(0.03, abs=1e-4)

    assert len(data['resolved']) == 1
    assert data['resolved'][0]['title'] == 'Penny B'
    assert data['resolved'][0]['pnl_realized'] == -10.0
    assert data['stats']['resolved_count'] == 1
    assert data['price_distribution']['1-5¢'] == 2   # penny_a(0.03) + penny_c(0.02)
    assert data['price_distribution']['5-10¢'] == 0  # penny_b(0.08) - RESOLVED, больше не попадает в распределение активных

def test_penny_stocks_dashboard_filtering(isolated_db):
    # Тест фильтрации:
    # 1. YES-прогноз с начальной ценой 0.05 (должен пройти: <= 0.10)
    # 2. YES-прогноз с начальной ценой 0.95 (должен отсеяться: > 0.10)
    # 3. NO-прогноз с начальной ценой 0.95 (должен пройти: NO по цене 0.05 <= 0.10)
    # 4. NO-прогноз с начальной ценой 0.05 (должен отсеяться: NO по цене 0.95 > 0.10)
    # 5. NULL-прогноз с начальной ценой 0.04 (теперь тоже в active, как неоцененный)
    with db_module.get_connection() as conn:
        conn.execute("""
            INSERT INTO penny_stocks_monitoring (market_id, title, url, initial_price, current_price, max_price_seen, min_price_seen, status, predicted_outcome)
            VALUES 
            ('p1', 'Yes Pass', 'http://1', 0.05, 0.05, 0.05, 0.05, 'ACTIVE', 'YES'),
            ('p2', 'Yes Fail', 'http://2', 0.95, 0.95, 0.95, 0.95, 'ACTIVE', 'YES'),
            ('p3', 'No Pass',  'http://3', 0.95, 0.94, 0.95, 0.94, 'ACTIVE', 'NO'),
            ('p4', 'No Fail',  'http://4', 0.05, 0.05, 0.05, 0.05, 'ACTIVE', 'NO'),
            ('p5', 'Null Pass','http://5', 0.04, 0.04, 0.04, 0.04, 'ACTIVE', NULL)
        """)
        
    data = data_provider.get_penny_stocks_dashboard()
    active_titles = [x['title'] for x in data['active']]

    # Оцененные рынки в active (p1 YES pass, p3 NO pass)
    assert 'Yes Pass' in active_titles
    assert 'No Pass' in active_titles

    # Неподходящие оцененные рынки НЕ в active (p2 YES fail, p4 NO fail)
    assert 'Yes Fail' not in active_titles
    assert 'No Fail' not in active_titles

    # NULL-рынок (p5) теперь ВХОДИТ в active (единый список с серым бейджем)
    assert 'Null Pass' in active_titles
    p5_data = next(x for x in data['active'] if x['market_id'] == 'p5')
    assert p5_data['predicted_outcome'] is None
    assert p5_data['cheap_outcome'] == 'YES'  # 0.04 < 0.90

    # Проверяем цены исхода для p3 (NO):
    p3_data = next(x for x in data['active'] if x['market_id'] == 'p3')
    assert p3_data['initial_price_outcome'] == pytest.approx(0.05, abs=1e-4)  # 1.0 - 0.95
    assert p3_data['current_price_outcome'] == pytest.approx(0.06, abs=1e-4)  # 1.0 - 0.94

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
        # Сигнал для corridors (LOSS), чтобы проверить win_rate = 0.0 при total > 0, wins = 0
        conn.execute("""
            INSERT INTO signals (id, type, market_id, platform, edge, confidence, priority, summary, details, status, strategy_type, pnl_realized, was_profitable, resolved_at)
            VALUES ('sig_corr_loss', 'scout', 'm1', 'poly', 0.1, 0.8, 'HIGH', 's', 'd', 'LOSS', 'synthetic_corridor', -5.0, 0, datetime('now'))
        """)

    data = data_provider.get_corridors_dashboard()
    assert len(data['synthetic']) == 1
    assert data['synthetic'][0]['event_title'] == 'Synthetic Title'
    assert len(data['temporal']) == 1
    assert data['temporal'][0]['event_title'] == 'Temporal Title'
    assert len(data['cross']) == 1
    assert data['cross'][0]['market_a_title'] == 'Title A'

    # Проверяем kpis структуру
    assert 'kpis' in data
    for stype in ['synthetic_corridor', 'temporal_corridor', 'cross_platform']:
        assert stype in data['kpis']
        kpi = data['kpis'][stype]
        assert 'total' in kpi
        assert 'win_rate' in kpi
        assert 'avg_pnl' in kpi
        assert 'best_pnl' in kpi

    # Проверяем win_rate при wins=0, total=1
    assert data['kpis']['synthetic_corridor']['win_rate'] == 0.0
    assert data['kpis']['synthetic_corridor']['total'] == 1
    
    # При отсутствии сигналов — дефолтные значения, не ошибка
    assert data['kpis']['temporal_corridor']['win_rate'] is None
    assert data['kpis']['temporal_corridor']['total'] == 0

def test_virtual_portfolio_operations(isolated_db):
    # Вставляем Penny Stock в мониторинг
    with db_module.get_connection() as conn:
        conn.execute("""
            INSERT INTO penny_stocks_monitoring (market_id, title, url, initial_price, current_price, max_price_seen, min_price_seen, status, predicted_outcome)
            VALUES ('penny_v', 'Penny Virtual', 'http://v', 0.04, 0.05, 0.05, 0.04, 'ACTIVE', 'YES')
        """)
    
    # Изначально портфель пуст
    data = data_provider.get_penny_stocks_dashboard()
    assert len(data['portfolio']) == 0
    
    # "Покупаем" виртуально
    from agents.shared.python.db import buy_virtual_penny_stock
    buy_virtual_penny_stock('penny_v', 0.04)
    
    data = data_provider.get_penny_stocks_dashboard()
    assert len(data['portfolio']) == 1
    assert data['portfolio'][0]['market_id'] == 'penny_v'
    assert data['portfolio'][0]['virtual_bought_price'] == 0.04
    assert data['portfolio'][0]['pnl_cents'] == 0.01  # current_price(0.05) - bought(0.04)
    assert data['portfolio'][0]['pnl_percent'] == 25.0
    
    # "Продаем" (удаляем)
    from agents.shared.python.db import sell_virtual_penny_stock
    sell_virtual_penny_stock('penny_v')
    
    data = data_provider.get_penny_stocks_dashboard()
    assert len(data['portfolio']) == 0

def test_virtual_portfolio_no_outcome_approx(isolated_db):
    # Позиция со ставкой на NO
    with db_module.get_connection() as conn:
        conn.execute("""
            INSERT INTO penny_stocks_monitoring (market_id, title, url, initial_price, current_price, max_price_seen, min_price_seen, status, predicted_outcome)
            VALUES ('penny_no', 'Penny NO', 'http://no', 0.04, 0.03, 0.04, 0.03, 'ACTIVE', 'NO')
        """)
        
    from agents.shared.python.db import buy_virtual_penny_stock
    buy_virtual_penny_stock('penny_no', 0.04)  # YES=0.04, значит NO=0.96
    
    data = data_provider.get_penny_stocks_dashboard()
    assert len(data['portfolio']) == 1
    p = data['portfolio'][0]
    assert p['bought_outcome_price'] == pytest.approx(0.96, abs=1e-4)
    assert p['current_outcome_price'] == pytest.approx(0.97, abs=1e-4)
    assert p['pnl_cents'] == pytest.approx(0.01, abs=1e-4)
    assert p['pnl_percent'] == pytest.approx(1.04, abs=0.01)

def test_cheapest_price_outcome_no_direction(isolated_db):
    """NULL-рынок с init >= 0.90 должен считать цену исхода как 1.0 - init (NO-направление)."""
    with db_module.get_connection() as conn:
        conn.execute("""
            INSERT INTO penny_stocks_monitoring
              (market_id, title, url, initial_price, current_price,
               max_price_seen, min_price_seen, status, predicted_outcome)
            VALUES
              ('c_no', 'Cheap NO Market', 'http://cno', 0.93, 0.92, 0.93, 0.91, 'ACTIVE', NULL)
        """)

    data = data_provider.get_penny_stocks_dashboard()
    # Теперь NULL-рынок попадает в active
    active_by_id = {x['market_id']: x for x in data['active']}

    assert 'c_no' in active_by_id
    c = active_by_id['c_no']
    assert c['cheap_outcome'] == 'NO'
    assert c['predicted_outcome'] is None
    assert c['initial_price_outcome'] == pytest.approx(0.07, abs=1e-4)   # 1.0 - 0.93
    assert c['current_price_outcome'] == pytest.approx(0.08, abs=1e-4)   # 1.0 - 0.92
    assert c['max_price_seen_outcome'] == pytest.approx(0.09, abs=1e-4)  # 1.0 - 0.91
    assert c['min_price_seen_outcome'] == pytest.approx(0.07, abs=1e-4)  # 1.0 - 0.93

def test_active_limit_100(isolated_db):
    """active не должен возвращать больше 100 строк (LIMIT 100 в SQL)."""
    with db_module.get_connection() as conn:
        conn.executemany("""
            INSERT INTO penny_stocks_monitoring
              (market_id, title, url, initial_price, current_price,
               max_price_seen, min_price_seen, status, predicted_outcome)
            VALUES (?, ?, 'http://x', 0.03, 0.03, 0.03, 0.03, 'ACTIVE', NULL)
        """, [(f"bulk_{i}", f"Bulk {i}") for i in range(150)])

    data = data_provider.get_penny_stocks_dashboard()
    assert len(data['active']) == 100


def test_null_market_in_active_not_excluded(isolated_db):
    """Рынок без прогноза (NULL) должен попадать в active с cheap_outcome."""
    with db_module.get_connection() as conn:
        conn.execute("""
            INSERT INTO penny_stocks_monitoring
              (market_id, title, url, initial_price, current_price,
               max_price_seen, min_price_seen, status, predicted_outcome)
            VALUES ('null_mkt', 'Null Market', 'http://nm', 0.05, 0.05, 0.05, 0.05, 'ACTIVE', NULL)
        """)

    data = data_provider.get_penny_stocks_dashboard()
    active_ids = [x['market_id'] for x in data['active']]
    assert 'null_mkt' in active_ids
    null_row = next(x for x in data['active'] if x['market_id'] == 'null_mkt')
    assert null_row['predicted_outcome'] is None
    assert null_row['cheap_outcome'] == 'YES'


def test_virtual_portfolio_null_prediction_no_outcome(isolated_db):
    """Виртуальный портфель для рынка без прогноза с NO-направлением (init >= 0.90)."""
    with db_module.get_connection() as conn:
        conn.execute("""
            INSERT INTO penny_stocks_monitoring (market_id, title, url, initial_price, current_price, max_price_seen, min_price_seen, status, predicted_outcome)
            VALUES ('penny_null_no', 'Penny Null NO', 'http://null_no', 0.94, 0.93, 0.94, 0.93, 'ACTIVE', NULL)
        """)
        
    from agents.shared.python.db import buy_virtual_penny_stock
    buy_virtual_penny_stock('penny_null_no', 0.94)
    
    data = data_provider.get_penny_stocks_dashboard()
    assert len(data['portfolio']) == 1
    p = data['portfolio'][0]
    assert p['cheap_outcome'] == 'NO'
    assert p['bought_outcome_price'] == pytest.approx(0.06, abs=1e-4)
    assert p['current_outcome_price'] == pytest.approx(0.07, abs=1e-4)
    assert p['pnl_cents'] == pytest.approx(0.01, abs=1e-4)

