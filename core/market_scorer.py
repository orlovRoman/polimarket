import math
from datetime import datetime, timezone
from typing import TypedDict

class CompactMarket(TypedDict):
    id: str
    p: float      # price
    vol: float    # volume
    end: str      # close_time ISO

def score_market(m: CompactMarket) -> float:
    """Детерминированный скоринг без LLM."""
    price = float(m.get('p', 0.5))
    vol   = float(m.get('vol', 0))
    score = 0.0

    # Неопределённость (максимум при price=0.5)
    uncertainty = 1.0 - abs(price - 0.5) * 2
    score += uncertainty * 40.0

    # Объём (логарифмическая шкала, max 30 очков)
    if vol > 0:
        score += min(math.log10(vol + 1) * 5.0, 30.0)

    # Временная близость (7–30 дней до закрытия)
    end_str = m.get('end', '')
    if end_str:
        try:
            end_dt = datetime.fromisoformat(str(end_str).replace('Z', '+00:00'))
            days = (end_dt - datetime.now(timezone.utc)).days
            if 7 <= days <= 30:
                score += 20.0
            elif days < 7:
                score += 8.0
        except (ValueError, AttributeError):
            pass

    return round(score, 4)

def screen_markets_code(markets: list[CompactMarket], top_n: int = 30) -> list[str]:
    """Возвращает top_n market_id без LLM."""
    scored = sorted(markets, key=score_market, reverse=True)
    return [m['id'] for m in scored[:top_n]]
