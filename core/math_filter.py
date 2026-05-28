from enum import Enum
from dataclasses import dataclass
import re
from typing import Optional
from core.models import Market

class FilterDecision(Enum):
    CONFIRMED_ARBITRAGE = "CONFIRMED_ARBITRAGE"
    CONFIRMED_NO_ARBI = "CONFIRMED_NO_ARBI"
    AMBIGUOUS = "AMBIGUOUS"

@dataclass(frozen=True)
class MathFilterResult:
    decision: FilterDecision
    arbitrage_type: str
    spread_pct: float
    reasoning: str
    trade_instruction: str
    has_arbitrage: bool = False

def _parse_threshold(title: str) -> Optional[tuple[float, str]]:
    title = title.lower()
    
    # Absolute currency: $1.8T, $500B, $50M, $500K
    m = re.search(r'\$?([\d\.]+)\s*([tbmk])\b', title)
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        multiplier = {'t': 1e12, 'b': 1e9, 'm': 1e6, 'k': 1e3}
        return (val * multiplier[unit], 'usd')
        
    # Percentages: 5%, 4.5%
    m = re.search(r'([\d\.]+)\s*%', title)
    if m:
        return (float(m.group(1)), '%')
        
    # Indexes / Absolute numbers: 6000, 5500 (3+ digits to avoid matching small random numbers like years if not careful, though years are 4 digits. Let's just match any 3+ digit number not preceded by certain characters, or just any number if context allows. User said "3+ digits")
    m = re.search(r'\b(\d{3,}(?:\.\d+)?)\b', title)
    if m:
        # Avoid years like 2024, 2025, 2026, 2027, 2028 if they are the only number
        # Actually user spec: "6000, 5500 (индексы, 3+ цифр) -> (6000.0, 'pts')"
        # We can just check if it's not a year 202x
        val = float(m.group(1))
        if 2020 <= val <= 2030:
            return None
        return (val, 'pts')
            
    return None

def _same_unit(u1: str, u2: str) -> bool:
    if u1 == 'usd' and u2 == 'usd': return True
    if u1 == '%' and u2 == '%': return True
    if u1 == 'pts' and u2 == 'pts': return True
    return False

def _looks_complementary(title_a: str, title_b: str) -> bool:
    a = title_a.lower()
    b = title_b.lower()
    
    pairs = [
        ('democrat', 'republican'),
        ('trump', 'harris'),
        ('trump', 'biden'),
        ('kamala', 'trump'),
        ('win', 'lose')
    ]
    for w1, w2 in pairs:
        if (w1 in a and w2 in b) or (w2 in a and w1 in b):
            return True
    return False

def math_pre_filter(market_a: Market, market_b: Market, min_spread_pct: float = 5.0) -> MathFilterResult:
    # 1. Monotonicity
    t_a = _parse_threshold(market_a.title)
    t_b = _parse_threshold(market_b.title)
    
    if t_a and t_b and _same_unit(t_a[1], t_b[1]) and t_a[0] != t_b[0]:
        if t_a[0] > t_b[0]:
            higher_market, lower_market = market_a, market_b
            p_higher, p_lower = market_a.price, market_b.price
        else:
            higher_market, lower_market = market_b, market_a
            p_higher, p_lower = market_b.price, market_a.price
            
        if p_higher > p_lower:
            spread = (p_higher - p_lower) * 100
            if spread >= min_spread_pct:
                instruction = f"SELL YES на [{lower_market.title}]({lower_market.url}) ({p_lower*100:.0f}¢) + SELL NO на [{higher_market.title}]({higher_market.url}) ({(1-p_higher)*100:.0f}¢). Суммарный сбор: {(p_lower + 1 - p_higher)*100:.0f}¢ → гарантированная выплата 100¢."
                return MathFilterResult(
                    decision=FilterDecision.CONFIRMED_ARBITRAGE,
                    arbitrage_type="monotonicity_violation",
                    spread_pct=spread,
                    reasoning=f"Нарушение монотонности: порог {max(t_a[0], t_b[0])} стоит дороже порога {min(t_a[0], t_b[0])}",
                    trade_instruction=instruction,
                    has_arbitrage=True
                )
            else:
                return MathFilterResult(
                    decision=FilterDecision.CONFIRMED_NO_ARBI,
                    arbitrage_type="false_positive",
                    spread_pct=spread,
                    reasoning=f"Нарушение монотонности есть, но спред {spread:.1f}% меньше минимального {min_spread_pct:.1f}%",
                    trade_instruction=""
                )
        elif p_higher <= p_lower:
            return MathFilterResult(
                decision=FilterDecision.CONFIRMED_NO_ARBI,
                arbitrage_type="false_positive",
                spread_pct=0.0,
                reasoning=f"Монотонность соблюдена: P(higher) {p_higher} <= P(lower) {p_lower}",
                trade_instruction=""
            )
            
    # 2. Complementary
    if _looks_complementary(market_a.title, market_b.title):
        price_sum = market_a.price + market_b.price
        if price_sum - 1.0 > 0.03 and (price_sum - 1.0) * 100 >= min_spread_pct:
            spread = (price_sum - 1.0) * 100
            instruction = f"SELL YES на [{market_a.title}]({market_a.url}) ({market_a.price*100:.0f}¢) + SELL YES на [{market_b.title}]({market_b.url}) ({market_b.price*100:.0f}¢). Суммарный сбор: {(price_sum)*100:.0f}¢ → гарантированная выплата 100¢."
            return MathFilterResult(
                decision=FilterDecision.CONFIRMED_ARBITRAGE,
                arbitrage_type="complementary_overpriced",
                spread_pct=spread,
                reasoning=f"Сумма взаимоисключающих исходов {price_sum:.2f} > 1.0",
                trade_instruction=instruction,
                has_arbitrage=True
            )
        if 1.0 - price_sum > 0.03 and (1.0 - price_sum) * 100 >= min_spread_pct:
            spread = (1.0 - price_sum) * 100
            instruction = f"BUY YES на [{market_a.title}]({market_a.url}) ({market_a.price*100:.0f}¢) + BUY YES на [{market_b.title}]({market_b.url}) ({market_b.price*100:.0f}¢). Суммарная стоимость: {price_sum*100:.0f}¢ → покупка ниже номинала, один из исходов выплатит 100¢."
            return MathFilterResult(
                decision=FilterDecision.AMBIGUOUS,
                arbitrage_type="complementary_underpriced",
                spread_pct=spread,
                reasoning=f"Сумма взаимоисключающих исходов {price_sum:.2f} < 1.0 (это favorable bet, требуется подтверждение LLM)",
                trade_instruction=instruction,
                has_arbitrage=False
            )
            
    # 3. Direct price divergence (Cross-platform)
    if market_a.platform != market_b.platform:
        direct_spread_pct = abs(market_a.price - market_b.price) * 100
        if direct_spread_pct < min_spread_pct:
            return MathFilterResult(
                decision=FilterDecision.CONFIRMED_NO_ARBI,
                arbitrage_type="false_positive",
                spread_pct=direct_spread_pct,
                reasoning=f"Расхождение цен {direct_spread_pct:.1f}% меньше минимального спреда {min_spread_pct:.1f}%",
                trade_instruction=""
            )
        else:
            return MathFilterResult(
                decision=FilterDecision.AMBIGUOUS,
                arbitrage_type="price_divergence",
                spread_pct=direct_spread_pct,
                reasoning=f"Значимое расхождение цен {direct_spread_pct:.1f}%, требуется подтверждение LLM",
                trade_instruction=""
            )

    # 4. Fallback
    return MathFilterResult(
        decision=FilterDecision.AMBIGUOUS,
        arbitrage_type="unknown",
        spread_pct=0.0,
        reasoning="Математические фильтры не дали однозначного ответа",
        trade_instruction=""
    )
