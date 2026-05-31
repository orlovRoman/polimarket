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
    
    bid = orderbook.get("bid_depth_5", 0) or 0
    ask = orderbook.get("ask_depth_5", 0) or 0
    
    # Спред может быть в cents или в spread (где 0.01 = 1 цент)
    spread = orderbook.get("spread_cents")
    if spread is None:
        s_val = orderbook.get("spread")
        if s_val is not None:
            # Если значение больше 1.0, считаем его уже в центах, иначе переводим
            spread = s_val if s_val > 1.0 else s_val * 100.0
        else:
            spread = 100.0

    # Нормализуем, если значения None
    if bid is None:
        bid = 0.0
    if ask is None:
        ask = 0.0
    if spread is None:
        spread = 100.0

    if bid + ask < 50:   # меньше $50 ликвидности
        return LiquidityCheck(ok=False, reason=f"Низкая ликвидность: bid+ask={bid+ask:.0f}")
    if spread > 20:       # спред > 20 центов — слишком дорого
        return LiquidityCheck(ok=False, reason=f"Широкий спред: {spread:.1f}¢")
    
    return LiquidityCheck(ok=True)
