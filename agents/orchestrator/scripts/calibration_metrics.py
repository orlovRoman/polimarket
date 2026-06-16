import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("NexusPolyBot.Calibration")

def get_win_rate_by_strategy(conn, window_days: int) -> dict:
    """Возвращает win rate для SCOUT, SWING, PENNY, WHALE, COMPOUND."""
    metrics = {}
    
    # 1. Сигналы (SCOUT, SWING)
    rows = conn.execute("""
        SELECT type, 
               COUNT(*) as total,
               SUM(CASE WHEN was_profitable = 1 THEN 1 ELSE 0 END) as wins
        FROM signals 
        WHERE status = 'ARCHIVED' 
          AND resolved_at >= datetime('now', ?)
        GROUP BY type
    """, (f'-{window_days} days',)).fetchall()
    
    for r in rows:
        stype = (r['type'] or 'unknown').lower()
        total = r['total'] or 0
        wins = r['wins'] or 0
        if total > 0:
            metrics[stype] = {
                "total": total,
                "wins": wins,
                "win_rate": round(100.0 * wins / total, 1)
            }
            
    # 2. PENNY (penny_stocks_monitoring)
    penny_rows = conn.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN UPPER(predicted_outcome) = UPPER(actual_outcome) THEN 1 ELSE 0 END) as wins
        FROM penny_stocks_monitoring
        WHERE status = 'RESOLVED' AND predicted_outcome IS NOT NULL
          AND resolved_at >= datetime('now', ?)
    """, (f'-{window_days} days',)).fetchone()
    if penny_rows and penny_rows['total'] > 0:
        metrics['penny'] = {
            "total": penny_rows['total'],
            "wins": penny_rows['wins'],
            "win_rate": round(100.0 * penny_rows['wins'] / penny_rows['total'], 1)
        }

    # 3. WHALE (whale_stocks_monitoring)
    # 4. COMPOUND (compound_opportunities)
    # Упростим: берем virtual trades history
    whale_rows = conn.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN pnl_cents > 0 THEN 1 ELSE 0 END) as wins
        FROM whale_virtual_trades_history
        WHERE sold_at >= datetime('now', ?)
    """, (f'-{window_days} days',)).fetchone()
    if whale_rows and whale_rows['total'] > 0:
        metrics['whale'] = {
            "total": whale_rows['total'],
            "wins": whale_rows['wins'],
            "win_rate": round(100.0 * whale_rows['wins'] / whale_rows['total'], 1)
        }
        
    compound_rows = conn.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins
        FROM compound_virtual_trades_history
        WHERE sold_at >= datetime('now', ?)
    """, (f'-{window_days} days',)).fetchone()
    if compound_rows and compound_rows['total'] > 0:
        metrics['compound'] = {
            "total": compound_rows['total'],
            "wins": compound_rows['wins'],
            "win_rate": round(100.0 * compound_rows['wins'] / compound_rows['total'], 1)
        }

    return metrics

def get_brier_score(conn, window_days: int) -> dict:
    """Считает Brier Score для SCOUT (только где scout_probability IS NOT NULL)."""
    rows = conn.execute("""
        SELECT a.scout_probability, m.outcome 
        FROM idea_audit a
        JOIN markets m ON a.market_id = m.id
        WHERE a.scout_probability IS NOT NULL
          AND m.outcome IN ('YES', 'NO')
          AND a.created_at >= datetime('now', ?)
    """, (f'-{window_days} days',)).fetchall()
    
    if not rows:
        return {"brier_score": None, "samples": 0}
        
    sum_sq_err = 0.0
    for r in rows:
        prob = r['scout_probability']
        actual = 1.0 if r['outcome'] == 'YES' else 0.0
        sum_sq_err += (prob - actual) ** 2
        
    score = sum_sq_err / len(rows)
    return {"brier_score": round(score, 4), "samples": len(rows)}

def get_funnel_stats(conn, window_days: int) -> dict:
    """Воронка отказов."""
    rows = conn.execute("""
        SELECT final_outcome, COUNT(*) as cnt
        FROM idea_audit
        WHERE created_at >= datetime('now', ?)
        GROUP BY final_outcome
    """, (f'-{window_days} days',)).fetchall()
    
    funnel = {}
    total = 0
    for r in rows:
        outcome = r['final_outcome'] or 'unknown'
        cnt = r['cnt']
        funnel[outcome] = cnt
        total += cnt
        
    return {"breakdown": funnel, "total_analyzed": total}

def get_pnl_by_strategy(conn, window_days: int) -> dict:
    """PnL по стратегиям."""
    pnl = {}
    
    # 1. Signals (SCOUT, SWING)
    rows = conn.execute("""
        SELECT type, SUM(pnl_realized) as pnl
        FROM signals
        WHERE status IN ('WIN', 'LOSS')
          AND resolved_at >= datetime('now', ?)
        GROUP BY type
    """, (f'-{window_days} days',)).fetchall()
    for r in rows:
        stype = (r['type'] or 'unknown').lower()
        if r['pnl'] is not None:
            pnl[stype] = round(r['pnl'], 2)
            
    # 2. Whale
    whale_pnl = conn.execute("""
        SELECT SUM(pnl_cents) / 100.0 as pnl
        FROM whale_virtual_trades_history
        WHERE sold_at >= datetime('now', ?)
    """, (f'-{window_days} days',)).fetchone()
    if whale_pnl and whale_pnl['pnl'] is not None:
        pnl['whale'] = round(whale_pnl['pnl'], 2)
        
    comp_pnl = conn.execute("""
        SELECT SUM(pnl_usd) as pnl
        FROM compound_virtual_trades_history
        WHERE sold_at >= datetime('now', ?)
    """, (f'-{window_days} days',)).fetchone()
    if comp_pnl and comp_pnl['pnl'] is not None:
        pnl['compound'] = round(comp_pnl['pnl'], 2)
        
    return pnl

def get_token_usage_stats(conn, window_days: int) -> dict:
    rows = conn.execute("""
        SELECT agent_name, COUNT(*) as calls, SUM(total_tokens) as tokens
        FROM llm_calls
        WHERE created_at >= datetime('now', ?)
        GROUP BY agent_name
    """, (f'-{window_days} days',)).fetchall()
    
    stats = {}
    for r in rows:
        stats[r['agent_name']] = {
            "calls": r['calls'],
            "tokens": r['tokens']
        }
    return stats

def get_shadow_rejection_reasons(conn, window_days: int) -> list:
    rows = conn.execute("""
        SELECT shadow_reason, COUNT(*) as cnt
        FROM idea_audit
        WHERE shadow_agree = 0 
          AND scout_edge IS NOT NULL
          AND created_at >= datetime('now', ?)
        GROUP BY shadow_reason
        ORDER BY cnt DESC
        LIMIT 10
    """, (f'-{window_days} days',)).fetchall()
    
    return [{"reason": r['shadow_reason'], "count": r['cnt']} for r in rows]

def get_all_metrics(conn, window_days: int) -> dict:
    return {
        "win_rate": get_win_rate_by_strategy(conn, window_days),
        "brier_score": get_brier_score(conn, window_days),
        "funnel": get_funnel_stats(conn, window_days),
        "pnl": get_pnl_by_strategy(conn, window_days),
        "tokens": get_token_usage_stats(conn, window_days),
        "shadow_rejections": get_shadow_rejection_reasons(conn, window_days),
        "window_days": window_days
    }
