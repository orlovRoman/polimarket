import logging
from agents.shared.python.db import get_connection, is_alert_already_sent, mark_alert_sent

logger = logging.getLogger("OnchainTrend")

def scan_volume_spikes(min_spike_ratio: float = 3.0) -> list[dict]:
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
        if is_alert_already_sent(alert_key, ttl_hours=6):
            continue
        spikes.append(dict(row))
        mark_alert_sent(alert_key)
    return spikes


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
