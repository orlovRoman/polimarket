from dataclasses import dataclass

@dataclass
class LiquidityResult:
    ok: bool
    reason: str
    confidence: float
    liquidity_risk: str  # "low" | "medium" | "high"

def check_liquidity_fast(orderbook: dict | None) -> LiquidityResult:
    """Детерминированная проверка — без LLM."""
    if not orderbook:
        return LiquidityResult(
            ok=False, reason="Ордербук недоступен",
            confidence=0.25, liquidity_risk="high"
        )
    spread = orderbook.get('spread')
    if spread is None or spread > 0.05:
        spread_val = spread if spread is not None else 0.0
        return LiquidityResult(
            ok=False, reason=f"Спред {spread_val:.3f} > 5%",
            confidence=0.30, liquidity_risk="high"
        )
    depth = min(
        orderbook.get('bid_depth_5', 0),
        orderbook.get('ask_depth_5', 0)
    )
    if depth < 50:
        return LiquidityResult(
            ok=False, reason=f"Глубина {depth:.0f} < $50",
            confidence=0.35, liquidity_risk="medium"
        )
    return LiquidityResult(
        ok=True, reason=f"Спред {spread:.3f}, глубина {depth:.0f}",
        confidence=0.70, liquidity_risk="low"
    )
