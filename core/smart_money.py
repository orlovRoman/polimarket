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

    # Агрегируем по кошелькам
    wallet_stats: dict = defaultdict(lambda: {"yes_usd": 0.0, "no_usd": 0.0, "trades": 0})
    
    for trade in trades:
        addr = trade.get("maker_address") or trade.get("taker_address", "")
        outcome = trade.get("outcome_index", 0)  # 0=YES, 1=NO
        
        try:
            size = float(trade.get("size", 0))
            price = float(trade.get("price", 0.5))
        except (ValueError, TypeError):
            continue
            
        usd = size * price
        
        if outcome == 0:
            wallet_stats[addr]["yes_usd"] += usd
        else:
            wallet_stats[addr]["no_usd"] += usd
        wallet_stats[addr]["trades"] += 1

    # Топ кошельки по объёму
    known_whales = get_known_whales()  # {address: {alias, win_rate}}
    top_wallets = sorted(wallet_stats.items(), key=lambda x: x[1]["yes_usd"] + x[1]["no_usd"], reverse=True)[:5]

    lines = []
    total_yes = sum(v["yes_usd"] for v in wallet_stats.values())
    total_no = sum(v["no_usd"] for v in wallet_stats.values())

    for addr, stats in top_wallets:
        whale_info = known_whales.get(addr, {})
        alias = whale_info.get("alias", addr[:8] + "...")
        win_rate = whale_info.get("win_rate")
        wr_str = f" | WR: {win_rate*100:.0f}%" if win_rate else ""
        side = "YES" if stats["yes_usd"] > stats["no_usd"] else "NO"
        vol = stats["yes_usd"] + stats["no_usd"]
        lines.append(f"  {alias}{wr_str} → {side} ${vol:,.0f}")

    return SmartMoneySummary(
        available=True,
        total_yes_usd=round(total_yes),
        total_no_usd=round(total_no),
        yes_dominance=round(total_yes / (total_yes + total_no), 2) if (total_yes + total_no) > 0 else 0.5,
        top_wallets=lines,
        summary="\n".join(lines) if lines else "Крупных сделок не найдено."
    )
