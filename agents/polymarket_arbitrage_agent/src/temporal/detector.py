from dataclasses import dataclass
from datetime import datetime, timezone
from .loader import PolyEvent, EventMarket

@dataclass
class CorridorCandidate:
    """
    Пара (early, late) из одного события.
    Конструкция: NO(early) + YES(late)
    """
    event: PolyEvent
    early: EventMarket
    late: EventMarket
    date_gap_days: int

    # Implied probabilities — из цен, без LLM
    p_early: float           # = price_yes_early
    p_late: float            # = price_yes_late
    p_in_corridor: float     # = p_late - p_early
    p_before: float          # = p_early
    p_never: float           # = 1 - p_late

    # Теоретические цены конструкции
    no_cost: float           # = 1 - p_early
    yes_cost: float          # = p_late
    theoretical_cost: float  # = no_cost + yes_cost
    theoretical_spread_pct: float  # = (1 - theoretical_cost) * 100


def find_candidates(
    events: list[PolyEvent],
    min_date_gap_days: int = 14,
    min_theoretical_spread_pct: float = 1.0,
    min_volume: float = 5_000,
) -> list[CorridorCandidate]:
    """
    Ищет все пары (early, late) внутри каждого события
    где temporal spread > min_theoretical_spread_pct.
    Никакого LLM — только математика цен.
    """
    candidates: list[CorridorCandidate] = []
    now = datetime.now(timezone.utc)

    for event in events:
        levels = event.date_sorted  # по возрастанию close_time

        for i in range(len(levels)):
            for j in range(i + 1, len(levels)):
                early = levels[i]
                late = levels[j]

                # Фильтр: ранняя дата ещё не истекла
                if early.close_time <= now:
                    continue
                    
                # Отсеиваем fallback-даты 2099 года, если у рынка не было даты завершения
                if early.close_time.year >= 2090 or late.close_time.year >= 2090:
                    continue

                # Фильтр: ликвидность обеих ног
                if early.volume < min_volume or late.volume < min_volume:
                    continue

                gap = (late.close_time - early.close_time).days
                if gap < min_date_gap_days:
                    continue

                p_early = early.price_yes
                p_late = late.price_yes

                no_cost = 1.0 - p_early    # цена покупки NO(early)
                yes_cost = p_late           # цена покупки YES(late)

                theoretical_cost = no_cost + yes_cost
                spread_pct = (1.0 - theoretical_cost) * 100

                if spread_pct < min_theoretical_spread_pct:
                    continue

                candidates.append(CorridorCandidate(
                    event=event,
                    early=early,
                    late=late,
                    date_gap_days=gap,
                    p_early=round(p_early, 4),
                    p_late=round(p_late, 4),
                    p_in_corridor=round(p_late - p_early, 4),
                    p_before=round(p_early, 4),
                    p_never=round(1.0 - p_late, 4),
                    no_cost=round(no_cost, 4),
                    yes_cost=round(yes_cost, 4),
                    theoretical_cost=round(theoretical_cost, 6),
                    theoretical_spread_pct=round(spread_pct, 3),
                ))

    # Лучший спред первым
    return sorted(candidates, key=lambda c: c.theoretical_spread_pct, reverse=True)


def compute_quality_score(
    real_spread_pct: float,
    date_gap_days: int,
    executable_contracts: float,
    p_in_corridor: float,
    min_executable: float = 10.0,
) -> float:
    """Оценка качества сигнала от 0 до 1 — без LLM."""
    # Спред: 5%+ = отлично, 1% = минимум
    spread_score = min(real_spread_pct / 5.0, 1.0)

    # Разнос дат: 30-90 дней оптимально
    if 30 <= date_gap_days <= 90:
        gap_score = 1.0
    elif date_gap_days < 30:
        gap_score = date_gap_days / 30.0
    else:
        gap_score = max(0.3, 1.0 - (date_gap_days - 90) / 180.0)

    # Ликвидность
    liquidity_score = min(executable_contracts / min_executable, 1.0)

    # Вероятность коридора
    # Нормировка на 0.3 (если вероятность коридора 30%+, считаем это отличным шансом для двойной выплаты и даем макс. балл)
    corridor_score = min(max(p_in_corridor / 0.3, 0.0), 1.0)

    score = (
        0.35 * spread_score +
        0.25 * gap_score +
        0.25 * liquidity_score +
        0.15 * corridor_score
    )

    return round(score, 3)
