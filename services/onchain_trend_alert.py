import logging
from typing import Optional
from datetime import datetime, timezone
from agents.shared.python.db import get_connection, is_alert_already_sent, mark_alert_sent

logger = logging.getLogger("NexusPolyBot.OnchainTrend")

def scan_volume_spikes(min_spike_ratio: float = 1.5) -> list[dict]:
    """
    SQL-only: находит рынки с аномальным ростом ончейн-объёма.
    Сравнивает объём за последние 2ч с предыдущими 2ч.
    Без LLM.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT
                    t.market_id,
                    m.title,
                    m.url,
                    m.price,
                    SUM(CASE WHEN t.timestamp > datetime('now', '-2 hours')
                             THEN t.amount_usd ELSE 0.0 END) AS vol_recent,
                    SUM(CASE WHEN t.timestamp BETWEEN datetime('now', '-4 hours')
                             AND datetime('now', '-2 hours')
                             THEN t.amount_usd ELSE 0.0 END) AS vol_prev,
                    SUM(CASE WHEN t.outcome = 'YES'
                             AND t.timestamp > datetime('now', '-2 hours')
                             THEN t.amount_usd ELSE 0.0 END) AS yes_vol,
                    SUM(CASE WHEN t.outcome = 'NO'
                             AND t.timestamp > datetime('now', '-2 hours')
                             THEN t.amount_usd ELSE 0.0 END) AS no_vol
                FROM trader_transactions t
                JOIN markets m ON t.market_id = m.id
                WHERE t.timestamp > datetime('now', '-4 hours')
                GROUP BY t.market_id
                HAVING vol_prev > 100.0 AND (vol_recent / vol_prev) >= ?
            """, (min_spike_ratio,)).fetchall()
    except Exception as e:
        logger.error(f"[OnchainTrend] Ошибка при SQL-запросе spikes: {e}")
        return []

    spikes = []
    for row in rows:
        alert_key = f"onchain_spike_{row['market_id']}"
        if is_alert_already_sent(alert_key, ttl_hours=2):
            continue
        spikes.append(dict(row))
        mark_alert_sent(alert_key, "onchain_spike")
        
        # Запись в Evaluation Engine
        try:
            total_vol = row['yes_vol'] + row['no_vol']
            ratio_yes = row['yes_vol'] / total_vol if total_vol > 0 else 0.5
            side = "YES" if row["yes_vol"] > row["no_vol"] else "NO"
            prob = ratio_yes if side == "YES" else (1.0 - ratio_yes)
            prob = max(0.0, min(1.0, prob))

            from core.eval.signal_logger import SignalLogger, StrategyType
            logger_eval = SignalLogger()
            row_price = row["price"] if row["price"] is not None else 0.5
            ts = int(datetime.now(timezone.utc).timestamp())
            logger_eval.log_signal(
                signal_id=f"sig-whale-{row['market_id']}-{ts}",
                strategy_type=StrategyType.WHALE,
                market_id=row['market_id'],
                predicted_probability=prob,
                market_price_at_signal=row_price,
                edge_at_signal=max(-1.0, min(1.0, prob - row_price)),
                metadata={
                    "target_outcome": side,
                    "priority": "medium",
                    "summary": f"Whale volume spike: {row['title']}",
                    "platform": "polymarket",
                    "yes_vol": row['yes_vol'],
                    "no_vol": row['no_vol'],
                    "vol_recent": row['vol_recent'],
                    "vol_prev": row['vol_prev']
                }
            )
        except Exception as e:
            logger.error(f"[OnchainTrend] Ошибка логирования Whale-сигнала в Evaluation Engine: {e}", exc_info=True)
    return spikes


def scan_large_single_bets() -> list[dict]:
    """
    Находит крупные одиночные сделки (> $1000) за последние 2 часа
    и логирует сигналы типа WHALE в Evaluation Engine.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT 
                    t.market_id,
                    t.wallet_address,
                    t.amount_usd,
                    t.outcome,
                    t.price,
                    m.title,
                    m.url
                FROM trader_transactions t
                JOIN markets m ON t.market_id = m.id
                WHERE t.timestamp > datetime('now', '-2 hours')
                  AND t.amount_usd > 1000.0
            """).fetchall()
    except Exception as e:
        logger.error(f"[OnchainTrend] Ошибка при SQL-запросе крупных сделок: {e}")
        return []

    signals = []
    for row in rows:
        alert_key = f"whale_single_bet_{row['market_id']}_{row['wallet_address']}_{row['amount_usd']:.0f}"
        if is_alert_already_sent(alert_key, ttl_hours=2):
            continue
            
        signals.append(dict(row))
        mark_alert_sent(alert_key, "whale_single_bet")
        
        try:
            side = row["outcome"]
            row_price = row["price"] if row["price"] is not None else 0.5
            prob = min(0.97, row_price + 0.12) if side == "YES" else max(0.03, (1.0 - row_price) + 0.12)
            
            from core.eval.signal_logger import SignalLogger, StrategyType
            logger_eval = SignalLogger()
            
            # Детализированное описание для логов
            summary_msg = f"Whale single bet: ${row['amount_usd']:,.0f} on {side} by {row['wallet_address'][:8]}... on {row['title']}"
            logger.info(f"[OnchainTrend] {summary_msg}")
            
            ts = int(datetime.now(timezone.utc).timestamp())
            logger_eval.log_signal(
                signal_id=f"sig-whale-single-{row['market_id']}-{row['wallet_address'][:8]}-{int(row['amount_usd'])}-{ts}",
                strategy_type=StrategyType.WHALE,
                market_id=row['market_id'],
                predicted_probability=prob,
                market_price_at_signal=row_price,
                edge_at_signal=max(-1.0, min(1.0, prob - row_price)),
                metadata={
                    "target_outcome": side,
                    "priority": "medium",
                    "summary": summary_msg,
                    "platform": "polymarket",
                    "wallet_address": row['wallet_address'],
                    "amount_usd": row['amount_usd'],
                    "reason": "large_single_bet"
                }
            )
        except Exception as e:
            logger.error(f"[OnchainTrend] Ошибка логирования крупной сделки: {e}", exc_info=True)
            
    return signals


def scan_wallet_series() -> list[dict]:
    """
    Находит серии сделок одного кошелька на одном рынке за последний 1 час (>= 5 сделок, общий объем > $2000)
    и логирует сигналы типа WHALE в Evaluation Engine.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT 
                    t.market_id,
                    t.wallet_address,
                    COUNT(*) as tx_count,
                    SUM(t.amount_usd) as total_amount_usd,
                    -- Определяем доминирующий исход серии
                    SUM(CASE WHEN t.outcome = 'YES' THEN t.amount_usd ELSE 0.0 END) as yes_vol,
                    SUM(CASE WHEN t.outcome = 'NO' THEN t.amount_usd ELSE 0.0 END) as no_vol,
                    AVG(t.price) as avg_price,
                    m.title,
                    m.url
                FROM trader_transactions t
                JOIN markets m ON t.market_id = m.id
                WHERE t.timestamp > datetime('now', '-1 hour')
                GROUP BY t.market_id, t.wallet_address
                HAVING tx_count >= 2 AND total_amount_usd > 2000.0
            """).fetchall()
    except Exception as e:
        logger.error(f"[OnchainTrend] Ошибка при SQL-запросе серий сделок: {e}")
        return []

    signals = []
    for row in rows:
        alert_key = f"whale_series_{row['market_id']}_{row['wallet_address']}"
        if is_alert_already_sent(alert_key, ttl_hours=2):
            continue
            
        signals.append(dict(row))
        mark_alert_sent(alert_key, "whale_series")
        
        try:
            side = "YES" if row["yes_vol"] > row["no_vol"] else "NO"
            row_price = row["avg_price"] if row["avg_price"] is not None else 0.5
            prob = min(0.97, row_price + 0.12) if side == "YES" else max(0.03, (1.0 - row_price) + 0.12)
            
            from core.eval.signal_logger import SignalLogger, StrategyType
            logger_eval = SignalLogger()
            
            summary_msg = f"Whale wallet series: {row['tx_count']} trades, total ${row['total_amount_usd']:,.0f} on {side} by {row['wallet_address'][:8]}... on {row['title']}"
            logger.info(f"[OnchainTrend] {summary_msg}")
            
            ts = int(datetime.now(timezone.utc).timestamp())
            logger_eval.log_signal(
                signal_id=f"sig-whale-series-{row['market_id']}-{row['wallet_address'][:8]}-{ts}",
                strategy_type=StrategyType.WHALE,
                market_id=row['market_id'],
                predicted_probability=prob,
                market_price_at_signal=row_price,
                edge_at_signal=max(-1.0, min(1.0, prob - row_price)),
                metadata={
                    "target_outcome": side,
                    "priority": "medium",
                    "summary": summary_msg,
                    "platform": "polymarket",
                    "wallet_address": row['wallet_address'],
                    "total_amount_usd": row['total_amount_usd'],
                    "tx_count": row['tx_count'],
                    "reason": "wallet_series"
                }
            )
        except Exception as e:
            logger.error(f"[OnchainTrend] Ошибка логирования серии сделок: {e}", exc_info=True)
            
    return signals


def build_spike_message(spike: dict) -> str:
    """Форматирует алерт. Никакого LLM."""
    vol_recent = spike["vol_recent"]
    vol_prev = spike["vol_prev"]
    vol_prev_safe = max(vol_prev, 100.0)
    ratio = vol_recent / vol_prev_safe
    side = "YES" if spike["yes_vol"] > spike["no_vol"] else "NO"
    price_yes = int(round(spike["price"] * 100))
    return (
        f"🔥 <b>Ончейн-всплеск объёма</b>\n"
        f"<a href='{spike['url']}'>{spike['title']}</a>\n"
        f"📈 Объём вырос <b>x{ratio:.1f}</b> за 2ч\n"
        f"💰 Деньги идут в <b>{side}</b> "
        f"(YES: ${spike['yes_vol']:,.0f} / NO: ${spike['no_vol']:,.0f})\n"
        f"💵 Цена YES: {price_yes}¢"
    )
