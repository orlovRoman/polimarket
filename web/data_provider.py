# web/data_provider.py
import sqlite3
import math
from datetime import datetime, timedelta, timezone
from typing import Optional, Any

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

        # 2.5 Отдельно догружаем статистику для penny_stocks из виртуальной истории сделок.
        # ВНИМАНИЕ: pnl_cents в БД хранит значение в долларах (долях доллара, например 0.05 = $0.05),
        # поэтому напрямую суммируем и округляем до 2 знаков, так же как для других стратегий.
        penny_pnl = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN sold_at >= datetime('now', '-7 days') THEN pnl_cents ELSE 0.0 END) as pnl_7d,
                SUM(CASE WHEN sold_at >= datetime('now', '-30 days') THEN pnl_cents ELSE 0.0 END) as pnl_30d
            FROM penny_virtual_trades_history
        """).fetchone()
        if penny_pnl:
            stats['penny_stocks']['pnl_7d'] = round(penny_pnl['pnl_7d'] or 0.0, 2)
            stats['penny_stocks']['pnl_30d'] = round(penny_pnl['pnl_30d'] or 0.0, 2)
            stats['penny_stocks']['signals_count'] = penny_pnl['total']

        # 3. Обновляем статус-эмодзи
        for stype, sdata in stats.items():
            sdata['status_emoji'] = get_status_emoji(sdata['sharpe'], sdata['win_rate'])

    return stats

def get_equity_curve(strategy: str, days: int = 30) -> list[dict] | dict[str, list[dict]]:
    """
    Генерирует кривую доходности (кумулятивный PnL по дням).
    Если strategy='all', возвращает словарь кривых для всех стратегий.
    """
    try:
        days = int(days)
        days = max(1, min(days, 365))
    except (ValueError, TypeError):
        days = 30

    from agents.shared.python.db import get_connection
    strategies = ['scout', 'synthetic_corridor', 'temporal_corridor', 'cross_platform', 'whale', 'penny_stocks']
    period_start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    def get_curve_for_strategy(conn, stype):
        if stype == 'penny_stocks':
            rows = conn.execute("""
                SELECT date(sold_at) as date, SUM(pnl_cents) as daily_pnl
                FROM penny_virtual_trades_history
                WHERE sold_at >= ?
                GROUP BY date(sold_at)
                ORDER BY date(sold_at) ASC
            """, (period_start,)).fetchall()
        else:
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
                'daily_pnl': round(pnl, 4),
                'cumulative_pnl': round(cumulative, 4)
            })
        return curve

    with get_connection() as conn:
        if strategy == 'all':
            return {stype: get_curve_for_strategy(conn, stype) for stype in strategies}
        else:
            return get_curve_for_strategy(conn, strategy)

def get_penny_stocks_dashboard(active_page=1, active_limit=100, resolved_page=1, resolved_limit=100, history_page=1, history_limit=100) -> dict:
    """
    Собирает данные для дашборда Penny Stocks (активные, завершенные позиции, статистика, распределение).
    """
    from agents.shared.python.db import get_connection
    with get_connection() as conn:
        # Подсчет общего количества активных
        active_total = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM penny_stocks_monitoring p
            WHERE p.status = 'ACTIVE' AND (
                (p.predicted_outcome = 'YES' AND p.initial_price <= 0.10) OR
                (p.predicted_outcome = 'NO' AND p.initial_price >= 0.90) OR
                (p.predicted_outcome IS NULL AND (p.initial_price <= 0.10 OR p.initial_price >= 0.90))
            )
        """).fetchone()['cnt']

        # Активные позиции (с прогнозом и без) с пагинацией
        active_offset = (active_page - 1) * active_limit
        active_rows = conn.execute("""
            SELECT p.market_id, p.title, p.url, p.initial_price, p.current_price, p.max_price_seen, p.min_price_seen, p.volume_2h, p.predicted_outcome, p.edge, p.confidence, p.added_at, p.virtual_bought_price,
                   (am.market_id IS NOT NULL) as is_analyzed
            FROM penny_stocks_monitoring p
            LEFT JOIN analyzed_markets am ON p.market_id = am.market_id
            WHERE p.status = 'ACTIVE' AND (
                (p.predicted_outcome = 'YES' AND p.initial_price <= 0.10) OR
                (p.predicted_outcome = 'NO' AND p.initial_price >= 0.90) OR
                (p.predicted_outcome IS NULL AND (p.initial_price <= 0.10 OR p.initial_price >= 0.90))
            )
            ORDER BY p.added_at DESC
            LIMIT ? OFFSET ?
        """, (active_limit, active_offset)).fetchall()

        active = []
        for r in active_rows:
            row_dict = dict(r)
            pred = row_dict['predicted_outcome']
            init = row_dict['initial_price']
            curr = row_dict['current_price']
            mx = row_dict['max_price_seen']
            mn = row_dict['min_price_seen']

            # Определяем целевой исход для отображения цен
            if pred is not None:
                outcome_to_track = pred
            else:
                outcome_to_track = 'NO' if (init is not None and init >= 0.90) else 'YES'

            row_dict['cheap_outcome'] = outcome_to_track

            if outcome_to_track == 'NO':
                row_dict['initial_price_outcome'] = round(1.0 - init, 4) if init is not None else None
                row_dict['current_price_outcome'] = round(1.0 - curr, 4) if curr is not None else None
                row_dict['max_price_seen_outcome'] = round(1.0 - mn, 4) if mn is not None else None
                row_dict['min_price_seen_outcome'] = round(1.0 - mx, 4) if mx is not None else None
            else:
                row_dict['initial_price_outcome'] = init
                row_dict['current_price_outcome'] = curr
                row_dict['max_price_seen_outcome'] = mx
                row_dict['min_price_seen_outcome'] = mn
            active.append(row_dict)

        # Подсчет общего количества завершенных
        resolved_total = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM penny_stocks_monitoring p
            WHERE p.status = 'RESOLVED' AND (
                (p.predicted_outcome = 'YES' AND p.initial_price <= 0.10) OR
                (p.predicted_outcome = 'NO' AND p.initial_price >= 0.90) OR
                (p.predicted_outcome IS NULL AND (p.initial_price <= 0.10 OR p.initial_price >= 0.90))
            )
        """).fetchone()['cnt']

        # Завершенные позиции (все дешевые, с прогнозом и без) с пагинацией
        resolved_offset = (resolved_page - 1) * resolved_limit
        resolved_rows = conn.execute("""
            SELECT p.market_id, p.title, p.url, p.initial_price, p.current_price, p.max_price_seen, p.min_price_seen, p.predicted_outcome, p.actual_outcome, p.edge, p.confidence, p.resolved_at,
                   h.pnl_cents as pnl_realized
            FROM penny_stocks_monitoring p
            LEFT JOIN (
                SELECT market_id, SUM(pnl_cents) as pnl_cents
                FROM penny_virtual_trades_history
                GROUP BY market_id
            ) h ON p.market_id = h.market_id
            WHERE p.status = 'RESOLVED' AND (
                (p.predicted_outcome = 'YES' AND p.initial_price <= 0.10) OR
                (p.predicted_outcome = 'NO' AND p.initial_price >= 0.90) OR
                (p.predicted_outcome IS NULL AND (p.initial_price <= 0.10 OR p.initial_price >= 0.90))
            )
            ORDER BY p.resolved_at DESC
            LIMIT ? OFFSET ?
        """, (resolved_limit, resolved_offset)).fetchall()

        resolved = []
        for r in resolved_rows:
            row_dict = dict(r)
            pred = row_dict['predicted_outcome']
            init = row_dict['initial_price']
            curr = row_dict['current_price']
            mx = row_dict['max_price_seen']
            mn = row_dict['min_price_seen']

            # Определяем направление
            if pred is not None:
                outcome_to_track = pred
            else:
                outcome_to_track = 'NO' if (init is not None and init >= 0.90) else 'YES'

            row_dict['cheap_outcome'] = outcome_to_track

            if outcome_to_track == 'NO':
                row_dict['initial_price_outcome'] = round(1.0 - init, 4) if init is not None else None
                row_dict['current_price_outcome'] = round(1.0 - curr, 4) if curr is not None else None
                row_dict['max_price_seen_outcome'] = round(1.0 - mn, 4) if mn is not None else None
                row_dict['min_price_seen_outcome'] = round(1.0 - mx, 4) if mx is not None else None
            else:
                row_dict['initial_price_outcome'] = init
                row_dict['current_price_outcome'] = curr
                row_dict['max_price_seen_outcome'] = mx
                row_dict['min_price_seen_outcome'] = mn

            # Гипотетический PNL, если реальной сделки не было, но рынок разрешен
            actual = row_dict['actual_outcome']
            pnl_realized = row_dict['pnl_realized']
            if pnl_realized is None and actual is not None and init is not None and outcome_to_track is not None:
                bought_outcome = (1.0 - init) if outcome_to_track == 'NO' else init
                sold_outcome = 1.0 if actual == outcome_to_track else 0.0
                row_dict['pnl_realized'] = round(sold_outcome - bought_outcome, 4)

            resolved.append(row_dict)

        # Виртуальный портфель
        portfolio_rows = conn.execute("""
            SELECT market_id, title, url, initial_price, current_price, predicted_outcome, edge, confidence, virtual_bought_price, virtual_bought_at
            FROM penny_stocks_monitoring
            WHERE status = 'ACTIVE' AND virtual_bought_price IS NOT NULL
            ORDER BY virtual_bought_at DESC
        """).fetchall()

        portfolio = []
        for r in portfolio_rows:
            row_dict = dict(r)
            pred = row_dict['predicted_outcome']
            init = row_dict['initial_price']
            v_bought = row_dict['virtual_bought_price']
            v_curr = row_dict['current_price']

            # Определяем направление сделки
            if pred is not None:
                outcome_to_track = pred
            else:
                outcome_to_track = 'NO' if (init is not None and init >= 0.90) else 'YES'

            row_dict['cheap_outcome'] = outcome_to_track

            # Учитываем направление ставки
            if outcome_to_track == 'NO':
                bought_outcome = 1.0 - v_bought if v_bought is not None else None
                curr_outcome = 1.0 - v_curr if v_curr is not None else None
            else:  # YES
                bought_outcome = v_bought
                curr_outcome = v_curr

            pnl_cents = curr_outcome - bought_outcome if (curr_outcome is not None and bought_outcome is not None) else 0.0
            pnl_percent = (pnl_cents / bought_outcome * 100) if (bought_outcome is not None and bought_outcome > 0) else 0.0

            row_dict['bought_outcome_price'] = round(bought_outcome, 4) if bought_outcome is not None else None
            row_dict['current_outcome_price'] = round(curr_outcome, 4) if curr_outcome is not None else None
            row_dict['pnl_cents'] = round(pnl_cents, 4)
            row_dict['pnl_percent'] = round(pnl_percent, 2)

            portfolio.append(row_dict)

        # Статистика
        total_active = active_total
        total_resolved = resolved_total

        # Статистика по истории виртуальных сделок
        stats_row = conn.execute("""
            SELECT
                COUNT(*) as count,
                SUM(CASE WHEN pnl_cents > 0 THEN 1 ELSE 0 END) as wins,
                MAX(pnl_cents) as best_pnl,
                AVG(pnl_cents) as avg_pnl
            FROM penny_virtual_trades_history
            WHERE sold_at >= datetime('now', '-30 days')
        """).fetchone()

        win_rate = None
        best_pnl = 0.0
        avg_pnl = 0.0
        if stats_row and stats_row['count'] and stats_row['count'] > 0:
            win_rate = stats_row['wins'] / stats_row['count']
            best_pnl = stats_row['best_pnl'] or 0.0
            avg_pnl = stats_row['avg_pnl'] or 0.0

        avg_entry_row = conn.execute("""
            SELECT AVG(
                CASE
                    WHEN predicted_outcome = 'NO' THEN 1.0 - initial_price
                    ELSE initial_price
                END
            ) as avg_entry
            FROM penny_stocks_monitoring
            WHERE status = 'ACTIVE' AND (
                (predicted_outcome = 'YES' AND initial_price <= 0.10) OR
                (predicted_outcome = 'NO' AND initial_price >= 0.90) OR
                (predicted_outcome IS NULL AND (initial_price <= 0.10 OR initial_price >= 0.90))
            )
        """).fetchone()
        avg_entry = avg_entry_row['avg_entry'] if avg_entry_row else None

        total_active_predicted = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM penny_stocks_monitoring
            WHERE status = 'ACTIVE' AND predicted_outcome IS NOT NULL AND (
                (predicted_outcome = 'YES' AND initial_price <= 0.10) OR
                (predicted_outcome = 'NO' AND initial_price >= 0.90)
            )
        """).fetchone()['cnt']

        # 1. Кумулятивный PnL по истории виртуальных сделок
        trades_pnl_row = conn.execute("SELECT SUM(pnl_cents) as total_pnl FROM penny_virtual_trades_history").fetchone()
        total_trades_pnl = trades_pnl_row['total_pnl'] if trades_pnl_row and trades_pnl_row['total_pnl'] is not None else 0.0

        # 2. Кумулятивный PnL по всем закрытым (RESOLVED) penny-рынкам (реальный + гипотетический)
        all_resolved_rows = conn.execute("""
            SELECT p.initial_price, p.predicted_outcome, p.actual_outcome, h.pnl_cents as pnl_realized
            FROM penny_stocks_monitoring p
            LEFT JOIN (
                SELECT market_id, SUM(pnl_cents) as pnl_cents
                FROM penny_virtual_trades_history
                GROUP BY market_id
            ) h ON p.market_id = h.market_id
            WHERE p.status = 'RESOLVED' AND (
                (p.predicted_outcome = 'YES' AND p.initial_price <= 0.10) OR
                (p.predicted_outcome = 'NO' AND p.initial_price >= 0.90) OR
                (p.predicted_outcome IS NULL AND (p.initial_price <= 0.10 OR p.initial_price >= 0.90))
            )
        """).fetchall()

        total_resolved_pnl = 0.0
        for r in all_resolved_rows:
            pnl_realized = r['pnl_realized']
            if pnl_realized is not None:
                total_resolved_pnl += pnl_realized
            else:
                actual = r['actual_outcome']
                init = r['initial_price']
                pred = r['predicted_outcome']
                if actual is not None and init is not None:
                    if pred is not None:
                        outcome_to_track = pred
                    else:
                        outcome_to_track = 'NO' if init >= 0.90 else 'YES'
                    
                    bought_outcome = (1.0 - init) if outcome_to_track == 'NO' else init
                    sold_outcome = 1.0 if actual == outcome_to_track else 0.0
                    total_resolved_pnl += (sold_outcome - bought_outcome)

        stats = {
            'active_count': total_active,
            'active_predicted_count': total_active_predicted,
            'resolved_count': total_resolved,
            'win_rate': win_rate,
            'avg_entry_price': avg_entry,
            'best_trade_pnl': best_pnl,
            'avg_pnl': avg_pnl,
            'total_trades_pnl': round(total_trades_pnl, 4),
            'total_resolved_pnl': round(total_resolved_pnl, 4)
        }

        # Распределение цен входа
        all_prices_rows = conn.execute("""
            SELECT
                CASE
                    WHEN predicted_outcome = 'NO' THEN 1.0 - initial_price
                    ELSE initial_price
                END as outcome_initial_price
            FROM penny_stocks_monitoring
            WHERE status = 'ACTIVE' AND (
                (predicted_outcome = 'YES' AND initial_price <= 0.10) OR
                (predicted_outcome = 'NO' AND initial_price >= 0.90) OR
                (predicted_outcome IS NULL AND (initial_price <= 0.10 OR initial_price >= 0.90))
            )
        """).fetchall()
        bins = {
            '1-5¢': 0,
            '5-10¢': 0,
            '10-15¢': 0,
            '15-20¢': 0,
            '20+¢': 0
        }
        for r in all_prices_rows:
            p = r['outcome_initial_price']
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

        # Подсчет общего количества истории виртуальных сделок
        history_total = conn.execute("SELECT COUNT(*) as cnt FROM penny_virtual_trades_history").fetchone()['cnt']

        # История виртуальных сделок с пагинацией
        history_offset = (history_page - 1) * history_limit
        history_rows = conn.execute("""
            SELECT 
                h.id, 
                h.market_id, 
                h.title, 
                h.url, 
                h.outcome, 
                h.bought_price, 
                h.bought_outcome_price, 
                h.sold_price, 
                h.sold_outcome_price, 
                h.pnl_cents, 
                h.pnl_percent, 
                h.bought_at, 
                h.sold_at, 
                h.max_price_seen, 
                h.min_price_seen,
                m.current_price,
                m.status as market_status,
                m.actual_outcome
            FROM penny_virtual_trades_history h
            LEFT JOIN (
                SELECT market_id, current_price, status, actual_outcome
                FROM penny_stocks_monitoring
                GROUP BY market_id
            ) m ON h.market_id = m.market_id
            ORDER BY h.sold_at DESC
            LIMIT ? OFFSET ?
        """, (history_limit, history_offset)).fetchall()
        
        virtual_history = []
        for r in history_rows:
            row_dict = dict(r)
            outcome = row_dict['outcome']
            mx = row_dict['max_price_seen']
            mn = row_dict['min_price_seen']
            if mx is not None and mn is not None:
                if outcome == 'NO':
                    row_dict['max_price_seen_outcome'] = round(1.0 - mn, 4)
                    row_dict['min_price_seen_outcome'] = round(1.0 - mx, 4)
                else:
                    row_dict['max_price_seen_outcome'] = mx
                    row_dict['min_price_seen_outcome'] = mn
            else:
                row_dict['max_price_seen_outcome'] = None
                row_dict['min_price_seen_outcome'] = None

            # Расчет текущей стоимости исхода сделки
            outcome = row_dict['outcome']
            curr = row_dict['current_price']
            status = row_dict['market_status']
            actual_outcome = row_dict['actual_outcome']
            current_outcome_price = None

            if status == 'ACTIVE' and curr is not None:
                if outcome == 'NO':
                    current_outcome_price = round(1.0 - curr, 4)
                else:
                    current_outcome_price = curr
            elif status == 'RESOLVED':
                if actual_outcome is not None:
                    current_outcome_price = 1.0 if actual_outcome == outcome else 0.0

            row_dict['current_outcome_price'] = current_outcome_price
            virtual_history.append(row_dict)

        # Последние проанализированные рынки для системных оповещений
        system_alerts = []
        try:
            alerts_rows = conn.execute("""
                SELECT am.market_id, am.analyzed_at, COALESCE(p.title, m.title, am.market_id) as title
                FROM analyzed_markets am
                LEFT JOIN penny_stocks_monitoring p ON am.market_id = p.market_id
                LEFT JOIN markets m ON am.market_id = m.id
                ORDER BY am.analyzed_at DESC
                LIMIT 30
            """).fetchall()
            for r in alerts_rows:
                system_alerts.append({
                    'market_id': r['market_id'],
                    'analyzed_at': r['analyzed_at'],
                    'title': r['title']
                })
        except Exception as e:
            logger.warning(f"[DataProvider] analyzed_markets недоступна: {e}")

    return {
        'active': active,
        'resolved': resolved,
        'portfolio': portfolio,
        'virtual_history': virtual_history,
        'stats': stats,
        'price_distribution': bins,
        'active_total': active_total,
        'resolved_total': resolved_total,
        'history_total': history_total,
        'system_alerts': system_alerts
    }

def get_strategy_signals(strategy: str, days: Optional[int] = 30, limit: int = 50, page: Optional[int] = None) -> Any:
    """Возвращает последние сигналы для конкретной стратегии вместе с названием рынков."""
    import json
    from agents.shared.python.db import get_connection
    
    # 1. Формируем условия фильтрации
    where_clauses = ["s.strategy_type = ?"]
    params = [strategy]
    
    if days is not None:
        period_start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        where_clauses.append("s.created_at >= ?")
        params.append(period_start)
        
    where_str = " AND ".join(where_clauses)
    
    with get_connection() as conn:
        # Сначала посчитаем total, если используется пагинация
        total_count = 0
        if page is not None:
            count_query = f"""
                SELECT COUNT(*) as cnt
                FROM signals s
                JOIN markets m ON s.market_id = m.id
                WHERE {where_str}
            """
            total_count = conn.execute(count_query, params).fetchone()['cnt']
            
        # Теперь делаем выборку сигналов
        query = f"""
            SELECT s.id, s.created_at, s.status, s.edge, s.confidence, s.pnl_realized, s.target_outcome,
                   s.estimated_probability, s.predicted_probability, s.market_price_at_signal,
                   s.details,
                   m.title as market_title, m.url as market_url
            FROM signals s
            JOIN markets m ON s.market_id = m.id
            WHERE {where_str}
            ORDER BY s.created_at DESC
        """
        
        if page is not None:
            offset = (page - 1) * limit
            query += " LIMIT ? OFFSET ?"
            query_params = params + [limit, offset]
        else:
            query += " LIMIT ?"
            query_params = params + [limit]
            
        rows = conn.execute(query, query_params).fetchall()
        
        signals = []
        for r in rows:
            row_dict = dict(r)
            details_str = row_dict.get('details')
            wallet_address = None
            if details_str:
                try:
                    meta = json.loads(details_str)
                    wallet_address = meta.get('wallet_address')
                except Exception:
                    pass
            row_dict['wallet_address'] = wallet_address
            if 'details' in row_dict:
                del row_dict['details']
            signals.append(row_dict)
            
        if page is not None:
            # Считаем brier_score и avg_edge по ВСЕМ сигналам данной стратегии
            stats_query = """
                SELECT 
                    s.estimated_probability, s.predicted_probability, s.status, s.edge
                FROM signals s
                WHERE s.strategy_type = ?
            """
            stats_params = [strategy]
            if days is not None:
                stats_query += " AND s.created_at >= ?"
                stats_params.append(period_start)
                
            stats_rows = conn.execute(stats_query, stats_params).fetchall()
            
            brier_sum = 0.0
            brier_count = 0
            edge_sum = 0.0
            edge_count = 0
            
            for sr in stats_rows:
                p = sr['estimated_probability'] if sr['estimated_probability'] is not None else sr['predicted_probability']
                status = sr['status']
                edge = sr['edge']
                
                if p is not None and status in ('WIN', 'LOSS'):
                    outcome = 1.0 if status == 'WIN' else 0.0
                    brier_sum += (p - outcome) ** 2
                    brier_count += 1
                    
                if edge is not None:
                    edge_sum += edge
                    edge_count += 1
                    
            avg_brier = brier_sum / brier_count if brier_count > 0 else None
            avg_edge = edge_sum / edge_count if edge_count > 0 else None
            
            total_stats = {
                "brier_score": avg_brier,
                "avg_edge": avg_edge
            }
            
            return {
                "signals": signals,
                "total": total_count,
                "stats": total_stats
            }
            
        return signals

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

def get_corridors_dashboard(synthetic_page=1, synthetic_limit=50, temporal_page=1, temporal_limit=50, cross_page=1, cross_limit=50) -> dict:
    """Собирает лог коридоров (синтетические, временные, кросс-платформенные) и KPI по ним."""
    from agents.shared.python.db import get_connection
    with get_connection() as conn:
        # Подсчет общего количества
        synthetic_total = conn.execute("SELECT COUNT(*) as cnt FROM synthetic_corridors WHERE (status != 'DELETED' OR status IS NULL)").fetchone()['cnt']
        temporal_total = conn.execute("SELECT COUNT(*) as cnt FROM temporal_corridors WHERE (status != 'DELETED' OR status IS NULL)").fetchone()['cnt']
        cross_total = conn.execute("SELECT COUNT(*) as cnt FROM cross_arbitrage_signals WHERE has_arbitrage = 1 AND (status != 'deleted' OR status IS NULL)").fetchone()['cnt']

        # Синтетические коридоры
        synth_offset = (synthetic_page - 1) * synthetic_limit
        synth_rows = conn.execute("""
            SELECT signal_id, event_title, event_url, lower_level, lower_price_yes, upper_level, upper_price_yes, theoretical_cost, theoretical_spread_pct, real_cost, real_spread_pct, total_invested_usd, pnl_in_corridor_usd, roi_min_pct, roi_max_pct, created_at
            FROM synthetic_corridors
            WHERE (status != 'DELETED' OR status IS NULL)
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (synthetic_limit, synth_offset)).fetchall()
        synthetic = [dict(r) for r in synth_rows]

        # Временные коридоры
        temp_offset = (temporal_page - 1) * temporal_limit
        temp_rows = conn.execute("""
            SELECT id, signal_id, event_title, event_url, theoretical_cost, theoretical_spread_pct, real_cost, real_spread_pct, early_stake_usd, late_stake_usd, ev_usd, roi_pct, status, created_at
            FROM temporal_corridors
            WHERE (status != 'DELETED' OR status IS NULL)
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (temporal_limit, temp_offset)).fetchall()
        temporal = [dict(r) for r in temp_rows]

        # Кросс-платформа (только с подтвержденным арбитражем)
        cross_offset = (cross_page - 1) * cross_limit
        cross_rows = conn.execute("""
            SELECT id, market_a_title, market_a_platform, market_a_price, market_b_title, market_b_platform, market_b_price, spread_percent, arbitrage_type, reasoning, status, created_at, action_a, action_b, entry_price_a_cents, entry_price_b_cents, expected_pnl_pct, risk_level, has_arbitrage, trade_instruction
            FROM cross_arbitrage_signals
            WHERE has_arbitrage = 1 AND (status != 'deleted' OR status IS NULL)
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (cross_limit, cross_offset)).fetchall()
        cross = [dict(r) for r in cross_rows]

        # Диагностическая выборка (последние 10 записей без фильтрации для отладки)
        diag_rows = conn.execute("""
            SELECT id, market_a_title, market_a_platform, market_a_price, market_b_title, market_b_platform, market_b_price, spread_percent, arbitrage_type, reasoning, status, created_at, action_a, action_b, entry_price_a_cents, entry_price_b_cents, expected_pnl_pct, risk_level, has_arbitrage, trade_instruction
            FROM cross_arbitrage_signals
            ORDER BY created_at DESC
            LIMIT 10
        """).fetchall()
        cross_diagnostics = [dict(r) for r in diag_rows]

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
        'cross_diagnostics': cross_diagnostics,
        'kpis': kpis,
        'synthetic_total': synthetic_total,
        'temporal_total': temporal_total,
        'cross_total': cross_total
    }
