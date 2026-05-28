import json
import requests
from dataclasses import dataclass, field
from datetime import datetime
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


def load_events_with_levels(
    limit: int = 50,
    min_markets_per_event: int = 2,
    min_volume_per_market: float = 5_000,
) -> list[PolyEvent]:
    """
    Загружает события через /events API.
    Возвращает только события с >= 2 рынками с числовыми уровнями.
    """
    url = "https://gamma-api.polymarket.com/events"
    params = {
        "active": "true",
        "closed": "false",
        "limit": limit,
        "order": "volume",
        "ascending": "false",
    }
    
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    raw_events = resp.json()
    
    result: list[PolyEvent] = []
    
    for event in raw_events:
        raw_markets = event.get("markets", [])
        if len(raw_markets) < min_markets_per_event:
            continue
        
        outcome_markets: list[OutcomeMarket] = []
        
        for m in raw_markets:
            try:
                prices = json.loads(m.get("outcomePrices", "[]"))
                volume = float(m.get("volumeNum", 0) or m.get("volume", 0) or 0)
                
                if not prices or volume < min_volume_per_market:
                    continue
                
                price_yes = float(prices[0])
                price_no = float(prices[1]) if len(prices) > 1 else 1.0 - price_yes
                
                tokens = json.loads(m.get("clobTokenIds", "[]"))
                token_yes = tokens[0] if tokens else None
                token_no = tokens[1] if len(tokens) > 1 else None
                
                question = m.get("question", "")
                numeric_level, unit = parse_numeric_level(question)
                
                outcome_markets.append(OutcomeMarket(
                    market_id=m["id"],
                    question=question,
                    price_yes=price_yes,
                    price_no=price_no,
                    volume=volume,
                    end_date=_parse_date(m),
                    token_yes=token_yes,
                    token_no=token_no,
                    numeric_level=numeric_level,
                    level_unit=unit,
                ))
            except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                continue
        
        # Только события где >= 2 рынков с распознанными числовыми уровнями
        leveled = [m for m in outcome_markets if m.numeric_level is not None]
        if len(leveled) < min_markets_per_event:
            continue
        
        # Проверяем что все уровни имеют одну единицу — иначе сравнение некорректно
        units = set(m.level_unit for m in leveled)
        if len(units) > 1:
            continue  # смешанные единицы — пропускаем без LLM
        
        slug = event.get("slug", "")
        result.append(PolyEvent(
            event_slug=slug,
            event_title=event.get("title", ""),
            event_url=f"https://polymarket.com/event/{slug}",
            markets=leveled,
        ))
    
    return result
