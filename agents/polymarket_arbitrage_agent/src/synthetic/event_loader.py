import json
import logging
import requests
from dataclasses import dataclass, field, replace

logger = logging.getLogger(f"NexusPolyBot.{__name__}")
from datetime import datetime, timezone
from typing import Optional

from agents.shared.utils.parsers import parse_numeric_level

@dataclass
class OutcomeMarket:
    """Один рынок-исход внутри события (один уровень оценки)."""
    market_id: str
    question: str           # оригинальный заголовок
    price_yes: float        # из outcomePrices[0]
    price_no: float         # из outcomePrices[1] или 1 - price_yes
    volume: float
    end_date: Optional[datetime]
    token_yes: Optional[str]    # clobTokenIds[0] — нужен для ордербука
    token_no: Optional[str]     # clobTokenIds[1]
    numeric_level: Optional[float] = None  # извлечённое число ($1.5T → 1.5)
    level_unit: str = ""    # "T", "B", "%", "points", "unknown"

@dataclass
class PolyEvent:
    """Событие с несколькими уровнями-рынками."""
    event_slug: str
    event_title: str
    event_url: str
    markets: list[OutcomeMarket] = field(default_factory=list)

    @property
    def sorted_markets(self) -> list[OutcomeMarket]:
        """Рынки отсортированные по numeric_level ASC."""
        return sorted(
            [m for m in self.markets if m.numeric_level is not None],
            key=lambda m: m.numeric_level
        )

def _parse_date(item: dict) -> Optional[datetime]:
    for field in ("endDate", "end_date_iso", "endDateIso", "end"):
        raw = item.get(field)
        if raw:
            try:
                return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
    return None


def load_events_with_levels_from_raw(
    raw_events: list[dict],
    min_markets: int = 2,
    min_volume_per_market: float = 5_000,
    min_cumulative_sum: float = 1.005,
) -> tuple[list[PolyEvent], dict]:
    """
    Парсит сырые события в PolyEvent с числовыми уровнями.
    Возвращает только события с >= 2 рынками с числовыми уровнями.
    """
    stats = {"total": 0, "few_markets": 0, "no_levels": 0, "mixed_units": 0, "low_sum": 0, "passed": 0}
    result: list[PolyEvent] = []
    
    now = datetime.now(timezone.utc)
    for event in raw_events:
        stats["total"] += 1
        raw_markets = event.get("markets", [])
        if len(raw_markets) < min_markets:
            stats["few_markets"] += 1
            continue
        
        outcome_markets: list[OutcomeMarket] = []
        
        for m in raw_markets:
            try:
                # Фильтруем закрытые или разрешенные рынки
                if m.get("closed") is True or m.get("closed") == "true" or m.get("resolved") is True:
                    continue

                end_date = _parse_date(m)
                if end_date and end_date <= now:
                    continue

                prices = json.loads(m.get("outcomePrices", "[]"))
                
                # volumeNum — это объем всего события, volume — объем конкретного рынка
                volume_raw = m.get("volume") or m.get("volumeNum") or 0
                volume = float(volume_raw) if volume_raw else 0.0
                
                if not prices or volume < min_volume_per_market:
                    continue
                
                price_yes = float(prices[0])
                price_no = float(prices[1]) if len(prices) > 1 else 1.0 - price_yes
                
                tokens = json.loads(m.get("clobTokenIds", "[]"))
                token_yes = tokens[0] if tokens else None
                token_no = tokens[1] if len(tokens) > 1 else None
                
                question = m.get("question", "")
                parsed = parse_numeric_level(question)
                numeric_level, unit = parsed if parsed else (None, "unknown")
                logger.debug(f"[PARSER] '{question[:60]}' -> ({numeric_level}, {unit})")
                
                outcome_markets.append(OutcomeMarket(
                    market_id=m["id"],
                    question=question,
                    price_yes=price_yes,
                    price_no=price_no,
                    volume=volume,
                    end_date=end_date,
                    token_yes=token_yes,
                    token_no=token_no,
                    numeric_level=numeric_level,
                    level_unit=unit,
                ))
            except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
                logger.debug(f"[PARSER] Пропущен рынок {m.get('id','?')}: {e}")
                continue
        
        # Только события где >= 2 рынков с распознанными числовыми уровнями
        leveled = [m for m in outcome_markets if m.numeric_level is not None]
        if len(leveled) < min_markets:
            stats["no_levels"] += 1
            continue
        
        # Нормализуем денежные единицы (K, M, B, T), если они смешаны
        UNIT_MULTIPLIERS = {'T': 1e12, 'B': 1e9, 'M': 1e6, 'K': 1e3}
        event_units = {m.level_unit for m in leveled}
        financial_units = event_units.intersection(UNIT_MULTIPLIERS.keys())
        
        if len(financial_units) > 1:
            # Приводим к максимальной единице из присутствующих
            target_unit = max(financial_units, key=UNIT_MULTIPLIERS.get)
            normalized = []
            for m in leveled:
                if m.level_unit in UNIT_MULTIPLIERS and m.level_unit != target_unit:
                    scale = UNIT_MULTIPLIERS[m.level_unit] / UNIT_MULTIPLIERS[target_unit]
                    normalized.append(replace(m, numeric_level=m.numeric_level * scale, level_unit=target_unit))
                else:
                    normalized.append(m)
            leveled = normalized
            # Обновляем набор единиц после нормализации
            event_units = {m.level_unit for m in leveled}

        # Проверяем что все уровни имеют одну единицу — иначе сравнение некорректно
        if len(event_units) > 1:
            stats["mixed_units"] += 1
            continue  # смешанные единицы — пропускаем без LLM

        total_yes_prob = sum(m.price_yes for m in leveled)
        logger.debug(f"[SCA] '{event.get('title','')[:50]}': leveled={len(leveled)}, sum_yes={total_yes_prob:.2f}, units={event_units}")

        # Проверяем наличие нарушения монотонности (потенциального арбитража) — O(n)
        sorted_leveled = sorted(leveled, key=lambda m: m.numeric_level)
        
        has_violation = any(
            sorted_leveled[i].price_yes < sorted_leveled[i + 1].price_yes
            for i in range(len(sorted_leveled) - 1)
        )

        # Фильтр: отсекаем взаимоисключающие рынки (mutually exclusive)
        # Если есть явное нарушение монотонности (has_violation), пропускаем этот фильтр (например, свежий рынок с неполными котировками)
        if not has_violation and total_yes_prob < min_cumulative_sum:
            logger.info(
                f"[SCA] Пропущено '{event.get('title', '')[:40]}': "
                f"sum(price_yes)={total_yes_prob:.2f} < {min_cumulative_sum} (нет нарушения и малая сумма)"
            )
            stats["low_sum"] += 1
            continue
        
        # ЛОГИРОВАНИЕ УРОВНЕЙ И ЦЕН (ДЛЯ ДИАГНОСТИКИ)
        levels_log = " | ".join([f"{m.numeric_level}{m.level_unit}: {m.price_yes:.2f}" for m in sorted_leveled])
        logger.debug(f"[SCA] Анализ уровней '{event.get('title', '')[:40]}': {levels_log}")

        stats["passed"] += 1
        slug = event.get("slug", "")
        result.append(PolyEvent(
            event_slug=slug,
            event_title=event.get("title", ""),
            event_url=f"https://polymarket.com/event/{slug}",
            markets=leveled,
        ))
    
    logger.info(f"[SCA] Статистика парсинга: {stats}")
    return result, stats
