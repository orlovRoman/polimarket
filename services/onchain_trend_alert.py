import logging
from typing import Optional
from datetime import datetime, timezone
from agents.shared.python.db import get_connection, is_alert_already_sent, mark_alert_sent
from config import WHALE_GATE_MIN_CONFIDENCE

logger = logging.getLogger("NexusPolyBot.OnchainTrend")

def _normalize_close_time(raw_close) -> Optional[str]:
    if not raw_close:
        return None
    try:
        if isinstance(raw_close, datetime):
            return raw_close.strftime("%Y-%m-%d %H:%M:%S")
        ct = str(raw_close).replace('Z', '+00:00')
        return datetime.fromisoformat(ct).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

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
    close_time: str = None,
    confidence: float = 0.5
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
                confidence=confidence,
                wallet_address=metadata_extra.get("wallet_address"),
                close_time=close_time,
                amount_usd=metadata_extra.get("amount_usd", metadata_extra.get("total_amount_usd", 0.0))
            )
        except Exception as db_e:
            logger.error(f"[OnchainTrend] Ошибка сохранения Whale-сигнала в мониторинг: {db_e}", exc_info=True)
            
    except Exception as e:
        logger.error(f"[OnchainTrend] Ошибка логирования Whale-сигнала в Evaluation Engine: {e}", exc_info=True)


def _process_spike_row(row: dict, min_market_price: float, max_market_price: float, base_bonus: float, min_vol: float, min_win_rate: float = 0.50, min_trades: int = 3) -> Optional[dict]:
    row = dict(row)
    m_price = row.get("market_price") if row.get("market_price") is not None else row.get("price", 0.5)
    vol = row.get("market_volume") or row.get("vol_recent", 0.0)
    
    if vol < min_vol or m_price <= min_market_price or m_price >= max_market_price:
        logger.info(f"Skipped whale spike (market filter): market_id={row['market_id']}, vol={vol}, price={m_price}, reason=EXTREME_PRICE_OR_VOL")
        return None

    if not row.get("top_wallet"):
        logger.info(f"Skipped whale spike (no qualified wallet): market_id={row['market_id']}")
        return None
        
    wallet_win_rate = row.get("win_rate") or 0.0
    wallet_n_trades = row.get("n_trades") or 0
    if wallet_n_trades < min_trades or wallet_win_rate < min_win_rate:
        logger.info(
            f"Skipped whale spike (wallet filter): market_id={row['market_id']}, "
            f"wallet={row.get('top_wallet','?')[:8]}, "
            f"win_rate={wallet_win_rate}, n_trades={wallet_n_trades}"
        )
        return None
        
    alert_key = f"onchain_spike_{row['market_id']}"
    if is_alert_already_sent(alert_key, ttl_hours=2):
        return None
        
    try:
        total_vol = row['yes_vol'] + row['no_vol']
        ratio_yes = row['yes_vol'] / total_vol if total_vol > 0 else 0.5
        side = "YES" if row["yes_vol"] > row["no_vol"] else "NO"
        prob = ratio_yes if side == "YES" else (1.0 - ratio_yes)
        
        prob = min(1.0, prob + base_bonus)
        
        prob = max(0.0, min(1.0, prob))

        m_price = row.get("market_price") if row.get("market_price") is not None else row.get("price", 0.5)
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
            close_time=_normalize_close_time(row.get('close_time'))
        )
        
        row['side'] = side
        row['prob'] = prob
        row['entry_price'] = entry_price
        row['confidence'] = prob  # Add confidence since it's asked for context

        mark_alert_sent(alert_key, "onchain_spike")

    except Exception as e:
        logger.error(f"[OnchainTrend] Ошибка обработки строки spike: {e}", exc_info=True)
        return None
    return row

def scan_volume_spikes(min_spike_ratio: float = 1.5) -> list[dict]:
    """
    SQL-only: находит рынки с аномальным ростом ончейн-объёма.
    Сравнивает объём за последние 2ч с предыдущими 2ч.
    Без LLM.
    """
    try:
        from agents.shared.python.db import get_whale_settings
        settings = get_whale_settings()
        min_whale_win_rate = float(settings.get('min_whale_win_rate', 0.50))
        min_whale_trades = int(settings.get('min_whale_trades', 3))
        min_market_price = float(settings.get('min_market_price', 0.05))
        max_market_price = float(settings.get('max_market_price', 0.95))
        base_bonus = float(settings.get('whale_edge_bonus', 0.0))
        min_vol = float(settings.get('min_market_volume', 5000.0))
        
        with get_connection() as conn:
            rows = conn.execute("""
                WITH spike_markets AS (
                    SELECT
                        t.market_id,
                        m.title,
                        m.url,
                        m.price as market_price,
                        m.volume as market_volume,
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
                        (SELECT w.address 
                         FROM trader_transactions tt
                         JOIN wallets w ON tt.wallet_address = w.address
                         WHERE tt.market_id = t.market_id
                           AND tt.timestamp > datetime('now', '-2 hours')
                           AND w.win_rate >= ? AND w.n_trades >= ?
                         ORDER BY tt.amount_usd DESC LIMIT 1) as top_wallet
                    FROM trader_transactions t
                    JOIN markets m ON t.market_id = m.id
                    WHERE t.timestamp > datetime('now', '-4 hours')
                    GROUP BY t.market_id
                    HAVING (vol_prev > 20.0 OR vol_recent > 500.0) AND (vol_recent / vol_prev) >= ?
                )
                SELECT sm.*, wal.win_rate, wal.n_trades
                FROM spike_markets sm
                LEFT JOIN wallets wal ON wal.address = sm.top_wallet
            """, (min_whale_win_rate, min_whale_trades, min_spike_ratio)).fetchall()
    except Exception as e:
        logger.error(f"[OnchainTrend] Ошибка при SQL-запросе spikes: {e}")
        return []

    spikes = []
    for row in rows:
        processed = _process_spike_row(row, min_market_price, max_market_price, base_bonus, min_vol, min_whale_win_rate, min_whale_trades)
        if processed:
            spikes.append(processed)
    return spikes

def _evaluate_wallet_confidence(win_rate: float, n_trades: int, is_insider: bool, min_win_rate: float, min_trades: int, amount_usd: float = 0.0) -> tuple[float, float]:
    if is_insider:
        return 0.85, 1.5
    if win_rate >= min_win_rate and n_trades >= min_trades:
        return 0.65, 1.0
    # Градация по размеру ставки
    if amount_usd >= 20000.0:
        return 0.65, 0.9
    elif amount_usd >= 5000.0:
        return 0.60, 0.8  # текущий фоллбэк
    elif amount_usd >= 1000.0:
        return 0.45, 0.5  # новая промежуточная зона
    return 0.3, 0.5

def _process_single_bet_row(row: dict, min_vol: float, min_win_rate: float, min_trades: int, min_market_price: float, max_market_price: float, base_bonus: float) -> Optional[dict]:
    row = dict(row)
    alert_key = f"whale_single_bet_{row['market_id']}_{row['wallet_address']}_{row['amount_usd']:.0f}"
    if is_alert_already_sent(alert_key, ttl_hours=2):
        return None

    # Phase 3.2: Market liquidity & extreme price filter
    m_price = row["market_price"] if row["market_price"] is not None else 0.5
    vol = row.get("market_volume") or 0.0
    if vol < min_vol or m_price <= min_market_price or m_price >= max_market_price:
        logger.info(f"Skipped whale signal (market filter): market_id={row['market_id']}, vol={vol}, price={m_price}, reason=LOW_VOL_OR_EXTREME_PRICE")
        return None
        
    # Phase 3.1: Wallet quality filter
    win_rate = row.get("win_rate") or 0.0
    n_trades = row.get("n_trades") or 0
    is_insider = row.get("is_insider")
    amount_usd = row.get("amount_usd") or 0.0
    confidence, bonus_mult = _evaluate_wallet_confidence(win_rate, n_trades, is_insider, min_win_rate, min_trades, amount_usd)
        
    # Skip creating trading signals for low-confidence whales
    if confidence < WHALE_GATE_MIN_CONFIDENCE:
        logger.info(f"Skipped whale signal (wallet filter): wallet={row['wallet_address']}, win_rate={win_rate}, n_trades={n_trades}, is_insider={is_insider}, amount={amount_usd}, reason=LOW_CONF")
        return None

    try:
        side = row["outcome"]
        if str(side).upper() not in ("YES", "NO"):
            logger.warning(f"Unknown outcome '{side}' in single bet, skipping")
            return None
        is_yes = str(side).upper() in ["YES", "1", "TRUE"]
        
        if row["price"] is not None:
            entry_price = row["price"]
            price_yes = row["price"] if is_yes else (1.0 - row["price"])
        else:
            price_yes = m_price
            entry_price = price_yes if is_yes else (1.0 - price_yes)
            
        bonus = base_bonus * bonus_mult
        prob = min(0.97, price_yes + bonus) if is_yes else max(0.03, (1.0 - price_yes) + bonus)
        
        summary_msg = f"Whale single bet: ${row['amount_usd']:,.0f} on {side} by {row['wallet_address'][:8]}... on {row['title']}"
        logger.info(f"[OnchainTrend] {summary_msg} (conf={confidence})")
        
        _log_whale_signal_to_eval(
            market_id=row['market_id'],
            side=side,
            prob=prob,
            entry_price=entry_price,
            summary_msg=summary_msg,
            metadata_extra={
                "wallet_address": row['wallet_address'],
                "amount_usd": row['amount_usd'],
                "reason": "large_single_bet",
                "win_rate": win_rate,
                "n_trades": n_trades,
                "is_insider": is_insider,
                "confidence_score": confidence
            },
            title=row.get('title'),
            url=row.get('url'),
            is_single=True,
            close_time=_normalize_close_time(row.get('close_time')),
            confidence=confidence
        )

        mark_alert_sent(alert_key, "whale_single_bet")

    except Exception as e:
        logger.error(f"[OnchainTrend] Ошибка обработки строки крупной сделки: {e}", exc_info=True)
        return None
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
                    m.volume as market_volume,
                    m.close_time,
                    w.win_rate,
                    w.n_trades,
                    w.is_insider
                FROM trader_transactions t
                JOIN markets m ON t.market_id = m.id
                LEFT JOIN wallets w ON t.wallet_address = w.address
                WHERE t.timestamp > datetime('now', '-2 hours')
                  AND t.amount_usd > 1000.0
            """).fetchall()
    except Exception as e:
        logger.error(f"[OnchainTrend] Ошибка при SQL-запросе крупных сделок: {e}")
        return []

    from agents.shared.python.db import get_whale_settings
    settings = get_whale_settings()
    min_vol = float(settings.get('min_market_volume', 5000.0))
    min_win_rate = float(settings.get('min_whale_win_rate', 0.60))
    min_trades = int(settings.get('min_whale_trades', 20))
    min_market_price = float(settings.get('min_market_price', 0.05))
    max_market_price = float(settings.get('max_market_price', 0.95))
    base_bonus = float(settings.get('whale_edge_bonus', 0.0))

    signals = []
    for row in rows:
        processed = _process_single_bet_row(row, min_vol, min_win_rate, min_trades, min_market_price, max_market_price, base_bonus)
        if processed:
            signals.append(processed)
    return signals

def _process_wallet_series_row(row: dict, min_vol: float, min_win_rate: float, min_trades: int, min_market_price: float, max_market_price: float, base_bonus: float) -> Optional[dict]:
    row = dict(row)
    alert_key = f"whale_series_{row['market_id']}_{row['wallet_address']}"
    if is_alert_already_sent(alert_key, ttl_hours=2):
        return None

    # Phase 3.2: Market liquidity & extreme price filter
    m_price = row["market_price"] if row["market_price"] is not None else 0.5
    vol = row.get("market_volume") or 0.0
    if vol < min_vol or m_price <= min_market_price or m_price >= max_market_price:
        logger.info(f"Skipped whale series (market filter): market_id={row['market_id']}, vol={vol}, price={m_price}, reason=LOW_VOL_OR_EXTREME_PRICE")
        return None
        
    win_rate = row.get("win_rate") or 0.0
    n_trades = row.get("n_trades") or 0
    is_insider = row.get("is_insider")
    amount_usd = row.get("total_amount_usd") or 0.0
    confidence, bonus_mult = _evaluate_wallet_confidence(win_rate, n_trades, is_insider, min_win_rate, min_trades, amount_usd)
        
    if confidence < WHALE_GATE_MIN_CONFIDENCE:
        logger.info(f"Skipped whale series (wallet filter): wallet={row['wallet_address']}, win_rate={win_rate}, n_trades={n_trades}, is_insider={is_insider}, amount={amount_usd}, reason=LOW_CONF")
        return None

    try:
        side = "YES" if row["yes_vol"] > row["no_vol"] else "NO"
        is_yes = str(side).upper() in ["YES", "1", "TRUE"]
        
        if row["avg_price"] is not None:
            entry_price = row["avg_price"]
            price_yes = row["avg_price"] if is_yes else (1.0 - row["avg_price"])
        else:
            price_yes = m_price
            entry_price = price_yes if is_yes else (1.0 - price_yes)
            
        bonus = base_bonus * bonus_mult
        prob = min(0.97, price_yes + bonus) if is_yes else max(0.03, (1.0 - price_yes) + bonus)
        
        summary_msg = f"Whale series: ${row['total_amount_usd']:,.0f} ({row['tx_count']} txs) on {side} by {row['wallet_address'][:8]}... on {row['title']}"
        logger.info(f"[OnchainTrend] {summary_msg} (conf={confidence})")
        
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
                "reason": "wallet_series",
                "win_rate": win_rate,
                "n_trades": n_trades,
                "is_insider": is_insider,
                "confidence_score": confidence
            },
            title=row.get('title'),
            url=row.get('url'),
            is_series=True,
            close_time=_normalize_close_time(row.get('close_time')),
            confidence=confidence
        )

        mark_alert_sent(alert_key, "whale_series")

    except Exception as e:
        logger.error(f"[OnchainTrend] Ошибка обработки строки серии сделок: {e}", exc_info=True)
        return None
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
                    m.volume as market_volume,
                    m.close_time,
                    w.win_rate,
                    w.n_trades,
                    w.is_insider
                FROM trader_transactions t
                JOIN markets m ON t.market_id = m.id
                LEFT JOIN wallets w ON t.wallet_address = w.address
                WHERE t.timestamp > datetime('now', '-1 hour')
                GROUP BY t.market_id, t.wallet_address
                HAVING tx_count >= 3 AND total_amount_usd > 2000.0
            """).fetchall()
    except Exception as e:
        logger.error(f"[OnchainTrend] Ошибка при SQL-запросе серий сделок: {e}")
        return []

    from agents.shared.python.db import get_whale_settings
    settings = get_whale_settings()
    min_vol = float(settings.get('min_market_volume', 5000.0))
    min_win_rate = float(settings.get('min_whale_win_rate', 0.60))
    min_trades = int(settings.get('min_whale_trades', 20))
    min_market_price = float(settings.get('min_market_price', 0.05))
    max_market_price = float(settings.get('max_market_price', 0.95))
    base_bonus = float(settings.get('whale_edge_bonus', 0.0))

    signals = []
    for row in rows:
        processed = _process_wallet_series_row(row, min_vol, min_win_rate, min_trades, min_market_price, max_market_price, base_bonus)
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
