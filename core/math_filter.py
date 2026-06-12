from enum import Enum
from dataclasses import dataclass
import re
from datetime import datetime as _dt
from typing import Optional
import logging
from core.models import Market

logger = logging.getLogger("NexusPolyBot.math_filter")

_COMMON_STOPWORDS = frozenset({
    'will', 'the', 'a', 'an', 'in', 'by', 'of', 'to', 'at', 'on', 'for',
    'be', 'is', 'are', 'was', 'its',
})

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
    
    # 1. Сокращения валют без пробела: $1.8T, $500B, $50M, $500K
    m = re.search(r'\$?([\d\.]+)([tbmk])\b', title)
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        multiplier = {'t': 1e12, 'b': 1e9, 'm': 1e6, 'k': 1e3}
        return (val * multiplier[unit], 'usd')
        
    # 2. Полные названия денежных единиц: $2 billion, $10 million
    _WORD_MULT = {
        'trillion': 1e12, 'billion': 1e9, 'million': 1e6, 'thousand': 1e3
    }
    m2 = re.search(r'\$?([\d\.]+)\s+(trillion|billion|million|thousand)\b', title)
    if m2:
        val = float(m2.group(1))
        return (val * _WORD_MULT[m2.group(2)], 'usd')
        
    # Percentages: 5%, 4.5%
    m = re.search(r'([\d\.]+)\s*%', title)
    if m:
        return (float(m.group(1)), '%')
        
    # БАГ 4 ФИКС: двухпроходная стратегия для индексов/абсолютных чисел.
    # Проблема: "S&P 500 above 5500" — regex находил 500 (из тикера) раньше, чем 5500 (порог).
    _cur_year = _dt.now().year

    def _is_year(v: float) -> bool:
        return _cur_year - 2 <= v <= _cur_year + 10

    # Проход 1: ищем число сразу после контекстного слова-порога.
    # Используем finditer (а не search), чтобы перебрать ВСЕ матчи: если первый оказался годом,
    # пробуем следующий — вместо того чтобы сразу падать в max()-фоллбэк.
    _CTX = r'(?:above|below|over|under|hit|reach(?:es)?|at|exceed(?:s)?|cross(?:es)?|surpass(?:es)?|top(?:s)?)'
    _ctx_pattern = rf'{_CTX}\s+\$?([\d]{{3,}}(?:,\d{{3}})*(?:\.\d+)?)\b'
    for _m in re.finditer(_ctx_pattern, title):
        try:
            val = float(_m.group(1).replace(',', ''))
            if not _is_year(val):
                return (val, 'pts')
        except ValueError:
            pass

    # Проход 2: истинный fallback — контекстных слов нет, берём max() из всех 3+ цифр.
    # Примечание: max() не идеален, но в остатке лучшей эвристики нет: порог обычно крупнее случайных чисел.
    _candidates = re.findall(r'\b(\d{3,}(?:\.\d+)?)\b', title)
    _valid = []
    for _c in _candidates:
        try:
            _v = float(_c)
            if not _is_year(_v):
                _valid.append(_v)
        except ValueError:
            pass

    if _valid:
        return (max(_valid), 'pts')

    return None

def _same_unit(u1: str, u2: str) -> bool:
    if u1 == 'usd' and u2 == 'usd': return True
    if u1 == '%' and u2 == '%': return True
    if u1 == 'pts' and u2 == 'pts': return True
    return False

def _looks_complementary(title_a: str, title_b: str) -> bool:
    a = title_a.lower()
    b = title_b.lower()
    
    # Проверяем явные пары (democrat/republican) с границами слов \b
    # Чтобы избежать ложных срабатываний на demand, democracy, demographic
    explicit_patterns = [
        (r'\bdemocrats?\b|\bdemocratic\b', r'\brepublicans?\b|\bgop\b'),
        (r'\bdem\b', r'\brep\b')
    ]
    for p1, p2 in explicit_patterns:
        if (re.search(p1, a) and re.search(p2, b)) or (re.search(p2, a) and re.search(p1, b)):
            return True
            
    directional_pairs = [
        ('above', 'below'),
        ('over', 'under'),
        ('more than', 'less than')
    ]
    words_a = set(re.findall(r'\b\w+\b', a))
    words_b = set(re.findall(r'\b\w+\b', b))
    
    _COMPLEMENTARY_STOPWORDS = _COMMON_STOPWORDS | {
        'nominee', 'primary', 'win', 'wins', 'race', 'candidate',
        'election', 'vote', 'poll', 'percent', 'seats', 'electoral',
    }
    common = (words_a & words_b) - _COMPLEMENTARY_STOPWORDS
    
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

def _check_same_event(title_a: str, title_b: str, allow_different_dates: bool = False,
                      market_a=None, market_b=None) -> bool:
    """
    Грубая проверка: описывают ли рынки одно событие.
    Использует три слоя фильтрации:
    1. Layer 1: event_slug (Polymarket-only)
    2. Layer 2: Семантические эмбеддинги all-MiniLM-L6-v2
    3. Layer 2b (fallback): Regex-оценка пересечения ключевых слов.
    """
    # Layer 1: event_slug (0ms, 100% точность для Polymarket)
    slug_a = getattr(market_a, 'event_slug', None)
    slug_b = getattr(market_b, 'event_slug', None)
    if slug_a and slug_b:
        return slug_a == slug_b

    # Layer 2: Embedding similarity (5ms, кросс-платформенный)
    try:
        from core.semantic_filter import semantic_same_event
        semantic_result = semantic_same_event(title_a, title_b)
        if semantic_result is True:
            return True
        if semantic_result is False:
            return False
        if semantic_result is None:
            logger.debug(
                f"[check_same_event] Серая зона (0.65-0.75) или модель недоступна, "
                f"fallback на regex: '{title_a[:40]}' vs '{title_b[:40]}'"
            )
    except Exception as e:
        logger.warning(f"Error in semantic same-event check: {e}")

    # Layer 2b (fallback): Regex-оценка пересечения ключевых слов
    if not allow_different_dates:
        # Мгновенно отсекаем разные годы или разные кварталы
        years_a = set(re.findall(r'\b(202\d|203\d)\b', title_a.lower()))
        years_b = set(re.findall(r'\b(202\d|203\d)\b', title_b.lower()))
        if (years_a or years_b) and years_a != years_b:
            return False

        quarters_a = set(re.findall(r'\bq[1-4]\b', title_a.lower()))
        quarters_b = set(re.findall(r'\bq[1-4]\b', title_b.lower()))
        if quarters_a and quarters_b and quarters_a != quarters_b:
            return False

    # Проверка несовпадающих типов выборов (выборы в Сенат, Палату, промежуточные и т.д. — разные события)
    election_types = {'midterm', 'midterms', 'senate', 'house', 'governor', 'presidential', 'president', 'mayor'}
    type_a = {w for w in re.findall(r'\b\w+\b', title_a.lower()) if w in election_types}
    type_b = {w for w in re.findall(r'\b\w+\b', title_b.lower()) if w in election_types}
    def norm_type(types):
        res = set()
        for t in types:
            if t == 'midterms':
                res.add('midterm')
            elif t in ('presidential', 'president'):
                res.add('president')
            else:
                res.add(t)
        return res
    norm_a = norm_type(type_a)
    norm_b = norm_type(type_b)
    if (norm_a and not norm_b) or (not norm_a and norm_b):
        pass  # не блокируем, overlap-проверка решит позже
    elif norm_a and norm_b and not (norm_a & norm_b):
        return False  # разные типы -> точно разные события

    # Проверка кандидатов: если один рынок о конкретном кандидате, а другой нет, или о другом — это разные события.
    candidates = {
        'trump', 'harris', 'biden', 'obama', 'kennedy', 'haley', 'desantis', 'pence', 'michelle',
        'putin', 'zelensky', 'netanyahu', 'xi', 'macron', 'scholz', 'starmer', 'sunak'
    }
    cand_a = {w for w in re.findall(r'\b\w+\b', title_a.lower()) if w in candidates}
    cand_b = {w for w in re.findall(r'\b\w+\b', title_b.lower()) if w in candidates}
    if cand_a != cand_b:
        return False

    stopwords = _COMMON_STOPWORDS | {
        'above', 'below', 'over', 'under', 'hit', 'hits', 'reach', 'exceed',
        'close', 'closes', 'closed', 'closing',
        'end', 'finish',
        'market', 'cap', 'price', 'value', 'valuation', 'worth', 'stock', 'stocks',
        'win', 'wins',
        'us', 'usa',
        'who', 'whom', 'whose', 'which', 'what', 'where', 'when', 'how', 'why',
    }
    time_markers = {
        'q1', 'q2', 'q3', 'q4', 'jan', 'feb', 'mar', 'apr',
        'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
        'january', 'february', 'march', 'april', 'june', 'july',
        'august', 'september', 'october', 'november', 'december',
        'quarter', 'year', 'month', 'week', 'day', 'daily', 'weekly',
        'monthly', 'quarterly', 'yearly'
    }
    # Нормализация: заменяем известные синонимы
    aliases = {
        'btc': 'bitcoin', 'eth': 'ethereum', 'fed': 'federal',
        'sp500': 'sp', 's&p': 'sp', 'spx': 'sp',
        'democrats': 'democrat', 'democratic': 'democrat',
        'republicans': 'republican', 'gop': 'republican',
        'midterms': 'midterm',
        'elections': 'election',
    }
    def normalize(title: str) -> set:
        # Предобработка: нормализуем тикеры ДО токенизации
        t = title.lower()
        t = re.sub(r's&p\s*500', 'sp', t)
        t = t.replace('s&p', 'sp')
        t = t.replace('sp500', 'sp')
        t = t.replace('spx', 'sp')
        
        words = set(re.findall(r'\b\w+\b', t)) - stopwords
        words = {aliases.get(w, w) for w in words}
        words = {w for w in words if not re.search(r'\d', w)}
        words -= time_markers
        return words

    a_words = normalize(title_a)
    b_words = normalize(title_b)
    if not a_words or not b_words:
        return False
    overlap = len(a_words & b_words) / min(len(a_words), len(b_words))
    return overlap >= 0.50  # минимум 50% совпадение ключевых слов

def math_pre_filter(market_a: Market, market_b: Market, min_spread_pct: float = 5.0, check_logical_implication: bool = False) -> MathFilterResult:
    # 1. Monotonicity
    t_a = _parse_threshold(market_a.title)
    t_b = _parse_threshold(market_b.title)
    
    # NEW: одинаковые пороги — монотонность неприменима
    if t_a and t_b and _same_unit(t_a[1], t_b[1]) and t_a[0] == t_b[0]:
        same_event = _check_same_event(market_a.title, market_b.title, market_a=market_a, market_b=market_b)
        if same_event and market_a.platform != market_b.platform:
            pass  # BTC $100K на Polymarket vs Kalshi — это price_divergence, не identical_threshold
        else:
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
                # Если это одно событие — математически гарантированный арбитраж,
                # LLM не нужен: P(X>high) > P(X>low) логически невозможно.
                same_event = (higher_market.platform == lower_market.platform) and _check_same_event(
                    higher_market.title, lower_market.title,
                    market_a=higher_market, market_b=lower_market
                )
                if same_event:
                    instruction = (
                        f"BUY YES на [{lower_market.title}]({lower_market.url}) "
                        f"({p_lower*100:.0f}¢). "
                        f"Обоснование: порог {min(t_a[0], t_b[0]):.0f} не может стоить дешевле "
                        f"порога {max(t_a[0], t_b[0]):.0f} при одном событии. "
                        f"Спред: {spread:.1f}%."
                    )
                    is_valid, reason = validate_trade_instruction(instruction)
                    return MathFilterResult(
                        decision=FilterDecision.CONFIRMED_ARBITRAGE,
                        arbitrage_type="monotonicity_violation",
                        spread_pct=spread,
                        reasoning=(
                            f"Нарушение монотонности подтверждено: рынки описывают одно событие "
                            f"(overlap ≥ 50%). P(>{max(t_a[0],t_b[0]):.0f})={p_higher:.2f} > "
                            f"P(>{min(t_a[0],t_b[0]):.0f})={p_lower:.2f}."
                        ),
                        trade_instruction=instruction if is_valid else f"⚠️ {reason}",
                        has_arbitrage=is_valid,
                    )
                # Разные события (разный оракул/дата) — оставляем AMBIGUOUS
                instruction = (
                    f"⚠️ Рынок [{lower_market.title}]({lower_market.url}) ({p_lower*100:.0f}¢) "
                    f"потенциально недооценён относительно [{higher_market.title}] ({p_higher*100:.0f}¢). "
                    f"Спред: {spread:.1f}%. Требует подтверждения LLM (разные оракулы/даты)."
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
        if not _check_same_event(market_a.title, market_b.title, market_a=market_a, market_b=market_b):
            return MathFilterResult(
                decision=FilterDecision.CONFIRMED_NO_ARBI,
                arbitrage_type="different_events",
                spread_pct=0.0,
                reasoning=(
                    "Рынки выглядят комплементарными по ключевым словам, "
                    "но описывают разные события (embedding cosine < 0.65 "
                    "или разные event_slug). Арбитраж невозможен."
                ),
                trade_instruction=""
            )
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
        if not _check_same_event(market_a.title, market_b.title, allow_different_dates=True, market_a=market_a, market_b=market_b):
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
