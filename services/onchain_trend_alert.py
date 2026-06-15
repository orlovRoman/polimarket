import logging
from typing import Optional
from datetime import datetime, timezone
from agents.shared.python.db import get_connection, is_alert_already_sent, mark_alert_sent

logger = logging.getLogger("NexusPolyBot.OnchainTrend")

def _log_whale_signal_to_eval(
    market_id: str,
    side: str,
    prob: float,
    entry_price: float,
    summary_msg: str,
    metadata_extra: dict,
    title: str = None,
    url: str = None,
    is_single: bool = False,
    is_series: bool = False,
    close_time: str = None
):
    try:
        from core.eval.signal_logger import SignalLogger, StrategyType
        logger_eval = SignalLogger()
        ts = int(datetime.now(timezone.utc).timestamp())
        
        if is_single:
            sig_id = f"sig-whale-single-{market_id}-{metadata_extra.get('wallet_address', '')[:8]}-{int(metadata_extra.get('amount_usd', 0))}-{ts}"
        elif is_series:
            sig_id = f"sig-whale-series-{market_id}-{metadata_extra.get('wallet_address', '')[:8]}-{ts}"
        else:
            sig_id = f"sig-whale-{market_id}-{ts}"

        if not close_time and market_id:
            try:
                from agents.shared.python.db import get_connection
                with get_connection() as conn:
                    m_row = conn.execute("SELECT close_time FROM markets WHERE id = ?", (market_id,)).fetchone()
                    if m_row and m_row["close_time"]:
                        close_time = m_row["close_time"]
            except Exception:
                pass

        metadata = {
            "target_outcome": side,
            "priority": "medium",
            "summary": summary_msg,
            "platform": "polymarket",
            "close_time": close_time
        }
        metadata.update(metadata_extra)
        
        logger_eval.log_signal(
            signal_id=sig_id,
            strategy_type=StrategyType.WHALE,
            market_id=market_id,
            predicted_probability=prob,
            market_price_at_signal=entry_price,
            edge_at_signal=max(-1.0, min(1.0, prob - entry_price)),
            metadata=metadata
        )

        # Сохраняем в мониторинг виртуального портфеля
        try:
            from agents.shared.python.db import add_whale_stock_to_monitoring
            edge = max(-1.0, min(1.0, prob - entry_price))
            
            title_val = title
            url_val = url
            if not title_val or not url_val:
                with get_connection() as conn:
                    m_row = conn.execute("SELECT title, url FROM markets WHERE id = ?", (market_id,)).fetchone()
                    if m_row:
                        title_val = title_val or m_row['title']
                        url_val = url_val or m_row['url']
            
            yes_price = entry_price if side == "YES" else (1.0 - entry_price)

            add_whale_stock_to_monitoring(
                market_id=market_id,
                title=title_val or f"Market {market_id}",
                url=url_val or "",
                initial_price=yes_price,
                predicted_outcome=side,
                edge=edge,
                confidence=0.5,
                wallet_address=metadata_extra.get("wallet_address"),
                close_time=close_time
            )
        except Exception as db_e:
            logger.error(f"[OnchainTrend] Ошибка сохранения Whale-сигнала в мониторинг: {db_e}", exc_info=True)
            
    except Exception as e:
        logger.error(f"[OnchainTrend] Ошибка логирования Whale-сигнала в Evaluation Engine: {e}", exc_info=True)


def _process_spike_row(row: dict) -> Optional[dict]:
    row = dict(row)
    alert_key = f"onchain_spike_{row['market_id']}"
    if is_alert_already_sent(alert_key, ttl_hours=2):
        return None
        
    mark_alert_sent(alert_key, "onchain_spike")
    
    try:
        total_vol = row['yes_vol'] + row['no_vol']
        ratio_yes = row['yes_vol'] / total_vol if total_vol > 0 else 0.5
        side = "YES" if row["yes_vol"] > row["no_vol"] else "NO"
        prob = ratio_yes if side == "YES" else (1.0 - ratio_yes)
        prob = max(0.0, min(1.0, prob))

        m_price = row["price"] if row["price"] is not None else 0.5
        entry_price = m_price if side == "YES" else (1.0 - m_price)

        _log_whale_signal_to_eval(
            market_id=row['market_id'],
            side=side,
            prob=prob,
            entry_price=entry_price,
            summary_msg=f"Whale volume spike: {row['title']}",
            metadata_extra={
                "wallet_address": row.get("top_wallet"),
                "yes_vol": row['yes_vol'],
                "no_vol": row['no_vol'],
                "vol_recent": row['vol_recent'],
                "vol_prev": row['vol_prev']
            },
            title=row.get('title'),
            url=row.get('url'),
            close_time=row.get('close_time')
        )

    except Exception as e:
        logger.error(f"[OnchainTrend] Ошибка обработки строки spike: {e}", exc_info=True)
    return dict(row)

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
                    m.close_time,
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
                             THEN t.amount_usd ELSE 0.0 END) AS no_vol,
                    (SELECT wallet_address FROM trader_transactions
                     WHERE market_id = t.market_id
                       AND timestamp > datetime('now', '-2 hours')
                     ORDER BY amount_usd DESC LIMIT 1) as top_wallet
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
        processed = _process_spike_row(row)
        if processed:
            spikes.append(processed)
    return spikes

def _process_single_bet_row(row: dict) -> Optional[dict]:
    row = dict(row)
    alert_key = f"whale_single_bet_{row['market_id']}_{row['wallet_address']}_{row['amount_usd']:.0f}"
    if is_alert_already_sent(alert_key, ttl_hours=2):
        return None
        
    mark_alert_sent(alert_key, "whale_single_bet")
    
    try:
        side = row["outcome"]
        m_price = row["market_price"] if row["market_price"] is not None else 0.5
        
        if row["price"] is not None:
            entry_price = row["price"]
            price_yes = row["price"] if side == "YES" else (1.0 - row["price"])
        else:
            price_yes = m_price
            entry_price = price_yes if side == "YES" else (1.0 - price_yes)
            
        prob = min(0.97, price_yes + 0.12) if side == "YES" else max(0.03, (1.0 - price_yes) + 0.12)
        
        summary_msg = f"Whale single bet: ${row['amount_usd']:,.0f} on {side} by {row['wallet_address'][:8]}... on {row['title']}"
        logger.info(f"[OnchainTrend] {summary_msg}")
        
        _log_whale_signal_to_eval(
            market_id=row['market_id'],
            side=side,
            prob=prob,
            entry_price=entry_price,
            summary_msg=summary_msg,
            metadata_extra={
                "wallet_address": row['wallet_address'],
                "amount_usd": row['amount_usd'],
                "reason": "large_single_bet"
            },
            title=row.get('title'),
            url=row.get('url'),
            is_single=True,
            close_time=row.get('close_time')
        )

    except Exception as e:
        logger.error(f"[OnchainTrend] Ошибка обработки строки крупной сделки: {e}", exc_info=True)
    return dict(row)

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
                    m.url,
                    m.price as market_price,
                    m.close_time
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
        processed = _process_single_bet_row(row)
        if processed:
            signals.append(processed)
    return signals

def _process_wallet_series_row(row: dict) -> Optional[dict]:
    row = dict(row)
    alert_key = f"whale_series_{row['market_id']}_{row['wallet_address']}"
    if is_alert_already_sent(alert_key, ttl_hours=2):
        return None
        
    mark_alert_sent(alert_key, "whale_series")
    
    try:
        side = "YES" if row["yes_vol"] > row["no_vol"] else "NO"
        m_price = row["market_price"] if row["market_price"] is not None else 0.5
        
        if row["avg_price"] is not None:
            entry_price = row["avg_price"]
            price_yes = row["avg_price"] if side == "YES" else (1.0 - row["avg_price"])
        else:
            price_yes = m_price
            entry_price = price_yes if side == "YES" else (1.0 - price_yes)
            
        prob = min(0.97, price_yes + 0.12) if side == "YES" else max(0.03, (1.0 - price_yes) + 0.12)
        
        summary_msg = f"Whale wallet series: {row['tx_count']} trades, total ${row['total_amount_usd']:,.0f} on {side} by {row['wallet_address'][:8]}... on {row['title']}"
        logger.info(f"[OnchainTrend] {summary_msg}")
        
        _log_whale_signal_to_eval(
            market_id=row['market_id'],
            side=side,
            prob=prob,
            entry_price=entry_price,
            summary_msg=summary_msg,
            metadata_extra={
                "wallet_address": row['wallet_address'],
                "total_amount_usd": row['total_amount_usd'],
                "tx_count": row['tx_count'],
                "reason": "wallet_series"
            },
            title=row.get('title'),
            url=row.get('url'),
            is_series=True,
            close_time=row.get('close_time')
        )

    except Exception as e:
        logger.error(f"[OnchainTrend] Ошибка обработки строки серии сделок: {e}", exc_info=True)
    return dict(row)

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
                    m.url,
                    m.price as market_price,
                    m.close_time
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
        processed = _process_wallet_series_row(row)
        if processed:
            signals.append(processed)
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
