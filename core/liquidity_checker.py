from dataclasses import dataclass
from typing import Optional

@dataclass
class LiquidityCheck:
    ok: bool
    reason: str = ""

def check_liquidity_fast(orderbook: dict) -> LiquidityCheck:
    """Быстрая проверка ликвидности по стакану без LLM."""
    if not orderbook:
        return LiquidityCheck(ok=False, reason="Стакан пуст")
    
    # Извлекаем и безопасно конвертируем в float
    try:
        bid = float(orderbook.get("bid_depth_5") or 0.0)
    except (ValueError, TypeError):
        bid = 0.0
        
    try:
        ask = float(orderbook.get("ask_depth_5") or 0.0)
    except (ValueError, TypeError):
        ask = 0.0
    
    # Спред может быть в cents или в spread (где 0.01 = 1 цент)
    spread_raw = orderbook.get("spread_cents")
    if spread_raw is None:
        spread_raw = orderbook.get("spread")
        if spread_raw is not None:
            try:
                s_val = float(spread_raw)
                # Если значение больше 1.0, считаем его уже в центах, иначе переводим
                spread = s_val if s_val > 1.0 else s_val * 100.0
            except (ValueError, TypeError):
                spread = 100.0
        else:
            spread = 100.0
    else:
        try:
            spread = float(spread_raw)
        except (ValueError, TypeError):
            spread = 100.0

    if bid + ask < 50:   # меньше $50 ликвидности
        return LiquidityCheck(ok=False, reason=f"Низкая ликвидность: bid+ask={bid+ask:.0f}")
    if spread > 20:       # спред > 20 центов — слишком дорого
        return LiquidityCheck(ok=False, reason=f"Широкий спред: {spread:.1f}¢")
    
    return LiquidityCheck(ok=True)
