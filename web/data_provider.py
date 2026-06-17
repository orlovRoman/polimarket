# web/data_provider.py
import sqlite3
import math
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Any

logger = logging.getLogger("NexusPolyBot.DataProvider")

def clean_db_url(url: str) -> str:
    if not url:
        return url
    return url.replace("polymarket.com/event/", "polymarket.com/market/")

from agents.shared.python.utils import _parse_dt_utc

def get_global_virtual_stake(conn) -> float:
    """Считывает глобальную ставку из БД с фоллбеком на конфиг."""
    try:
        row = conn.execute("SELECT value FROM memory WHERE key = 'global_virtual_stake'").fetchone()
        if row and row['value']:
            return float(row['value'])
    except Exception:
        pass
    try:
        from core import config_provider
        return float(config_provider.get_sync("eval.virtual_stake_usd", default=10.0))
    except Exception:
        return 10.0

def get_status_emoji(sharpe: float | None, win_rate: float | None) -> str:
    """Определяет статус-эмодзи стратегии на основе Sharpe и win rate."""
    if sharpe is not None and sharpe < 0:
        return "🔴"
    if win_rate is not None and win_rate > 0.55:
        return "🟢"
    return "🟡"

STRATEGY_ALIASES = {
    'favourite_compound': 'favourite_compounding',
    'compound_parlay': 'compound_parlays',
    'temporal': 'temporal_corridor',
    'synthetic': 'synthetic_corridor',
    'cross': 'cross_platform',
}

def normalize_strategy_name(name: str) -> str:
    if not name:
        return ""
    name = name.lower().strip()
    return STRATEGY_ALIASES.get(name, name)

def _load_strategy_metrics(conn, stats):
    """
    Внутренний хелпер для get_overview_stats. Должен вызываться только
    внутри одной транзакции (разделяя общее соединение conn).
    """
    rows = conn.execute("""
        SELECT strategy_type, win_rate, sharpe_ratio, total_signals
        FROM strategy_metrics
        WHERE id IN (SELECT MAX(id) FROM strategy_metrics GROUP BY strategy_type)
    """).fetchall()
    for r in rows:
        stype = normalize_strategy_name(r['strategy_type'])
        if stype in stats:
            stats[stype]['win_rate'] = r['win_rate']
            stats[stype]['sharpe'] = r['sharpe_ratio']
            stats[stype]['signals_count'] = r['total_signals'] or 0

def _load_signals_pnl(conn, stats):
    """
    Внутренний хелпер для get_overview_stats. Должен вызываться только
    внутри одной транзакции (разделяя общее соединение conn).
    """
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
        stype = normalize_strategy_name(r['strategy_type'])
        if stype in stats:
            new_pnl_7d = round(r['pnl_7d'] or 0.0, 2)
            new_pnl_30d = round(r['pnl_30d'] or 0.0, 2)
            if new_pnl_7d != 0.0 or stats[stype]['pnl_7d'] == 0.0:
                stats[stype]['pnl_7d'] = new_pnl_7d
            if new_pnl_30d != 0.0 or stats[stype]['pnl_30d'] == 0.0:
                stats[stype]['pnl_30d'] = new_pnl_30d
            current = stats[stype].get('signals_count') or 0
            stats[stype]['signals_count'] = max(current, r['total'] or 0)

def _load_penny_stocks_stats(conn, stats, virtual_stake):
    """
    Внутренний хелпер для get_overview_stats. Должен вызываться только
    внутри одной транзакции (разделяя общее соединение conn).
    """
    penny_rows = conn.execute("""
        SELECT
            predicted_outcome, actual_outcome, initial_price, resolved_at
        FROM penny_stocks_monitoring
        WHERE status = 'RESOLVED' AND predicted_outcome IS NOT NULL
    """).fetchall()

    total = len(penny_rows)
    total_valid = 0
    total_wins = 0
    pnl_7d = 0.0
    pnl_30d = 0.0
    
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)
    
    for r in penny_rows:
        pred = r['predicted_outcome']
        act = r['actual_outcome']
        init_price = r['initial_price']
        res_at_str = r['resolved_at']
        
        if not pred or not act:
            continue
        
        try:
            res_at = _parse_dt_utc(res_at_str)
            if res_at is None:
                continue
        except Exception:
            continue

        pred_up = pred.upper()
        act_up = act.upper()

        buy_price = init_price if pred_up == 'YES' else (1.0 - init_price)
        if not (0.001 < buy_price < 0.999):
            continue
            
        is_win = (pred_up == act_up)
        total_valid += 1
        if is_win:
            total_wins += 1
        
        if is_win:
            pnl = (virtual_stake / buy_price) * (1.0 - buy_price)
        else:
            pnl = -virtual_stake
            
        if res_at >= seven_days_ago:
            pnl_7d += pnl
            
        if res_at >= thirty_days_ago:
            pnl_30d += pnl

    stats['penny_stocks']['pnl_7d'] = round(pnl_7d, 2)
    stats['penny_stocks']['pnl_30d'] = round(pnl_30d, 2)
    stats['penny_stocks']['signals_count'] = max(stats['penny_stocks']['signals_count'], total)
    if total_valid > 0:
        stats['penny_stocks']['win_rate'] = total_wins / total_valid

def _load_whale_stats(conn, stats, virtual_stake):
    """
    Внутренний хелпер для get_overview_stats. Должен вызываться только
    внутри одной транзакции (разделяя общее соединение conn).
    """
    try:
        rows = conn.execute("""
            SELECT 
                p.initial_price, 
                p.predicted_outcome, 
                p.actual_outcome, 
                p.resolved_at,
                h.pnl_realized,
                h.bought_outcome_price
            FROM whale_stocks_monitoring p
            LEFT JOIN (
                SELECT market_id, SUM(pnl_cents) as pnl_realized, AVG(bought_outcome_price) as bought_outcome_price
                FROM whale_virtual_trades_history
                GROUP BY market_id
            ) h ON p.market_id = h.market_id
            WHERE p.status = 'RESOLVED'
        """).fetchall()

        pnl_7d = 0.0
        pnl_30d = 0.0
        total = len(rows)

        now = datetime.now(timezone.utc)
        seven_days_ago = now - timedelta(days=7)
        thirty_days_ago = now - timedelta(days=30)
        
        thirty_days_rows = 0
        wins_30d = 0

        for r in rows:
            pnl_realized = r['pnl_realized']
            bought_price = r['bought_outcome_price']
            pnl_val = 0.0
            
            actual = r['actual_outcome']
            init = r['initial_price']
            pred = r['predicted_outcome']
            resolved_at_str = r['resolved_at']

            if not pred or not actual:
                continue

            pred_up = pred.upper()
            act_up = actual.upper()
            is_win = (pred_up == act_up)

            if pnl_realized is not None and bought_price and 0.0 < bought_price < 1.0:
                pnl_val = (virtual_stake / bought_price) * pnl_realized
            elif init is not None:
                bought_outcome = (1.0 - init) if pred_up == 'NO' else init
                sold_outcome = 1.0 if is_win else 0.0
                if bought_outcome > 0:
                    pnl_val = (virtual_stake / bought_outcome) * (sold_outcome - bought_outcome)

            try:
                res_at = _parse_dt_utc(resolved_at_str)
                if res_at is None:
                    continue
            except Exception:
                continue

            if res_at >= seven_days_ago:
                pnl_7d += pnl_val
            if res_at >= thirty_days_ago:
                pnl_30d += pnl_val
                thirty_days_rows += 1
                if is_win:
                    wins_30d += 1

        stats['whale']['pnl_7d'] = round(pnl_7d, 2)
        stats['whale']['pnl_30d'] = round(pnl_30d, 2)
        stats['whale']['signals_count'] = max(stats['whale']['signals_count'], total)
        if thirty_days_rows > 0:
            stats['whale']['win_rate'] = wins_30d / thirty_days_rows

    except Exception as e:
        logger.warning(f"[Overview] Ошибка при загрузке статистики китов: {e}", exc_info=True)

def _load_compounding_stats(conn, stats, virtual_stake):
    """
    Внутренний хелпер для get_overview_stats. Должен вызываться только
    внутри одной транзакции (разделяя общее соединение conn).
    """
    try:
        # 1. Ручной портфель
        comp_pnl = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN sold_at >= datetime('now', '-7 days') THEN pnl_usd ELSE 0.0 END) as pnl_7d,
                SUM(CASE WHEN sold_at >= datetime('now', '-30 days') THEN pnl_usd ELSE 0.0 END) as pnl_30d
            FROM compound_virtual_trades_history
        """).fetchone()
        
        comp_wr = conn.execute("""
            SELECT 
                COUNT(*) as total_30d,
                SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins_30d
            FROM compound_virtual_trades_history
            WHERE sold_at >= datetime('now', '-30 days')
        """).fetchone()

        manual_pnl_7d = comp_pnl['pnl_7d'] or 0.0 if comp_pnl else 0.0
        manual_pnl_30d = comp_pnl['pnl_30d'] or 0.0 if comp_pnl else 0.0
        manual_total = comp_pnl['total'] or 0 if comp_pnl else 0
        manual_total_30d = comp_wr['total_30d'] or 0 if comp_wr else 0
        manual_wins_30d = comp_wr['wins_30d'] or 0 if comp_wr else 0

        # 2. Авто-сигналы
        auto_rows = conn.execute("""
            SELECT price, outcome, actual_outcome, resolved_at, pnl_usd
            FROM compound_opportunities
            WHERE status = 'RESOLVED' AND market_id NOT IN (
                SELECT DISTINCT market_id FROM compound_virtual_trades_history
            )
        """).fetchall()

        auto_total = len(auto_rows)
        auto_pnl_7d = 0.0
        auto_pnl_30d = 0.0
        auto_total_30d = 0
        auto_wins_30d = 0

        now = datetime.now(timezone.utc)
        seven_days_ago = now - timedelta(days=7)
        thirty_days_ago = now - timedelta(days=30)

        for r in auto_rows:
            price = r['price']
            outcome = r['outcome']
            actual = r['actual_outcome']
            resolved_at_str = r['resolved_at']
            pnl_db = r['pnl_usd']

            try:
                res_at = _parse_dt_utc(resolved_at_str)
                if res_at is None:
                    continue
            except Exception:
                continue

            if price is not None and outcome is not None and actual is not None and price > 0:
                is_win = (actual.upper() == outcome.upper())
                
                if pnl_db is not None:
                    pnl_val = pnl_db
                else:
                    pnl_val = (virtual_stake / price) * (1.0 - price) * 0.98 if is_win else -virtual_stake

                if res_at >= seven_days_ago:
                    auto_pnl_7d += pnl_val
                if res_at >= thirty_days_ago:
                    auto_pnl_30d += pnl_val
                    auto_total_30d += 1
                    if is_win:
                        auto_wins_30d += 1

        total_signals = manual_total + auto_total
        pnl_7d = manual_pnl_7d + auto_pnl_7d
        pnl_30d = manual_pnl_30d + auto_pnl_30d
        total_30d = manual_total_30d + auto_total_30d
        wins_30d = manual_wins_30d + auto_wins_30d

        stats['favourite_compounding']['pnl_7d'] = round(pnl_7d, 2)
        stats['favourite_compounding']['pnl_30d'] = round(pnl_30d, 2)
        stats['favourite_compounding']['signals_count'] = max(stats['favourite_compounding']['signals_count'], total_signals)
        
        if total_30d > 0:
            stats['favourite_compounding']['win_rate'] = wins_30d / total_30d

    except Exception as e:
        logger.warning(f"[Overview] Ошибка при расчете статистики favourite_compounding: {e}", exc_info=True)

    try:
        parlays_pnl = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status IN ('COMPLETED', 'FAILED') AND updated_at >= datetime('now', '-7 days') THEN
                    CASE WHEN status = 'COMPLETED' THEN current_stake - initial_stake ELSE -initial_stake END
                    ELSE 0.0 END) as pnl_7d,
                SUM(CASE WHEN status IN ('COMPLETED', 'FAILED') AND updated_at >= datetime('now', '-30 days') THEN
                    CASE WHEN status = 'COMPLETED' THEN current_stake - initial_stake ELSE -initial_stake END
                    ELSE 0.0 END) as pnl_30d
            FROM compound_chains
            WHERE status IN ('COMPLETED', 'FAILED')
        """).fetchone()

        if parlays_pnl:
            stats['compound_parlays']['pnl_7d'] = round(parlays_pnl['pnl_7d'] or 0.0, 2)
            stats['compound_parlays']['pnl_30d'] = round(parlays_pnl['pnl_30d'] or 0.0, 2)
            stats['compound_parlays']['signals_count'] = max(stats['compound_parlays']['signals_count'], parlays_pnl['total'] or 0)

        parlays_wr = conn.execute("""
            SELECT 
                COUNT(*) as total_30d,
                SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) as wins_30d
            FROM compound_chains
            WHERE status IN ('COMPLETED', 'FAILED') AND updated_at >= datetime('now', '-30 days')
        """).fetchone()
        if parlays_wr and parlays_wr['total_30d'] is not None and parlays_wr['total_30d'] > 0:
            stats['compound_parlays']['win_rate'] = (parlays_wr['wins_30d'] or 0) / parlays_wr['total_30d']
    except Exception as e:
        logger.warning(f"[Overview] compound_chains недоступна: {e}")

def get_overview_stats() -> dict:
    """
    Агрегирует общие метрики по всем стратегиям:
    - win_rate и sharpe из последнего расчета в strategy_metrics
    - pnl за 7 и 30 дней, а также количество сигналов из signals
    """
    from agents.shared.python.db import get_connection
    DEFAULT_STRATEGY_STATS = {
        'win_rate': None,
        'pnl_7d': 0.0,
        'pnl_30d': 0.0,
        'sharpe': None,
        'signals_count': 0,
        'status_emoji': '🟡'
    }
    
    strategies = [
        'scout', 'synthetic_corridor', 'temporal_corridor', 'cross_platform', 'whale', 'penny_stocks',
        'favourite_compounding', 'compound_parlays'
    ]
    
    stats = {}
    for s in strategies:
        stats[s] = dict(DEFAULT_STRATEGY_STATS)

    with get_connection() as conn:
        virtual_stake = get_global_virtual_stake(conn)
        _load_strategy_metrics(conn, stats)
        _load_signals_pnl(conn, stats)
        _load_penny_stocks_stats(conn, stats, virtual_stake)
        _load_whale_stats(conn, stats, virtual_stake)
        _load_compounding_stats(conn, stats, virtual_stake)

        # 3. Обновляем статус-эмодзи
        for stype, sdata in stats.items():
            sdata['status_emoji'] = get_status_emoji(sdata['sharpe'], sdata['win_rate'])

    return stats

def get_memory_stats() -> dict:
    """Возвращает статистику использования SQLite memory и RAG."""
    from agents.shared.python.db import get_connection
    stats = {'total_keys': 0, 'expired_keys': 0, 'vault_files': 0}
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM memory")
        stats['total_keys'] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM memory WHERE expires_at IS NOT NULL AND expires_at < datetime('now')")
        stats['expired_keys'] = cursor.fetchone()[0]
        try:
            cursor.execute("SELECT COUNT(*) FROM vault_index")
            stats['vault_files'] = cursor.fetchone()[0]
        except Exception:
            stats['vault_files'] = 0
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
    strategies = ['scout', 'synthetic_corridor', 'temporal_corridor', 'cross_platform', 'whale', 'penny_stocks', 'favourite_compounding', 'compound_parlays']
    period_start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    def get_curve_for_strategy(conn, stype):
        stype = normalize_strategy_name(stype)
        virtual_stake = get_global_virtual_stake(conn)
        if stype == 'penny_stocks':
            rows = conn.execute("""
                SELECT date(resolved_at) as date,
                       SUM(
                           (CASE 
                               WHEN predicted_outcome = 'YES' THEN 
                                   (CASE WHEN actual_outcome = 'YES' THEN 1.0 ELSE 0.0 END) - initial_price
                               WHEN predicted_outcome = 'NO' THEN 
                                   (CASE WHEN actual_outcome = 'NO' THEN 1.0 ELSE 0.0 END) - (1.0 - initial_price)
                               ELSE 0.0
                           END) * (? / NULLIF(CASE 
                               WHEN predicted_outcome = 'NO' THEN 1.0 - initial_price 
                               ELSE initial_price 
                           END, 0))
                       ) as daily_pnl
                FROM penny_stocks_monitoring
                WHERE status = 'RESOLVED' AND predicted_outcome IS NOT NULL AND resolved_at >= ?
                  AND (CASE WHEN predicted_outcome = 'NO' THEN 1.0 - initial_price ELSE initial_price END) > 0
                GROUP BY date(resolved_at)
                ORDER BY date(resolved_at) ASC
            """, (virtual_stake, period_start)).fetchall()
        elif stype == 'whale':
            rows = conn.execute("""
                SELECT date(sold_at) as date, 
                       SUM((COALESCE(pnl_cents, 0.0) / bought_outcome_price) * ?) as daily_pnl
                FROM whale_virtual_trades_history
                WHERE sold_at >= ? AND sold_at IS NOT NULL AND bought_outcome_price > 0
                GROUP BY date(sold_at)
                ORDER BY date(sold_at) ASC
            """, (virtual_stake, period_start)).fetchall()
        elif stype == 'favourite_compounding':
            rows = conn.execute("""
                SELECT date(ts) as date, SUM(pnl) as daily_pnl
                FROM (
                    SELECT sold_at as ts, pnl_usd as pnl
                    FROM compound_virtual_trades_history
                    WHERE sold_at >= ? AND sold_at IS NOT NULL
                    
                    UNION ALL
                    
                    SELECT resolved_at as ts,
                        COALESCE(pnl_usd, 
                            CASE WHEN UPPER(actual_outcome) = UPPER(outcome) THEN
                                (? / price) * (1.0 - price) * 0.98
                            ELSE
                                -?
                            END
                        ) as pnl
                    FROM compound_opportunities
                    WHERE status = 'RESOLVED' AND resolved_at >= ? AND price > 0 AND actual_outcome IS NOT NULL 
                      AND market_id NOT IN (
                        SELECT DISTINCT market_id FROM compound_virtual_trades_history
                    )
                )
                GROUP BY date(ts)
                ORDER BY date(ts) ASC
            """, (period_start, virtual_stake, virtual_stake, period_start)).fetchall()
        elif stype == 'compound_parlays':
            rows = conn.execute("""
                SELECT date(updated_at) as date,
                       SUM(CASE WHEN status = 'COMPLETED' THEN current_stake - initial_stake ELSE -initial_stake END) as daily_pnl
                FROM compound_chains
                WHERE status IN ('COMPLETED', 'FAILED') AND updated_at >= ?
                GROUP BY date(updated_at)
                ORDER BY date(updated_at) ASC
            """, (period_start,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT date(resolved_at) as date, SUM(pnl_realized) as daily_pnl
                FROM signals
                WHERE LOWER(strategy_type) = LOWER(?)
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

def get_penny_stocks_dashboard(active_page=1, active_limit=100, resolved_page=1, resolved_limit=100, history_page=1, history_limit=100, wins_page=1, wins_limit=50, losses_page=1, losses_limit=50) -> dict:
    """
    Собирает данные для дашборда Penny Stocks (активные, завершенные позиции, статистика, распределение).
    """
    from agents.shared.python.db import get_connection
    with get_connection() as conn:
        virtual_stake = get_global_virtual_stake(conn)
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
            if 'url' in row_dict:
                row_dict['url'] = clean_db_url(row_dict['url'])
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

        def _process_resolved_row(r):
            row_dict = dict(r)
            if 'url' in row_dict:
                row_dict['url'] = clean_db_url(row_dict['url'])
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

            # Авто-PnL (сигналы агентов)
            pnl_auto = None
            actual = row_dict['actual_outcome']
            if pred is not None and actual is not None and init is not None:
                pred_up = pred.upper()
                act_up = actual.upper()
                bought_outcome = (1.0 - init) if pred_up == 'NO' else init
                sold_outcome = 1.0 if act_up == pred_up else 0.0
                if bought_outcome > 0:
                    pnl_auto = round((virtual_stake / bought_outcome) * (sold_outcome - bought_outcome), 2)
                else:
                    pnl_auto = 0.0
            row_dict['pnl_auto'] = pnl_auto

            # Гипотетический PNL, если реальной сделки не было, но рынок разрешен
            pnl_realized = row_dict['pnl_realized']
            if pnl_realized is None and actual is not None and init is not None and outcome_to_track is not None:
                track_up = outcome_to_track.upper()
                act_up = actual.upper()
                bought_outcome = (1.0 - init) if track_up == 'NO' else init
                sold_outcome = 1.0 if act_up == track_up else 0.0
                if bought_outcome > 0:
                    row_dict['pnl_realized'] = round((virtual_stake / bought_outcome) * (sold_outcome - bought_outcome), 2)
                else:
                    row_dict['pnl_realized'] = 0.0
            else:
                bought_price = row_dict.get('bought_outcome_price')
                bet_size = row_dict.get('bet_size_usdc') or virtual_stake
                if bought_price and 0.0 < bought_price < 1.0:
                    # pnl_realized is already raw profit/loss (e.g. 0.95 or -1.0)
                    row_dict['pnl_realized'] = round((bet_size / bought_price) * (row_dict['pnl_realized'] or 0.0), 2)
                else:
                    row_dict['pnl_realized'] = 0.0

            return row_dict

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
                   h.pnl_cents as pnl_realized, h.bought_outcome_price as bought_outcome_price, h.bet_size_usdc as bet_size_usdc
            FROM penny_stocks_monitoring p
            LEFT JOIN (
                SELECT market_id, SUM(pnl_cents) as pnl_cents, AVG(bought_outcome_price) as bought_outcome_price, SUM(bet_size_usdc) as bet_size_usdc
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

        resolved = [_process_resolved_row(r) for r in resolved_rows]

        # Подсчет общего количества завершенных выигранных и проигранных (с прогнозом)
        wins_total = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM penny_stocks_monitoring p
            WHERE p.status = 'RESOLVED' AND p.predicted_outcome IS NOT NULL AND (
                (p.predicted_outcome = 'YES' AND p.initial_price <= 0.10) OR
                (p.predicted_outcome = 'NO' AND p.initial_price >= 0.90)
            ) AND p.predicted_outcome = p.actual_outcome
        """).fetchone()['cnt']

        losses_total = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM penny_stocks_monitoring p
            WHERE p.status = 'RESOLVED' AND p.predicted_outcome IS NOT NULL AND (
                (p.predicted_outcome = 'YES' AND p.initial_price <= 0.10) OR
                (p.predicted_outcome = 'NO' AND p.initial_price >= 0.90)
            ) AND p.predicted_outcome != p.actual_outcome
        """).fetchone()['cnt']

        # Выигранные завершенные с пагинацией
        wins_offset = (wins_page - 1) * wins_limit
        wins_rows = conn.execute("""
            SELECT p.market_id, p.title, p.url, p.initial_price, p.current_price, p.max_price_seen, p.min_price_seen, p.predicted_outcome, p.actual_outcome, p.edge, p.confidence, p.resolved_at,
                   h.pnl_cents as pnl_realized, h.bought_outcome_price as bought_outcome_price, h.bet_size_usdc as bet_size_usdc
            FROM penny_stocks_monitoring p
            LEFT JOIN (
                SELECT market_id, SUM(pnl_cents) as pnl_cents, AVG(bought_outcome_price) as bought_outcome_price, SUM(bet_size_usdc) as bet_size_usdc
                FROM penny_virtual_trades_history
                GROUP BY market_id
            ) h ON p.market_id = h.market_id
            WHERE p.status = 'RESOLVED' AND p.predicted_outcome IS NOT NULL AND (
                (p.predicted_outcome = 'YES' AND p.initial_price <= 0.10) OR
                (p.predicted_outcome = 'NO' AND p.initial_price >= 0.90)
            ) AND p.predicted_outcome = p.actual_outcome
            ORDER BY p.resolved_at DESC
            LIMIT ? OFFSET ?
        """, (wins_limit, wins_offset)).fetchall()

        # Проигранные завершенные с пагинацией
        losses_offset = (losses_page - 1) * losses_limit
        losses_rows = conn.execute("""
            SELECT p.market_id, p.title, p.url, p.initial_price, p.current_price, p.max_price_seen, p.min_price_seen, p.predicted_outcome, p.actual_outcome, p.edge, p.confidence, p.resolved_at,
                   h.pnl_cents as pnl_realized, h.bought_outcome_price as bought_outcome_price, h.bet_size_usdc as bet_size_usdc
            FROM penny_stocks_monitoring p
            LEFT JOIN (
                SELECT market_id, SUM(pnl_cents) as pnl_cents, AVG(bought_outcome_price) as bought_outcome_price, SUM(bet_size_usdc) as bet_size_usdc
                FROM penny_virtual_trades_history
                GROUP BY market_id
            ) h ON p.market_id = h.market_id
            WHERE p.status = 'RESOLVED' AND p.predicted_outcome IS NOT NULL AND (
                (p.predicted_outcome = 'YES' AND p.initial_price <= 0.10) OR
                (p.predicted_outcome = 'NO' AND p.initial_price >= 0.90)
            ) AND p.predicted_outcome != p.actual_outcome
            ORDER BY p.resolved_at DESC
            LIMIT ? OFFSET ?
        """, (losses_limit, losses_offset)).fetchall()

        resolved_wins = [_process_resolved_row(r) for r in wins_rows]
        resolved_losses = [_process_resolved_row(r) for r in losses_rows]

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
            if 'url' in row_dict:
                row_dict['url'] = clean_db_url(row_dict['url'])
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
            
            pnl_dollars = (virtual_stake / bought_outcome) * pnl_cents if (bought_outcome is not None and bought_outcome > 0) else 0.0

            row_dict['bought_outcome_price'] = round(bought_outcome, 4) if bought_outcome is not None else None
            row_dict['current_outcome_price'] = round(curr_outcome, 4) if curr_outcome is not None else None
            row_dict['pnl_cents'] = round(pnl_dollars, 2)
            row_dict['pnl_percent'] = round(pnl_percent, 2)

            portfolio.append(row_dict)

        # Статистика
        total_active = active_total
        total_resolved = resolved_total

        # === 1. АВТО-СТАТИСТИКА (СИГНАЛЫ АГЕНТОВ) ===
        # Выбираем завершенные авто-сигналы
        auto_resolved_row = conn.execute("""
            SELECT
                COUNT(*) as count,
                SUM(CASE WHEN 
                    (predicted_outcome = 'YES' AND actual_outcome = 'YES') OR 
                    (predicted_outcome = 'NO' AND actual_outcome = 'NO') THEN 1 ELSE 0 END
                ) as wins,
                MAX(
                    (CASE 
                        WHEN predicted_outcome = 'YES' THEN 1.0 / initial_price - 1.0
                        WHEN predicted_outcome = 'NO' THEN 1.0 / (1.0 - initial_price) - 1.0
                        ELSE 0.0
                    END) * (CASE WHEN actual_outcome = predicted_outcome THEN 1.0 ELSE 0.0 END) -
                    (CASE WHEN actual_outcome != predicted_outcome THEN 1.0 ELSE 0.0 END)
                ) as best_pnl,
                AVG(
                    (CASE 
                        WHEN predicted_outcome = 'YES' THEN 1.0 / initial_price - 1.0
                        WHEN predicted_outcome = 'NO' THEN 1.0 / (1.0 - initial_price) - 1.0
                        ELSE 0.0
                    END) * (CASE WHEN actual_outcome = predicted_outcome THEN 1.0 ELSE 0.0 END) -
                    (CASE WHEN actual_outcome != predicted_outcome THEN 1.0 ELSE 0.0 END)
                ) as avg_pnl,
                SUM(
                    (CASE 
                        WHEN predicted_outcome = 'YES' THEN 1.0 / initial_price - 1.0
                        WHEN predicted_outcome = 'NO' THEN 1.0 / (1.0 - initial_price) - 1.0
                        ELSE 0.0
                    END) * (CASE WHEN actual_outcome = predicted_outcome THEN 1.0 ELSE 0.0 END) -
                    (CASE WHEN actual_outcome != predicted_outcome THEN 1.0 ELSE 0.0 END)
                ) as total_pnl
            FROM penny_stocks_monitoring
            WHERE status = 'RESOLVED' AND predicted_outcome IS NOT NULL
        """).fetchone()

        auto_win_rate = None
        auto_best_pnl = 0.0
        auto_avg_pnl = 0.0
        auto_total_pnl = 0.0
        if auto_resolved_row and auto_resolved_row['count'] > 0:
            auto_win_rate = auto_resolved_row['wins'] / auto_resolved_row['count']
            auto_best_pnl = (auto_resolved_row['best_pnl'] or 0.0) * virtual_stake
            auto_avg_pnl = (auto_resolved_row['avg_pnl'] or 0.0) * virtual_stake
            auto_total_pnl = (auto_resolved_row['total_pnl'] or 0.0) * virtual_stake

        # Средняя цена входа активных авто-сигналов
        avg_entry_auto_row = conn.execute("""
            SELECT AVG(
                CASE
                    WHEN predicted_outcome = 'NO' THEN 1.0 - initial_price
                    ELSE initial_price
                END
            ) as avg_entry
            FROM penny_stocks_monitoring
            WHERE status = 'ACTIVE' AND predicted_outcome IS NOT NULL
        """).fetchone()
        avg_entry_auto = avg_entry_auto_row['avg_entry'] if avg_entry_auto_row else None

        # Расчет суммы выигрышей и проигрышей для авто-сигналов с прогнозом
        all_resolved_auto = conn.execute("""
            SELECT initial_price, predicted_outcome, actual_outcome
            FROM penny_stocks_monitoring
            WHERE status = 'RESOLVED' AND predicted_outcome IS NOT NULL
        """).fetchall()

        sum_won = 0.0
        sum_lost = 0.0
        for r in all_resolved_auto:
            init = r['initial_price']
            pred = r['predicted_outcome']
            actual = r['actual_outcome']
            if init is not None and pred is not None and actual is not None and init > 0 and init < 1.0:
                pred_up = pred.upper()
                act_up = actual.upper()
                bought_outcome = (1.0 - init) if pred_up == 'NO' else init
                sold_outcome = 1.0 if act_up == pred_up else 0.0
                if bought_outcome > 0:
                    pnl_val = (virtual_stake / bought_outcome) * (sold_outcome - bought_outcome)
                else:
                    pnl_val = 0.0
                
                if pnl_val > 0:
                    sum_won += pnl_val
                elif pnl_val < 0:
                    sum_lost += abs(pnl_val)

        # Всего активных с прогнозом
        total_active_predicted = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM penny_stocks_monitoring
            WHERE status = 'ACTIVE' AND predicted_outcome IS NOT NULL
        """).fetchone()['cnt']

        stats = {
            'active_count': total_active,
            'active_predicted_count': total_active_predicted,
            'resolved_count': total_resolved,
            'auto_resolved_count': auto_resolved_row['count'] if auto_resolved_row else 0,
            'win_rate': auto_win_rate,
            'avg_entry_price': avg_entry_auto,
            'best_trade_pnl': auto_best_pnl,
            'avg_pnl': auto_avg_pnl,
            'total_resolved_pnl': round(auto_total_pnl, 4),
            'sum_won': round(sum_won, 2),
            'sum_lost': round(sum_lost, 2)
        }

        # === 2. РУЧНАЯ СТАТИСТИКА (ВИРТУАЛЬНЫЕ СДЕЛКИ) ===
        manual_stats_row = conn.execute("""
            SELECT
                COUNT(*) as count,
                SUM(CASE WHEN pnl_cents > 0 THEN 1 ELSE 0 END) as wins,
                MAX(CASE WHEN bought_outcome_price > 0 THEN pnl_cents / bought_outcome_price ELSE 0.0 END) as best_pnl,
                AVG(CASE WHEN bought_outcome_price > 0 THEN pnl_cents / bought_outcome_price ELSE 0.0 END) as avg_pnl,
                SUM(CASE WHEN bought_outcome_price > 0 THEN pnl_cents / bought_outcome_price ELSE 0.0 END) as total_pnl
            FROM penny_virtual_trades_history
        """).fetchone()

        manual_win_rate = None
        manual_best_pnl = 0.0
        manual_avg_pnl = 0.0
        manual_total_pnl = 0.0
        if manual_stats_row and manual_stats_row['count'] > 0:
            manual_win_rate = manual_stats_row['wins'] / manual_stats_row['count']
            manual_best_pnl = (manual_stats_row['best_pnl'] or 0.0) * virtual_stake
            manual_avg_pnl = (manual_stats_row['avg_pnl'] or 0.0) * virtual_stake
            manual_total_pnl = (manual_stats_row['total_pnl'] or 0.0) * virtual_stake

        # Средняя цена входа в активном ручном портфеле
        avg_entry_manual_row = conn.execute("""
            SELECT AVG(
                CASE
                    WHEN predicted_outcome = 'NO' THEN 1.0 - virtual_bought_price
                    ELSE virtual_bought_price
                END
            ) as avg_entry
            FROM penny_stocks_monitoring
            WHERE status = 'ACTIVE' 
              AND virtual_bought_price IS NOT NULL
              AND predicted_outcome IS NOT NULL
        """).fetchone()
        avg_entry_manual = avg_entry_manual_row['avg_entry'] if avg_entry_manual_row else None

        manual_stats = {
            'count': manual_stats_row['count'] if manual_stats_row else 0,
            'win_rate': manual_win_rate,
            'avg_entry_price': avg_entry_manual,
            'best_trade_pnl': manual_best_pnl,
            'avg_pnl': manual_avg_pnl,
            'total_trades_pnl': round(manual_total_pnl, 4)
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
            if 'url' in row_dict:
                row_dict['url'] = clean_db_url(row_dict['url'])
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
            elif status == 'RESOLVED' and actual_outcome is not None:
                current_outcome_price = 1.0 if actual_outcome == outcome else 0.0

            row_dict['current_outcome_price'] = current_outcome_price
            bought_outcome_price = row_dict.get('bought_outcome_price')
            raw_pnl = row_dict['pnl_cents'] or 0.0
            if bought_outcome_price and 0.0 < bought_outcome_price < 1.0:
                shares = virtual_stake / bought_outcome_price
                row_dict['pnl_cents'] = round(raw_pnl * shares, 2)
            else:
                row_dict['pnl_cents'] = round(raw_pnl * virtual_stake, 2)
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
        'resolved_wins': resolved_wins,
        'resolved_losses': resolved_losses,
        'portfolio': portfolio,
        'virtual_history': virtual_history,
        'stats': stats,
        'manual_stats': manual_stats,
        'price_distribution': bins,
        'active_total': active_total,
        'resolved_total': resolved_total,
        'wins_total': wins_total,
        'losses_total': losses_total,
        'history_total': history_total,
        'system_alerts': system_alerts
    }

def get_whale_stocks_dashboard(active_page=1, active_limit=100, resolved_page=1, resolved_limit=100, history_page=1, history_limit=100, whales_page=1, whales_limit=10) -> dict:
    """
    Собирает данные для дашборда Whale Following (активные, завершенные позиции, статистика, распределение).
    """
    from agents.shared.python.db import get_connection
    with get_connection() as conn:
        virtual_stake = get_global_virtual_stake(conn)
        # Подсчет общего количества активных
        active_total = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM whale_stocks_monitoring p
            WHERE p.status = 'ACTIVE'
        """).fetchone()['cnt']

        # Активные позиции с пагинацией
        active_offset = (active_page - 1) * active_limit
        active_rows = conn.execute("""
            SELECT p.market_id, p.title, p.url, p.initial_price, p.current_price, p.max_price_seen, p.min_price_seen, p.volume_2h, p.predicted_outcome, p.edge, p.confidence, p.added_at, p.virtual_bought_price, p.wallet_address
            FROM whale_stocks_monitoring p
            WHERE p.status = 'ACTIVE'
            ORDER BY p.added_at DESC
            LIMIT ? OFFSET ?
        """, (active_limit, active_offset)).fetchall()

        active = []
        for r in active_rows:
            row_dict = dict(r)
            if 'url' in row_dict:
                row_dict['url'] = clean_db_url(row_dict['url'])
            pred = row_dict['predicted_outcome']
            init = row_dict['initial_price']
            curr = row_dict['current_price']
            mx = row_dict['max_price_seen']
            mn = row_dict['min_price_seen']

            outcome_to_track = pred if pred is not None else 'YES'
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
            FROM whale_stocks_monitoring p
            WHERE p.status = 'RESOLVED'
        """).fetchone()['cnt']

        # Завершенные позиции с пагинацией
        resolved_offset = (resolved_page - 1) * resolved_limit
        resolved_rows = conn.execute("""
            SELECT p.market_id, p.title, p.url, p.initial_price, p.current_price, p.max_price_seen, p.min_price_seen, p.predicted_outcome, p.actual_outcome, p.edge, p.confidence, p.resolved_at, p.wallet_address,
                   h.pnl_cents as pnl_realized
            FROM whale_stocks_monitoring p
            LEFT JOIN (
                SELECT market_id, SUM(pnl_cents) as pnl_cents
                FROM whale_virtual_trades_history
                GROUP BY market_id
            ) h ON p.market_id = h.market_id
            WHERE p.status = 'RESOLVED'
            ORDER BY p.resolved_at DESC
            LIMIT ? OFFSET ?
        """, (resolved_limit, resolved_offset)).fetchall()

        resolved = []
        for r in resolved_rows:
            row_dict = dict(r)
            if 'url' in row_dict:
                row_dict['url'] = clean_db_url(row_dict['url'])
            pred = row_dict['predicted_outcome']
            init = row_dict['initial_price']
            curr = row_dict['current_price']
            mx = row_dict['max_price_seen']
            mn = row_dict['min_price_seen']

            outcome_to_track = pred if pred is not None else 'YES'
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

            # Гипотетический PNL
            actual = row_dict['actual_outcome']
            pnl_realized = row_dict['pnl_realized']
            if pnl_realized is None and actual is not None and init is not None:
                track_up = outcome_to_track.upper()
                act_up = actual.upper()
                bought_outcome = (1.0 - init) if track_up == 'NO' else init
                sold_outcome = 1.0 if act_up == track_up else 0.0
                if bought_outcome > 0:
                    row_dict['pnl_realized'] = round((virtual_stake / bought_outcome) * (sold_outcome - bought_outcome), 2)
                else:
                    row_dict['pnl_realized'] = 0.0
            else:
                row_dict['pnl_realized'] = round((row_dict['pnl_realized'] or 0.0) * virtual_stake, 2)

            resolved.append(row_dict)

        # Виртуальный портфель
        portfolio_rows = conn.execute("""
            SELECT market_id, title, url, initial_price, current_price, predicted_outcome, edge, confidence, virtual_bought_price, virtual_bought_at, wallet_address
            FROM whale_stocks_monitoring
            WHERE status = 'ACTIVE' AND virtual_bought_price IS NOT NULL
            ORDER BY virtual_bought_at DESC
        """).fetchall()

        portfolio = []
        for r in portfolio_rows:
            row_dict = dict(r)
            if 'url' in row_dict:
                row_dict['url'] = clean_db_url(row_dict['url'])
            pred = row_dict['predicted_outcome']
            init = row_dict['initial_price']
            v_bought = row_dict['virtual_bought_price']
            v_curr = row_dict['current_price']

            outcome_to_track = pred if pred is not None else 'YES'
            row_dict['cheap_outcome'] = outcome_to_track

            if outcome_to_track == 'NO':
                bought_outcome = 1.0 - v_bought if v_bought is not None else None
                curr_outcome = 1.0 - v_curr if v_curr is not None else None
            else:
                bought_outcome = v_bought
                curr_outcome = v_curr

            pnl_cents = curr_outcome - bought_outcome if (curr_outcome is not None and bought_outcome is not None) else 0.0
            pnl_percent = (pnl_cents / bought_outcome * 100) if (bought_outcome is not None and bought_outcome > 0) else 0.0
            
            pnl_dollars = (virtual_stake / bought_outcome) * pnl_cents if (bought_outcome is not None and bought_outcome > 0) else 0.0

            row_dict['bought_outcome_price'] = round(bought_outcome, 4) if bought_outcome is not None else None
            row_dict['current_outcome_price'] = round(curr_outcome, 4) if curr_outcome is not None else None
            row_dict['pnl_cents'] = round(pnl_dollars, 2)
            row_dict['pnl_percent'] = round(pnl_percent, 2)

            portfolio.append(row_dict)

        total_active = active_total
        total_resolved = resolved_total

        # Статистика по истории виртуальных сделок
        stats_row = conn.execute("""
            SELECT
                MAX(pnl_cents) as best_pnl,
                AVG(pnl_cents) as avg_pnl
            FROM whale_virtual_trades_history
            WHERE sold_at >= datetime('now', '-30 days')
        """).fetchone()

        win_stats = conn.execute("""
            SELECT 
                COUNT(*) as count,
                SUM(CASE WHEN UPPER(predicted_outcome) = UPPER(actual_outcome) THEN 1 ELSE 0 END) as wins
            FROM whale_stocks_monitoring
            WHERE status = 'RESOLVED' AND predicted_outcome IS NOT NULL AND actual_outcome IS NOT NULL
              AND resolved_at >= datetime('now', '-30 days')
        """).fetchone()

        win_rate = None
        best_pnl = 0.0
        avg_pnl = 0.0
        if win_stats and win_stats['count'] and win_stats['count'] > 0:
            win_rate = (win_stats['wins'] or 0) / win_stats['count']
        if stats_row:
            # raw pnl from DB, not scaled yet
            best_pnl = (stats_row['best_pnl'] or 0.0) * virtual_stake
            avg_pnl = (stats_row['avg_pnl'] or 0.0) * virtual_stake

        avg_entry_row = conn.execute("""
            SELECT AVG(
                CASE
                    WHEN predicted_outcome = 'NO' THEN 1.0 - initial_price
                    ELSE initial_price
                END
            ) as avg_entry
            FROM whale_stocks_monitoring
            WHERE status = 'ACTIVE'
        """).fetchone()
        avg_entry = avg_entry_row['avg_entry'] if avg_entry_row else None

        total_active_predicted = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM whale_stocks_monitoring
            WHERE status = 'ACTIVE' AND predicted_outcome IS NOT NULL
        """).fetchone()['cnt']

        # 1. Кумулятивный PnL (raw pnl from DB, not scaled yet)
        trades_pnl_row = conn.execute("SELECT SUM(pnl_cents) as total_pnl FROM whale_virtual_trades_history").fetchone()
        total_trades_pnl = (trades_pnl_row['total_pnl'] or 0.0) * virtual_stake if trades_pnl_row else 0.0

        # 2. Кумулятивный PnL по всем закрытым whale-рынкам
        all_resolved_rows = conn.execute("""
            SELECT p.initial_price, p.predicted_outcome, p.actual_outcome, h.pnl_cents as pnl_realized
            FROM whale_stocks_monitoring p
            LEFT JOIN (
                SELECT market_id, SUM(pnl_cents) as pnl_cents
                FROM whale_virtual_trades_history
                GROUP BY market_id
            ) h ON p.market_id = h.market_id
            WHERE p.status = 'RESOLVED'
        """).fetchall()

        total_resolved_pnl = 0.0
        sum_won = 0.0
        sum_lost = 0.0
        for r in all_resolved_rows:
            pnl_realized = r['pnl_realized']
            pnl_val = 0.0
            if pnl_realized is not None:
                pnl_val = pnl_realized * virtual_stake
            else:
                actual = r['actual_outcome']
                init = r['initial_price']
                pred = r['predicted_outcome']
                if actual is not None and init is not None:
                    outcome_to_track = pred if pred is not None else 'YES'
                    track_up = outcome_to_track.upper()
                    act_up = actual.upper()
                    bought_outcome = (1.0 - init) if track_up == 'NO' else init
                    sold_outcome = 1.0 if act_up == track_up else 0.0
                    if bought_outcome > 0:
                        pnl_val = (virtual_stake / bought_outcome) * (sold_outcome - bought_outcome)
            
            total_resolved_pnl += pnl_val
            if pnl_val > 0:
                sum_won += pnl_val
            elif pnl_val < 0:
                sum_lost += abs(pnl_val)

        stats = {
            'active_count': total_active,
            'active_predicted_count': total_active_predicted,
            'resolved_count': total_resolved,
            'win_rate': win_rate,
            'avg_entry_price': avg_entry,
            'best_trade_pnl': best_pnl,
            'avg_pnl': avg_pnl,
            'total_trades_pnl': round(total_trades_pnl, 4),
            'total_resolved_pnl': round(total_resolved_pnl, 4),
            'sum_won': round(sum_won, 2),
            'sum_lost': round(sum_lost, 2)
        }

        # Распределение цен входа
        all_prices_rows = conn.execute("""
            SELECT
                CASE
                    WHEN predicted_outcome = 'NO' THEN 1.0 - initial_price
                    ELSE initial_price
                END as outcome_initial_price
            FROM whale_stocks_monitoring
            WHERE status = 'ACTIVE'
        """).fetchall()
        bins = {
            '1-20¢': 0,
            '20-40¢': 0,
            '40-60¢': 0,
            '60-80¢': 0,
            '80+¢': 0
        }
        for r in all_prices_rows:
            p = r['outcome_initial_price']
            if p is None:
                continue
            if p < 0.20:
                bins['1-20¢'] += 1
            elif p < 0.40:
                bins['20-40¢'] += 1
            elif p < 0.60:
                bins['40-60¢'] += 1
            elif p < 0.80:
                bins['60-80¢'] += 1
            else:
                bins['80+¢'] += 1

        history_total = conn.execute("SELECT COUNT(*) as cnt FROM whale_virtual_trades_history").fetchone()['cnt']
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
            FROM whale_virtual_trades_history h
            LEFT JOIN (
                SELECT market_id, current_price, status, actual_outcome
                FROM whale_stocks_monitoring
                GROUP BY market_id
            ) m ON h.market_id = m.market_id
            ORDER BY h.sold_at DESC
            LIMIT ? OFFSET ?
        """, (history_limit, history_offset)).fetchall()
        
        virtual_history = []
        for r in history_rows:
            row_dict = dict(r)
            if 'url' in row_dict:
                row_dict['url'] = clean_db_url(row_dict['url'])
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

            curr = row_dict['current_price']
            status = row_dict['market_status']
            actual_outcome = row_dict['actual_outcome']
            current_outcome_price = None

            if status == 'ACTIVE' and curr is not None:
                if outcome == 'NO':
                    current_outcome_price = round(1.0 - curr, 4)
                else:
                    current_outcome_price = curr
            elif status == 'RESOLVED' and actual_outcome is not None:
                current_outcome_price = 1.0 if actual_outcome == outcome else 0.0

            row_dict['current_outcome_price'] = current_outcome_price
            bought_outcome_price = row_dict.get('bought_outcome_price')
            raw_pnl = row_dict['pnl_cents'] or 0.0
            if bought_outcome_price and 0.0 < bought_outcome_price < 1.0:
                shares = virtual_stake / bought_outcome_price
                row_dict['pnl_cents'] = round(raw_pnl * shares, 2)
            else:
                row_dict['pnl_cents'] = round(raw_pnl * virtual_stake, 2)
            virtual_history.append(row_dict)

        # Последние алерты по транзакциям китов для оповещений
        system_alerts = []
        try:
            alerts_rows = conn.execute("""
                SELECT market_id, created_at, metadata
                FROM signals
                WHERE strategy_type = 'whale'
                ORDER BY created_at DESC
                LIMIT 30
            """).fetchall()
            for r in alerts_rows:
                import json
                meta = {}
                try:
                    if r['metadata']:
                        meta = json.loads(r['metadata'])
                except Exception:
                    pass
                system_alerts.append({
                    'market_id': r['market_id'],
                    'analyzed_at': r['created_at'],
                    'title': meta.get('summary') or f"Whale transaction on {r['market_id']}"
                })
        except Exception as e:
            logger.warning(f"[DataProvider] signals whale alerts недоступны: {e}")

        # Статистика китов с пагинацией
        try:
            row = conn.execute("SELECT COUNT(*) as cnt FROM wallets").fetchone()
            whales_total = row['cnt'] if row else 0
            whales_offset = (whales_page - 1) * whales_limit
            whales_rows = conn.execute("""
                SELECT 
                    w.address,
                    w.alias,
                    w.win_rate,
                    w.total_profit,
                    w.is_insider,
                    COUNT(t.id) as tx_count,
                    COALESCE(SUM(t.amount_usd), 0.0) as total_vol
                FROM wallets w
                LEFT JOIN trader_transactions t ON w.address = t.wallet_address
                GROUP BY w.address
                ORDER BY total_vol DESC, tx_count DESC
                LIMIT ? OFFSET ?
            """, (whales_limit, whales_offset)).fetchall()
            
            whales = []
            for r in whales_rows:
                r_dict = dict(r)
                r_dict['total_vol'] = round(r_dict['total_vol'], 2)
                r_dict['win_rate'] = round(r_dict['win_rate'], 4) if r_dict['win_rate'] is not None else 0.0
                r_dict['total_profit'] = round(r_dict['total_profit'], 2) if r_dict['total_profit'] is not None else 0.0
                whales.append(r_dict)
        except Exception as e:
            logger.warning(f"[DataProvider] wallets table query failed: {e}")
            whales = []
            whales_total = 0

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
        'system_alerts': system_alerts,
        'whales': whales,
        'whales_total': whales_total
    }

def get_strategy_signals(strategy: str, days: Optional[int] = 30, limit: int = 50, page: Optional[int] = None, sort_by: Optional[str] = None, sort_dir: Optional[str] = None) -> Any:

    """Возвращает последние сигналы для конкретной стратегии вместе с названием рынков."""
    import json
    from agents.shared.python.db import get_connection
    
    # 1. Формируем условия фильтрации
    where_clauses = ["LOWER(s.strategy_type) = LOWER(?)"]
    params = [strategy]
    
    if days is not None:
        period_start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        where_clauses.append("s.created_at >= ?")
        params.append(period_start)
        
    where_str = " AND ".join(where_clauses)
    
    ALLOWED_SORT_COLS = {
        'created_at': 's.created_at',
        'market_title': 'm.title',
        'wallet_address': "json_extract(s.details, '$.wallet_address')",
        'target_outcome': 's.target_outcome',
        'market_price_at_signal': 's.market_price_at_signal',
        'status': 's.status',
        'pnl_realized': 's.pnl_realized'
    }

    sort_dir_sql = "DESC"
    if sort_dir and sort_dir.lower() == "asc":
        sort_dir_sql = "ASC"

    order_by_parts = []
    if sort_by in ALLOWED_SORT_COLS:
        col_sql = ALLOWED_SORT_COLS[sort_by]
        if sort_by == 'target_outcome':
            order_by_parts.append(f"s.target_outcome {sort_dir_sql}")
            order_by_parts.append(f"COALESCE(s.estimated_probability, s.predicted_probability) {sort_dir_sql}")
        else:
            order_by_parts.append(f"{col_sql} {sort_dir_sql}")
    else:
        order_by_parts.append("s.created_at DESC")

    if sort_by != 'created_at':
        order_by_parts.append("s.created_at DESC")

    order_by_str = ", ".join(order_by_parts)
    
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
            ORDER BY {order_by_str}
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
                WHERE LOWER(s.strategy_type) = LOWER(?)
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


def get_compounding_dashboard(active_page=1, active_limit=100, wins_page=1, wins_limit=100, losses_page=1, losses_limit=100, history_page=1, history_limit=100) -> dict:
    """
    Собирает данные для дашборда Favourite Compounding (активные, завершенные позиции, статистика, ручной портфель, история ручных сделок).
    """
    from agents.shared.python.db import get_connection, get_compound_settings
    with get_connection() as conn:
        cfg = get_compound_settings()
        virtual_stake = float(cfg.get('virtual_stake', 50.0))
        # Подсчет общего количества активных
        active_total = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM compound_opportunities
            WHERE status IN ('NEW', 'ALERTED', 'BOUGHT', 'ALERTED_EXIT')
        """).fetchone()['cnt']

        # Активные позиции с пагинацией
        active_offset = (active_page - 1) * active_limit
        active_rows = conn.execute("""
            SELECT id, market_id, title, url, price, volume_usd, close_time, hours_left,
                   spread_pct, roi_net_pct, confidence, obviousness_reason, status,
                   virtual_bought_price, virtual_bought_at, outcome
            FROM compound_opportunities
            WHERE status IN ('NEW', 'ALERTED', 'BOUGHT', 'ALERTED_EXIT')
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (active_limit, active_offset)).fetchall()

        active = []
        for r in active_rows:
            row_dict = dict(r)
            if 'url' in row_dict:
                row_dict['url'] = clean_db_url(row_dict['url'])
            active.append(row_dict)

        # Подсчет общего количества выигранных
        wins_total_row = conn.execute("""
            SELECT COUNT(*) as cnt, SUM(pnl_usd) as total_pnl
            FROM compound_opportunities
            WHERE status = 'RESOLVED' AND actual_outcome IS NOT NULL AND actual_outcome = outcome
        """).fetchone()
        wins_total = wins_total_row['cnt']
        total_won_usd = wins_total_row['total_pnl'] or 0.0

        # Подсчет общего количества проигранных
        losses_total_row = conn.execute("""
            SELECT COUNT(*) as cnt, SUM(pnl_usd) as total_pnl
            FROM compound_opportunities
            WHERE status = 'RESOLVED' AND actual_outcome IS NOT NULL AND actual_outcome != outcome
        """).fetchone()
        losses_total = losses_total_row['cnt']
        total_lost_usd = abs(losses_total_row['total_pnl'] or 0.0)

        def _process_compound_resolved_row(r, virtual_stake):
            row_dict = dict(r)
            if 'url' in row_dict:
                row_dict['url'] = clean_db_url(row_dict['url'])
            price = row_dict['price']
            outcome = row_dict['outcome']
            actual = row_dict['actual_outcome']
            
            if actual is None:
                logger.warning(f"[_process_compound_resolved_row] Несогласованное состояние: actual_outcome IS NULL для {row_dict.get('id')}")
                row_dict['pnl_is_hypothetical'] = None
                return row_dict

            pnl_auto = row_dict.get('pnl_auto')
            if pnl_auto is None and price is not None and outcome is not None and price > 0:
                actual_up = actual.upper()
                outcome_up = outcome.upper()
                if actual_up == outcome_up:
                    pnl_auto = (virtual_stake / price) * (1.0 - price) * 0.98
                else:
                    pnl_auto = -virtual_stake
                pnl_auto = round(pnl_auto, 2)
            row_dict['pnl_auto'] = pnl_auto

            pnl_realized = row_dict['pnl_realized']
            if pnl_realized is not None:
                row_dict['pnl_is_hypothetical'] = False
            elif price is not None and outcome is not None and price > 0:
                actual_up = actual.upper()
                outcome_up = outcome.upper()
                if actual_up == outcome_up:
                    pnl_realized = virtual_stake * (1.0 - price) / price * 0.98
                else:
                    pnl_realized = -virtual_stake
                row_dict['pnl_realized'] = round(pnl_realized, 2)
                row_dict['pnl_is_hypothetical'] = True
            else:
                row_dict['pnl_realized'] = 0.0
                row_dict['pnl_is_hypothetical'] = True

            return row_dict

        # Выигранные позиции с пагинацией
        wins_offset = (wins_page - 1) * wins_limit
        wins_rows = conn.execute("""
            SELECT o.id, o.market_id, o.title, o.url, o.price, o.volume_usd, o.close_time,
                   o.confidence, o.obviousness_reason, o.status, o.actual_outcome, o.outcome,
                   o.resolved_at, o.exit_price, o.pnl_usd as pnl_auto,
                   h.pnl_usd as pnl_realized
            FROM compound_opportunities o
            LEFT JOIN (
                SELECT market_id, SUM(pnl_usd) as pnl_usd
                FROM compound_virtual_trades_history
                GROUP BY market_id
            ) h ON o.market_id = h.market_id
            WHERE o.status = 'RESOLVED' AND o.actual_outcome IS NOT NULL AND o.actual_outcome = o.outcome
            ORDER BY o.resolved_at DESC
            LIMIT ? OFFSET ?
        """, (wins_limit, wins_offset)).fetchall()

        # Проигранные позиции с пагинацией
        losses_offset = (losses_page - 1) * losses_limit
        losses_rows = conn.execute("""
            SELECT o.id, o.market_id, o.title, o.url, o.price, o.volume_usd, o.close_time,
                   o.confidence, o.obviousness_reason, o.status, o.actual_outcome, o.outcome,
                   o.resolved_at, o.exit_price, o.pnl_usd as pnl_auto,
                   h.pnl_usd as pnl_realized
            FROM compound_opportunities o
            LEFT JOIN (
                SELECT market_id, SUM(pnl_usd) as pnl_usd
                FROM compound_virtual_trades_history
                GROUP BY market_id
            ) h ON o.market_id = h.market_id
            WHERE o.status = 'RESOLVED' AND o.actual_outcome IS NOT NULL AND o.actual_outcome != o.outcome
            ORDER BY o.resolved_at DESC
            LIMIT ? OFFSET ?
        """, (losses_limit, losses_offset)).fetchall()

        resolved_wins = [_process_compound_resolved_row(r, virtual_stake) for r in wins_rows]
        resolved_losses = [_process_compound_resolved_row(r, virtual_stake) for r in losses_rows]

        # Виртуальный портфель (активный)
        portfolio_rows = conn.execute("""
            SELECT id, market_id, title, url, price, volume_usd, close_time, confidence,
                   virtual_bought_price, virtual_bought_at, outcome
            FROM compound_opportunities
            WHERE status IN ('NEW', 'ALERTED', 'BOUGHT', 'ALERTED_EXIT')
              AND virtual_bought_price IS NOT NULL
            ORDER BY virtual_bought_at DESC
        """).fetchall()

        portfolio = []
        for r in portfolio_rows:
            row_dict = dict(r)
            if 'url' in row_dict:
                row_dict['url'] = clean_db_url(row_dict['url'])
            v_bought = row_dict['virtual_bought_price']
            v_curr = row_dict['price']
            
            pnl_usd = 0.0
            if v_bought is not None and v_curr is not None and v_bought > 0:
                pnl_usd = virtual_stake * (v_curr - v_bought) / v_bought
                if pnl_usd > 0:
                    pnl_usd = pnl_usd * 0.98
            pnl_percent = (pnl_usd / virtual_stake) * 100 if virtual_stake > 0 else 0.0

            row_dict['bought_outcome_price'] = round(v_bought, 4) if v_bought is not None else None
            row_dict['current_outcome_price'] = round(v_curr, 4) if v_curr is not None else None
            row_dict['pnl_usd'] = round(pnl_usd, 2)
            row_dict['pnl_percent'] = round(pnl_percent, 2)
            portfolio.append(row_dict)

        # === 1. АВТО-СТАТИСТИКА (СИГНАЛЫ АГЕНТОВ) ===
        all_resolved_auto = conn.execute("""
            SELECT price, outcome, actual_outcome, pnl_usd
            FROM compound_opportunities
            WHERE status = 'RESOLVED' AND market_id NOT IN (
                SELECT DISTINCT market_id FROM compound_virtual_trades_history
            )
            ORDER BY resolved_at ASC
        """).fetchall()

        auto_count = len(all_resolved_auto)
        auto_wins = 0
        auto_best_pnl = -999999.0
        auto_total_pnl = 0.0
        sum_won = 0.0
        sum_lost = 0.0
        
        current_streak = 0
        streak_type = None
        max_drawdown = 0.0
        peak_pnl = 0.0
        running_pnl = 0.0

        for r in all_resolved_auto:
            price = r['price']
            outcome = r['outcome']
            actual = r['actual_outcome']
            if price is not None and outcome is not None and actual is not None and price > 0:
                act_up = actual.upper()
                out_up = outcome.upper()
                is_win = (act_up == out_up)
                
                if is_win:
                    auto_wins += 1
                    pnl_val = r['pnl_usd'] if r['pnl_usd'] is not None else (virtual_stake / price) * (1.0 - price) * 0.98
                    sum_won += pnl_val
                    if streak_type == "WIN":
                        current_streak += 1
                    else:
                        streak_type = "WIN"
                        current_streak = 1
                else:
                    pnl_val = r['pnl_usd'] if r['pnl_usd'] is not None else -virtual_stake
                    sum_lost += abs(pnl_val)
                    if streak_type == "LOSS":
                        current_streak += 1
                    else:
                        streak_type = "LOSS"
                        current_streak = 1
                
                auto_total_pnl += pnl_val
                if pnl_val > auto_best_pnl:
                    auto_best_pnl = pnl_val
                    
                running_pnl += pnl_val
                if running_pnl > peak_pnl:
                    peak_pnl = running_pnl
                drawdown = peak_pnl - running_pnl
                if drawdown > max_drawdown:
                    max_drawdown = drawdown

        if auto_best_pnl == -999999.0:
            auto_best_pnl = 0.0

        auto_win_rate = auto_wins / auto_count if auto_count > 0 else None
        auto_avg_pnl = auto_total_pnl / auto_count if auto_count > 0 else 0.0

        avg_entry_auto_row = conn.execute("""
            SELECT AVG(price) as avg_entry
            FROM compound_opportunities
            WHERE status IN ('NEW', 'ALERTED', 'BOUGHT', 'ALERTED_EXIT')
        """).fetchone()
        avg_entry_auto = avg_entry_auto_row['avg_entry'] if avg_entry_auto_row else None

        kelly_fraction = 0.0
        if auto_wins > 0 and auto_count > 0:
            p = auto_wins / auto_count
            avg_win_pnl = sum_won / auto_wins
            if avg_win_pnl > 0:
                b = avg_win_pnl / virtual_stake
                kelly_fraction = p - (1.0 - p) / b
                kelly_fraction = max(0.0, kelly_fraction) * 0.5
        stats = {
            'active_count': active_total,
            'active_predicted_count': active_total,
            'resolved_count': wins_total + losses_total,
            'auto_resolved_count': auto_count,
            'win_rate': auto_win_rate,
            'avg_entry_price': avg_entry_auto,
            'best_trade_pnl': round(auto_best_pnl, 2),
            'avg_pnl': round(auto_avg_pnl, 2),
            'total_resolved_pnl': round(auto_total_pnl, 2),
            'sum_won': round(sum_won, 2),
            'sum_lost': round(sum_lost, 2),
            'current_streak': f"{current_streak} {streak_type}" if streak_type else "0",
            'max_drawdown': round(max_drawdown, 2),
            'kelly_fraction': round(kelly_fraction * 100, 1)
        }

        # === 2. РУЧНАЯ СТАТИСТИКА (ВИРТУАЛЬНЫЕ СДЕЛКИ) ===
        manual_stats_row = conn.execute("""
            SELECT
                COUNT(*) as count,
                SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                MAX(pnl_usd) as best_pnl,
                AVG(pnl_usd) as avg_pnl,
                SUM(pnl_usd) as total_pnl
            FROM compound_virtual_trades_history
        """).fetchone()

        manual_win_rate = None
        manual_best_pnl = 0.0
        manual_avg_pnl = 0.0
        manual_total_pnl = 0.0
        if manual_stats_row and manual_stats_row['count'] > 0:
            manual_win_rate = manual_stats_row['wins'] / manual_stats_row['count']
            manual_best_pnl = manual_stats_row['best_pnl'] or 0.0
            manual_avg_pnl = manual_stats_row['avg_pnl'] or 0.0
            manual_total_pnl = manual_stats_row['total_pnl'] or 0.0

        avg_entry_manual_row = conn.execute("""
            SELECT AVG(virtual_bought_price) as avg_entry
            FROM compound_opportunities
            WHERE status IN ('NEW', 'ALERTED', 'BOUGHT', 'ALERTED_EXIT')
              AND virtual_bought_price IS NOT NULL
        """).fetchone()
        avg_entry_manual = avg_entry_manual_row['avg_entry'] if avg_entry_manual_row else None

        manual_stats = {
            'count': manual_stats_row['count'] if manual_stats_row else 0,
            'win_rate': manual_win_rate,
            'avg_entry_price': avg_entry_manual,
            'best_trade_pnl': manual_best_pnl,
            'avg_pnl': manual_avg_pnl,
            'total_trades_pnl': round(manual_total_pnl, 2)
        }

        # Подсчет истории виртуальных сделок
        history_total = conn.execute("SELECT COUNT(*) as cnt FROM compound_virtual_trades_history").fetchone()['cnt']

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
                h.pnl_usd, 
                h.pnl_percent, 
                h.bought_at, 
                h.sold_at, 
                h.max_price_seen, 
                h.min_price_seen,
                m.price as current_price,
                m.status as market_status,
                m.actual_outcome
            FROM compound_virtual_trades_history h
            LEFT JOIN (
                SELECT market_id, price, status, actual_outcome
                FROM compound_opportunities
                GROUP BY market_id
            ) m ON h.market_id = m.market_id
            ORDER BY h.sold_at DESC
            LIMIT ? OFFSET ?
        """, (history_limit, history_offset)).fetchall()

        virtual_history = []
        for r in history_rows:
            row_dict = dict(r)
            if 'url' in row_dict:
                row_dict['url'] = clean_db_url(row_dict['url'])
            virtual_history.append(row_dict)

        # Цепочки реинвестирования (Parlays)
        chains_rows = conn.execute("SELECT * FROM compound_chains ORDER BY id DESC LIMIT 50").fetchall()
        chains = [dict(c) for c in chains_rows]
        
        if chains:
            chain_ids = [c["id"] for c in chains]
            placeholders = ",".join("?" * len(chain_ids))
            bets_rows = conn.execute(
                f"""
                SELECT b.*, o.title, o.url, o.outcome as target_outcome 
                FROM compound_chain_bets b
                LEFT JOIN compound_opportunities o ON b.opp_id = o.id
                WHERE b.chain_id IN ({placeholders}) 
                ORDER BY b.chain_id DESC, b.step_index ASC
                """, 
                chain_ids
            ).fetchall()
            
            bets_by_chain = {}
            for b in bets_rows:
                b_dict = dict(b)
                if 'url' in b_dict:
                    b_dict['url'] = clean_db_url(b_dict['url'])
                c_id = b_dict["chain_id"]
                if c_id not in bets_by_chain:
                    bets_by_chain[c_id] = []
                bets_by_chain[c_id].append(b_dict)
                
            for c in chains:
                c["bets"] = bets_by_chain.get(c["id"], [])

    return {
        'active': active,
        'resolved_wins': resolved_wins,
        'resolved_losses': resolved_losses,
        'portfolio': portfolio,
        'stats': stats,
        'manual_stats': manual_stats,
        'virtual_history': virtual_history,
        'total_active': active_total,
        'total_won_usd': total_won_usd,
        'total_lost_usd': total_lost_usd,

        'wins_total': wins_total,
        'losses_total': losses_total,
        'history_total': history_total,
        'chains': chains
    }

def get_eval_status() -> dict:
    """
    Возвращает статус последнего запуска Outcome Tracker и историю последних калибровок.
    """
    from agents.shared.python.db import get_connection
    import json

    from core.logger import LLMLogger

    result = {
        "tracker": None,
        "calibrations": [],
        "llm_analytics": LLMLogger.get_llm_analytics_last_24h()
    }

    with get_connection() as conn:
        # 1. Читаем последний запуск трекера из таблицы memory
        try:
            row = conn.execute("SELECT value FROM memory WHERE key = 'outcome_tracker_last_run'").fetchone()
            if row:
                result["tracker"] = json.loads(row["value"])
        except Exception:
            pass

        # 2. Читаем последние 5 предложений калибровки
        try:
            cal_rows = conn.execute("""
                SELECT strategy_type, param_name, param_value, previous_value, reason, auto_applied, updated_at
                FROM calibration_params
                ORDER BY updated_at DESC, id DESC
                LIMIT 5
            """).fetchall()
            
            for r in cal_rows:
                result["calibrations"].append({
                    "strategy_type": r["strategy_type"],
                    "param_name": r["param_name"],
                    "param_value": r["param_value"],
                    "previous_value": r["previous_value"],
                    "reason": r["reason"],
                    "auto_applied": bool(r["auto_applied"]),
                    "updated_at": r["updated_at"]
                })
        except Exception:
            pass

    return result
