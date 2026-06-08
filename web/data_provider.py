# web/data_provider.py
import sqlite3
import math
from datetime import datetime, timedelta, timezone

def get_status_emoji(sharpe: float | None, win_rate: float | None) -> str:
    """Определяет статус-эмодзи стратегии на основе Sharpe и win rate."""
    if sharpe is not None and sharpe < 0:
        return "🔴"
    if win_rate is not None and win_rate > 0.55:
        return "🟢"
    return "🟡"

def get_overview_stats() -> dict:
    """
    Агрегирует общие метрики по всем стратегиям:
    - win_rate и sharpe из последнего расчета в strategy_metrics
    - pnl за 7 и 30 дней, а также количество сигналов из signals
    """
    from agents.shared.python.db import get_connection
    strategies = ['scout', 'synthetic_corridor', 'temporal_corridor', 'cross_platform', 'whale', 'penny_stocks']
    stats = {}
    for s in strategies:
        stats[s] = {
            'win_rate': None,
            'pnl_7d': 0.0,
            'pnl_30d': 0.0,
            'sharpe': None,
            'signals_count': 0,
            'status_emoji': '🟡'
        }
        
    with get_connection() as conn:
        # 1. Читаем последние метрики из strategy_metrics
        rows = conn.execute("""
            SELECT strategy_type, win_rate, sharpe_ratio
            FROM strategy_metrics
            WHERE id IN (SELECT MAX(id) FROM strategy_metrics GROUP BY strategy_type)
        """).fetchall()
        for r in rows:
            stype = r['strategy_type']
            if stype in stats:
                stats[stype]['win_rate'] = r['win_rate']
                stats[stype]['sharpe'] = r['sharpe_ratio']

        # 2. Вычисляем rolling PnL и общее число сигналов из signals
        rows_pnl = conn.execute("""
            SELECT strategy_type,
                   COUNT(*) as total,
                   SUM(CASE WHEN resolved_at >= datetime('now', '-7 days') THEN pnl_realized ELSE 0 END) as pnl_7d,
                   SUM(CASE WHEN resolved_at >= datetime('now', '-30 days') THEN pnl_realized ELSE 0 END) as pnl_30d
            FROM signals
            WHERE status IN ('WIN', 'LOSS') AND strategy_type IS NOT NULL
            GROUP BY strategy_type
        """).fetchall()
        for r in rows_pnl:
            stype = r['strategy_type']
            if stype in stats:
                stats[stype]['pnl_7d'] = round(r['pnl_7d'] or 0.0, 2)
                stats[stype]['pnl_30d'] = round(r['pnl_30d'] or 0.0, 2)
                stats[stype]['signals_count'] = r['total']

        # 3. Обновляем статус-эмодзи
        for stype, sdata in stats.items():
            sdata['status_emoji'] = get_status_emoji(sdata['sharpe'], sdata['win_rate'])
            
    return stats

def get_equity_curve(strategy: str, days: int = 30) -> list[dict] | dict[str, list[dict]]:
    """
    Генерирует кривую доходности (кумулятивный PnL по дням).
    Если strategy='all', возвращает словарь кривых для всех стратегий.
    """
    from agents.shared.python.db import get_connection
    strategies = ['scout', 'synthetic_corridor', 'temporal_corridor', 'cross_platform', 'whale', 'penny_stocks']
    period_start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    
    def get_curve_for_strategy(conn, stype):
        rows = conn.execute("""
            SELECT date(resolved_at) as date, SUM(pnl_realized) as daily_pnl
            FROM signals
            WHERE strategy_type = ? 
              AND status IN ('WIN', 'LOSS') 
              AND resolved_at >= ?
            GROUP BY date(resolved_at)
            ORDER BY date(resolved_at) ASC
        """, (stype, period_start)).fetchall()
        
        cumulative = 0.0
        curve = []
        for r in rows:
            pnl = r['daily_pnl'] or 0.0
            cumulative += pnl
            curve.append({
                'date': r['date'],
                'daily_pnl': round(pnl, 2),
                'cumulative_pnl': round(cumulative, 2)
            })
        return curve

    with get_connection() as conn:
        if strategy == 'all':
            return {stype: get_curve_for_strategy(conn, stype) for stype in strategies}
        else:
            return get_curve_for_strategy(conn, strategy)

def get_penny_stocks_dashboard() -> dict:
    """
    Собирает данные для дашборда Penny Stocks (активные, завершенные позиции, статистика, распределение).
    """
    from agents.shared.python.db import get_connection
    with get_connection() as conn:
        # Активные позиции
        active_rows = conn.execute("""
            SELECT market_id, title, url, initial_price, current_price, max_price_seen, min_price_seen, volume_2h, predicted_outcome, edge, confidence, added_at
            FROM penny_stocks_monitoring
            WHERE status = 'ACTIVE' AND initial_price <= 0.10
            ORDER BY added_at DESC
        """).fetchall()
        active = [dict(r) for r in active_rows]

        # Завершенные позиции (с PnL из сигналов)
        resolved_rows = conn.execute("""
            SELECT p.market_id, p.title, p.url, p.initial_price, p.current_price, p.max_price_seen, p.min_price_seen, p.predicted_outcome, p.actual_outcome, p.edge, p.confidence, p.resolved_at,
                   s.pnl_realized
            FROM penny_stocks_monitoring p
            LEFT JOIN signals s ON p.market_id = s.market_id AND s.strategy_type = 'penny_stocks'
            WHERE p.status = 'RESOLVED' AND p.initial_price <= 0.10
            ORDER BY p.resolved_at DESC
            LIMIT 50
        """).fetchall()
        resolved = [dict(r) for r in resolved_rows]

        # Статистика
        total_active = len(active)
        total_resolved = len(resolved)
        
        stats_row = conn.execute("""
            SELECT 
                COUNT(*) as count,
                SUM(CASE WHEN was_profitable = 1 THEN 1 ELSE 0 END) as wins,
                MAX(pnl_realized) as best_pnl,
                AVG(pnl_realized) as avg_pnl
            FROM signals
            WHERE strategy_type = 'penny_stocks' AND status IN ('WIN', 'LOSS') AND market_price_at_signal <= 0.10
        """).fetchone()

        win_rate = None
        best_pnl = 0.0
        avg_pnl = 0.0
        if stats_row and stats_row['count'] and stats_row['count'] > 0:
            win_rate = stats_row['wins'] / stats_row['count']
            best_pnl = stats_row['best_pnl'] or 0.0
            avg_pnl = stats_row['avg_pnl'] or 0.0

        avg_entry_row = conn.execute("""
            SELECT AVG(initial_price) as avg_entry
            FROM penny_stocks_monitoring
            WHERE initial_price <= 0.10
        """).fetchone()
        avg_entry = avg_entry_row['avg_entry'] if avg_entry_row else None

        stats = {
            'active_count': total_active,
            'resolved_count': total_resolved,
            'win_rate': win_rate,
            'avg_entry_price': avg_entry,
            'best_trade_pnl': best_pnl,
            'avg_pnl': avg_pnl
        }

        # Распределение цен входа
        all_prices_rows = conn.execute("SELECT initial_price FROM penny_stocks_monitoring WHERE initial_price <= 0.10").fetchall()
        bins = {
            '1-5¢': 0,
            '5-10¢': 0,
            '10-15¢': 0,
            '15-20¢': 0,
            '20+¢': 0
        }
        for r in all_prices_rows:
            p = r['initial_price']
            if p is None:
                continue
            if p < 0.05:
                bins['1-5¢'] += 1
            elif p < 0.10:
                bins['5-10¢'] += 1
            elif p < 0.15:
                bins['10-15¢'] += 1
            elif p < 0.20:
                bins['15-20¢'] += 1
            else:
                bins['20+¢'] += 1

    return {
        'active': active,
        'resolved': resolved,
        'stats': stats,
        'price_distribution': bins
    }

def get_strategy_signals(strategy: str, days: int = 30, limit: int = 50) -> list:
    """Возвращает последние сигналы для конкретной стратегии вместе с названием рынков."""
    from agents.shared.python.db import get_connection
    period_start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT s.id, s.created_at, s.status, s.edge, s.confidence, s.pnl_realized, s.target_outcome,
                   s.estimated_probability, s.predicted_probability, s.market_price_at_signal,
                   m.title as market_title, m.url as market_url
            FROM signals s
            JOIN markets m ON s.market_id = m.id
            WHERE s.strategy_type = ? AND s.created_at >= ?
            ORDER BY s.created_at DESC
            LIMIT ?
        """, (strategy, period_start, limit)).fetchall()
        return [dict(r) for r in rows]

def get_auto_disable_candidates() -> list:
    """Возвращает список стратегий, у которых Sharpe Ratio < 0."""
    from agents.shared.python.db import get_connection
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT strategy_type, win_rate, sharpe_ratio, avg_realized_pnl
            FROM strategy_metrics
            WHERE id IN (SELECT MAX(id) FROM strategy_metrics GROUP BY strategy_type)
              AND sharpe_ratio < 0
        """).fetchall()
        return [dict(r) for r in rows]

def get_corridors_dashboard() -> dict:
    """Собирает лог коридоров (синтетические, временные, кросс-платформенные) и KPI по ним."""
    from agents.shared.python.db import get_connection
    with get_connection() as conn:
        # Синтетические коридоры
        synth_rows = conn.execute("""
            SELECT signal_id, event_slug, event_title, event_url, lower_level, lower_price_yes, upper_level, upper_price_yes, theoretical_cost, theoretical_spread_pct, real_cost, real_spread_pct, total_invested_usd, pnl_in_corridor_usd, roi_min_pct, roi_max_pct, created_at
            FROM synthetic_corridors
            ORDER BY created_at DESC
            LIMIT 50
        """).fetchall()
        synthetic = [dict(r) for r in synth_rows]

        # Временные коридоры
        temp_rows = conn.execute("""
            SELECT id, signal_id, event_slug, event_title, event_url, theoretical_cost, theoretical_spread_pct, real_cost, real_spread_pct, early_stake_usd, late_stake_usd, ev_usd, roi_pct, status, created_at
            FROM temporal_corridors
            ORDER BY created_at DESC
            LIMIT 50
        """).fetchall()
        temporal = [dict(r) for r in temp_rows]

        # Кросс-платформа
        cross_rows = conn.execute("""
            SELECT id, market_a_title, market_a_platform, market_a_price, market_b_title, market_b_platform, market_b_price, spread_percent, arbitrage_type, reasoning, status, created_at, action_a, action_b, entry_price_a_cents, entry_price_b_cents, expected_pnl_pct, risk_level
            FROM cross_arbitrage_signals
            ORDER BY created_at DESC
            LIMIT 50
        """).fetchall()
        cross = [dict(r) for r in cross_rows]

        # KPI по стратегиям коридоров
        kpi_rows = conn.execute("""
            SELECT strategy_type,
                   COUNT(*) as total,
                   SUM(CASE WHEN was_profitable = 1 THEN 1 ELSE 0 END) as wins,
                   AVG(pnl_realized) as avg_pnl,
                   MAX(pnl_realized) as best_pnl
            FROM signals
            WHERE strategy_type IN ('synthetic_corridor', 'temporal_corridor', 'cross_platform') AND status IN ('WIN', 'LOSS')
            GROUP BY strategy_type
        """).fetchall()
        
        kpis = {}
        for r in kpi_rows:
            stype = r['strategy_type']
            total = r['total']
            win_rate = r['wins'] / total if total > 0 else 0.0
            kpis[stype] = {
                'total': total,
                'win_rate': win_rate,
                'avg_pnl': round(r['avg_pnl'] or 0.0, 2),
                'best_pnl': round(r['best_pnl'] or 0.0, 2)
            }
            
        for stype in ['synthetic_corridor', 'temporal_corridor', 'cross_platform']:
            if stype not in kpis:
                kpis[stype] = {
                    'total': 0,
                    'win_rate': None,
                    'avg_pnl': 0.0,
                    'best_pnl': 0.0
                }

    return {
        'synthetic': synthetic,
        'temporal': temporal,
        'cross': cross,
        'kpis': kpis
    }
