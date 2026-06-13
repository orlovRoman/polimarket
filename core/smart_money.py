from collections import defaultdict
from typing import Optional, List, Dict, Any
from agents.shared.python.db import get_known_whales
from core.context import SmartMoneySummary

def analyze_smart_money(trades: List[Dict[str, Any]], positions: List[Dict[str, Any]]) -> SmartMoneySummary:
    """
    Возвращает структурированный анализ ончейн активности.
    """
    if not trades and not positions:
        return SmartMoneySummary(available=False, summary="Ончейн данные недоступны.")

    import time
    from datetime import datetime
    
    now_ts = time.time()
    two_hours_ago = now_ts - 2 * 3600
    
    def _parse_trade_time(val) -> float:
        if not val:
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
        try:
            dt = datetime.fromisoformat(str(val).replace('Z', '+00:00'))
            return dt.timestamp()
        except Exception:
            return 0.0

    # Агрегируем по кошелькам
    wallet_stats: dict = defaultdict(lambda: {"yes_usd": 0.0, "no_usd": 0.0, "trades": 0})
    
    total_volume_usd = 0.0
    recent_volume_usd = 0.0
    
    for trade in trades:
        addr = trade.get("maker_address") or trade.get("taker_address", "")
        if not addr:          # FIX #1: пропускаем анонимные трейды
            continue
        outcome = trade.get("outcome_index", 0)  # 0 соответствует YES, 1 соответствует NO
        
        try:
            size = float(trade.get("size", 0))
            price = float(trade.get("price", 0.5))
        except (ValueError, TypeError):
            continue
            
        usd = size * price
        total_volume_usd += usd
        
        # Проверяем временной интервал (2 часа)
        trade_time_raw = trade.get("time") or trade.get("timestamp")
        trade_ts = _parse_trade_time(trade_time_raw)
        if trade_ts >= two_hours_ago:
            recent_volume_usd += usd
        
        if outcome == 0:
            wallet_stats[addr]["yes_usd"] += usd
        else:
            wallet_stats[addr]["no_usd"] += usd
        wallet_stats[addr]["trades"] += 1

    # FIX #2: обрабатываем positions
    for pos in positions:
        addr = pos.get("proxy_wallet_address") or pos.get("wallet_address", "")
        if not addr:
            continue
        outcome = pos.get("outcome_index", 0)
        try:
            size = float(pos.get("size", 0))
            avg_price = float(pos.get("avg_price", 0.5))
        except (ValueError, TypeError):
            continue
        usd = size * avg_price
        if outcome == 0:
            wallet_stats[addr]["yes_usd"] += usd
        else:
            wallet_stats[addr]["no_usd"] += usd

    # Топ кошельки по объёму
    known_whales = {k.lower(): v for k, v in get_known_whales().items()}  # {address: {alias, win_rate}}
    top_wallets = sorted(wallet_stats.items(), key=lambda x: x[1]["yes_usd"] + x[1]["no_usd"], reverse=True)[:5]

    lines = []
    total_yes = sum(v["yes_usd"] for v in wallet_stats.values())
    total_no = sum(v["no_usd"] for v in wallet_stats.values())

    wallets_list = []
    from core.context import WalletInfo
    from core.insider_filter import evaluate_wallet
    for addr, stats in top_wallets:
        whale_info = known_whales.get(addr.lower(), {})
        alias = whale_info.get("alias", addr[:8] + "...")
        win_rate = whale_info.get("win_rate")
        
        # p-value фильтр (строго по истории из БД)
        n_trades = whale_info.get("n_trades") or 0
        n_wins   = whale_info.get("n_wins") or 0
        verdict  = evaluate_wallet(addr, n_trades, n_wins)

        insider_tag = " 🔴INSIDER" if verdict.is_insider else ""
        wr_str = f" | WR: {win_rate*100:.0f}% (p={verdict.p_value:.3f}){insider_tag}" \
                 if win_rate is not None else ""
                 
        side = "YES" if stats["yes_usd"] > stats["no_usd"] else "NO"
        vol = stats["yes_usd"] + stats["no_usd"]
        lines.append(f"  {alias}{wr_str} → {side} ${vol:,.0f}")
        
        wallets_list.append(WalletInfo(
            address=addr,
            alias=whale_info.get("alias"),
            win_rate=win_rate,
            side=side,
            volume_usd=vol,
            is_insider=verdict.is_insider
        ))

    recent_ratio = recent_volume_usd / total_volume_usd if total_volume_usd > 0 else 0.0
    
    # Добавляем в текстовое описание недавнюю активность
    recent_info = f"Недавняя активность (2ч): ${recent_volume_usd:,.0f} ({recent_ratio:.0%})" if total_volume_usd > 0 else "Нет недавней активности."
    summary_text = "\n".join(lines) if lines else "Крупных сделок не найдено."
    if lines:
        summary_text = f"{summary_text}\n{recent_info}"

    return SmartMoneySummary(
        available=True,
        total_yes_usd=round(total_yes),
        total_no_usd=round(total_no),
        yes_dominance=round(total_yes / (total_yes + total_no), 2) if (total_yes + total_no) > 0 else 0.5,
        top_wallets=lines,
        summary=summary_text,
        wallets_list=wallets_list,
        recent_volume_2h_usd=round(recent_volume_usd, 2),
        recent_ratio_2h=round(recent_ratio, 4)
    )


def fetch_smart_money_sync(market_id: str) -> Optional[SmartMoneySummary]:
    """
    Читает уже загруженные данные из БД (trader_transactions + wallets).
    НЕ делает HTTP-запросов — только SELECT.
    Вызывается синхронно из run_agent_evaluation.
    """
    try:
        from collections import defaultdict
        from core.context import WalletInfo
        from agents.shared.python.db import get_connection

        with get_connection() as conn:
            rows = conn.execute("""
                SELECT tt.wallet_address, tt.amount_usd, tt.outcome, w.alias, w.win_rate, w.is_insider
                FROM trader_transactions tt
                JOIN wallets w ON tt.wallet_address = w.address
                WHERE tt.market_id = ?
                  AND tt.timestamp > datetime('now', '-48 hours')
            """, (market_id,)).fetchall()

        if not rows:
            return SmartMoneySummary(available=False, summary="Ончейн данные в локальной БД не найдены.")

        # Агрегируем по кошелькам
        wallets_data = defaultdict(lambda: {"yes_usd": 0.0, "no_usd": 0.0, "alias": None, "win_rate": None, "is_insider": False})
        for r in rows:
            addr = r["wallet_address"]
            outcome = r["outcome"]
            amount = r["amount_usd"]
            wallets_data[addr]["alias"] = r["alias"]
            wallets_data[addr]["win_rate"] = r["win_rate"]
            wallets_data[addr]["is_insider"] = bool(r["is_insider"])
            if outcome == "YES":
                wallets_data[addr]["yes_usd"] += amount
            else:
                wallets_data[addr]["no_usd"] += amount

        wallets_list = []
        total_yes = 0.0
        total_no = 0.0
        lines = []

        for addr, data in wallets_data.items():
            yes_vol = data["yes_usd"]
            no_vol = data["no_usd"]
            vol = yes_vol + no_vol
            side = "YES" if yes_vol >= no_vol else "NO"
            total_yes += yes_vol
            total_no += no_vol
            
            wallets_list.append(WalletInfo(
                address=addr,
                alias=data["alias"],
                win_rate=data["win_rate"],
                side=side,
                volume_usd=vol,
                is_insider=data["is_insider"]
            ))
            
            insider_tag = " 🔴INSIDER" if data["is_insider"] else ""
            wr_str = f" | WR: {data['win_rate']*100:.0f}%{insider_tag}" if data['win_rate'] is not None else ""
            lines.append(f"  {data['alias'] or addr[:8] + '...'}{wr_str} → {side} ${vol:,.0f}")

        total = total_yes + total_no
        yes_dominance = total_yes / total if total > 0 else 0.5

        return SmartMoneySummary(
            available=True,
            total_yes_usd=total_yes,
            total_no_usd=total_no,
            yes_dominance=yes_dominance,
            top_wallets=lines[:5],
            summary="\n".join(lines[:5]),
            wallets_list=wallets_list
        )
    except Exception as e:
        from config import logger
        logger.warning(f"[SmartMoney] fetch_sync ошибка: {e}", exc_info=True)
        return None


async def refresh_known_whales_from_holders(condition_id: str) -> int:
    """
    Запрашивает топ-20 холдеров рынка через data-api и
    upsert-ит их в wallets как кандидатов (без win_rate до первого resolve).
    Вызывается из background worker, не из основного цикла.
    """
    import httpx
    from agents.shared.python.db import get_connection
    from config import logger

    url = f"https://data-api.polymarket.com/holders?market={condition_id}&limit=20"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            for token_id in ["0", "1"]:  # YES и NO токены
                resp = await client.get(url + f"&tokenId={token_id}")
                holders = resp.json() if resp.status_code == 200 else []
                if not isinstance(holders, list):
                    holders = []
                with get_connection() as conn:
                    for h in holders:
                        addr = h.get("proxyWallet", "").lower()
                        if not addr:
                            continue
                        conn.execute("""
                            INSERT OR IGNORE INTO wallets
                            (address, alias, win_rate, last_seen)
                            VALUES (?, ?, NULL, CURRENT_TIMESTAMP)
                        """, (addr, h.get("pseudonym", addr[:8])))
    except Exception as e:
        logger.warning(f"[SmartMoney] refresh_known_whales ошибка: {e}")
        return 0
    return 1
