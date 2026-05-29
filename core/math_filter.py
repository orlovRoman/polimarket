from enum import Enum
from dataclasses import dataclass
import re
from typing import Optional
import logging
from core.models import Market

logger = logging.getLogger("math_filter")

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
        from datetime import datetime
        current_year = datetime.now().year
        if current_year - 2 <= val <= current_year + 10:
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
    
    explicit_pairs = [
        ('democrat', 'republican'),
        ('trump', 'harris'),
        ('trump', 'biden'),
        ('kamala', 'trump')
    ]
    for w1, w2 in explicit_pairs:
        if (w1 in a and w2 in b) or (w2 in a and w1 in b):
            return True
            
    directional_pairs = [
        ('above', 'below'),
        ('over', 'under'),
        ('more than', 'less than')
    ]
    words_a = set(re.findall(r'\b\w+\b', a))
    words_b = set(re.findall(r'\b\w+\b', b))
    common = (words_a & words_b) - {'will', 'the', 'a', 'in', 'by', 'of', 'to', 'at', 'on', 'for'}
    
    for w1, w2 in directional_pairs:
        if ((w1 in a and w2 in b) or (w2 in a and w1 in b)) and len(common) >= 2:
            return True
            
    return False

def validate_trade_instruction(instruction: str) -> tuple[bool, str]:
    """
    Проверяет, что трейд-инструкция содержит только исполнимые
    на Polymarket операции: BUY YES или BUY NO.
    
    SELL YES / SELL NO допустимы только при наличии позиции — 
    система не отслеживает позиции, поэтому запрещаем шорты.
    
    Returns: (is_valid, reason)
    """
    forbidden = ["SELL YES", "SELL NO", "SHORT"]
    for op in forbidden:
        if op.upper() in instruction.upper():
            return False, f"Недопустимая операция '{op}' — шорт на Polymarket невозможен без открытой позиции."
    return True, "OK"

def _check_same_event(title_a: str, title_b: str) -> bool:
    """
    Грубая проверка: описывают ли рынки одно событие.
    Если ключевые слова сильно расходятся — это разные события.
    """
    stopwords = {'will', 'the', 'a', 'in', 'by', 'of', 'to', 'at', 'on', 
                 'for', 'above', 'below', 'over', 'hit', 'reach', 'exceed'}
    a_words = set(re.findall(r'\b\w+\b', title_a.lower())) - stopwords
    b_words = set(re.findall(r'\b\w+\b', title_b.lower())) - stopwords
    # Числа исключаем — они не являются идентификаторами события
    a_words = {w for w in a_words if not re.match(r'^\d+$', w)}
    b_words = {w for w in b_words if not re.match(r'^\d+$', w)}
    if not a_words or not b_words:
        return False
    overlap = len(a_words & b_words) / min(len(a_words), len(b_words))
    return overlap >= 0.40  # минимум 40% совпадение ключевых слов

def math_pre_filter(market_a: Market, market_b: Market, min_spread_pct: float = 5.0, check_logical_implication: bool = False) -> MathFilterResult:
    # 1. Monotonicity
    t_a = _parse_threshold(market_a.title)
    t_b = _parse_threshold(market_b.title)
    
    # NEW: одинаковые пороги — монотонность неприменима
    if t_a and t_b and _same_unit(t_a[1], t_b[1]) and t_a[0] == t_b[0]:
        return MathFilterResult(
            decision=FilterDecision.CONFIRMED_NO_ARBI,
            arbitrage_type="identical_threshold",
            spread_pct=0.0,
            reasoning=(
                f"Оба рынка упоминают порог {t_a[0]:.0f} — одинаковое число НЕ означает "
                f"логическую импликацию. Это могут быть разные события, разные оракулы, "
                f"разные временны́е условия. Спред=0%, LLM вызов экономим."
            ),
            trade_instruction=""
        )

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
                instruction = (
                    f"⚠️ Требует открытой позиции: "
                    f"SELL YES на [{higher_market.title}] ({p_higher*100:.0f}¢) "
                    f"при наличии позиции. Альтернатива: сигнал LLM для BUY YES на [{lower_market.title}] ({p_lower*100:.0f}¢) "
                    f"как недооценённый рынок. Спред: {spread:.1f}%."
                )
                return MathFilterResult(
                    decision=FilterDecision.AMBIGUOUS,
                    arbitrage_type="monotonicity_violation",
                    spread_pct=spread,
                    reasoning=f"Нарушение монотонности: порог {max(t_a[0], t_b[0])} стоит дороже порога {min(t_a[0], t_b[0])}",
                    trade_instruction=instruction,
                    has_arbitrage=False
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
            instruction = (
                f"BUY NO на [{market_a.title}]({market_a.url}) ({(1-market_a.price)*100:.0f}¢) "
                f"+ BUY NO на [{market_b.title}]({market_b.url}) ({(1-market_b.price)*100:.0f}¢). "
                f"Суммарная стоимость: {(2 - price_sum)*100:.0f}¢ → гарантированная выплата 100¢."
            )
            is_valid, reason = validate_trade_instruction(instruction)
            if not is_valid:
                logger.warning(f"[math_filter] Невалидный трейд отклонён: {reason}")
                return MathFilterResult(
                    decision=FilterDecision.AMBIGUOUS,
                    arbitrage_type="complementary_overpriced",
                    spread_pct=spread,
                    reasoning=f"Сумма взаимоисключающих исходов {price_sum:.2f} > 1.0",
                    trade_instruction=f"⚠️ Трейд недоступен: {reason}",
                    has_arbitrage=False
                )
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
            instruction = (
                f"BUY YES на [{market_a.title}]({market_a.url}) ({market_a.price*100:.0f}¢) "
                f"+ BUY YES на [{market_b.title}]({market_b.url}) ({market_b.price*100:.0f}¢). "
                f"Суммарная стоимость: {price_sum*100:.0f}¢ → покупка ниже номинала, "
                f"один из исходов выплатит 100¢."
            )
            # BUY YES всегда валиден — validate здесь не нужна
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

    # 3b. Logical implication (A ⊃ B) — только на одной платформе
    if check_logical_implication and market_a.platform == market_b.platform:
        # NEW: проверяем что это одно событие
        if not _check_same_event(market_a.title, market_b.title):
            return MathFilterResult(
                decision=FilterDecision.CONFIRMED_NO_ARBI,
                arbitrage_type="different_events",
                spread_pct=0.0,
                reasoning="Рынки описывают разные события — логическая импликация неприменима.",
                trade_instruction=""
            )
        p_a, p_b = market_a.price, market_b.price
        implication_spread = abs(p_a - p_b) * 100
        if implication_spread >= min_spread_pct:
            # Определяем какой рынок "следствие" (более дешёвый — потенциально недооценён)
            if p_a > p_b:
                anchor, underpriced = market_a, market_b
                p_anchor, p_under = p_a, p_b
            else:
                anchor, underpriced = market_b, market_a
                p_anchor, p_under = p_b, p_a
            
            instruction = (
                f"BUY YES на [{underpriced.title}]({underpriced.url}) "
                f"({p_under*100:.0f}¢). "
                f"Обоснование: если '{anchor.title}' реализуется с вероятностью {p_anchor*100:.0f}¢, "
                f"то связанное событие не может стоить меньше. "
                f"⚠️ Это НЕ гарантированный арбитраж — только вероятностная ставка на логическую связь."
            )
            is_valid, reason = validate_trade_instruction(instruction)
            if not is_valid:
                logger.warning(f"[math_filter] Невалидный трейд отклонён: {reason}")
                instruction = f"⚠️ Трейд недоступен: {reason}"
                
            return MathFilterResult(
                decision=FilterDecision.AMBIGUOUS,
                arbitrage_type="logical_implication",
                spread_pct=implication_spread,
                reasoning=(
                    f"Логическая импликация: если '{anchor.title}' ({p_anchor:.2f}) реализуется, "
                    f"'{underpriced.title}' ({p_under:.2f}) должен стоить не меньше. "
                    f"Разрыв {implication_spread:.1f}%. "
                    f"ВАЖНО: шорт первого рынка невозможен на Polymarket."
                ),
                trade_instruction=instruction,
                has_arbitrage=False
            )
        else:
            return MathFilterResult(
                decision=FilterDecision.CONFIRMED_NO_ARBI,
                arbitrage_type="logical_implication",
                spread_pct=implication_spread,
                reasoning=f"Логическая связь есть, но спред {implication_spread:.1f}% ниже порога",
                trade_instruction="",
                has_arbitrage=False
            )

    # 4. Fallback
    return MathFilterResult(
        decision=FilterDecision.AMBIGUOUS,
        arbitrage_type="unknown",
        spread_pct=0.0,
        reasoning="Математические фильтры не дали однозначного ответа",
        trade_instruction=""
    )
