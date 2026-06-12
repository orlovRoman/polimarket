import json
import logging
import requests
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(f"NexusPolyBot.{__name__}")

@dataclass
class EventMarket:
    """Один market внутри события."""
    market_id: str
    question: str
    price_yes: float
    price_no: float
    volume: float
    close_time: datetime
    token_yes: Optional[str]
    token_no: Optional[str]

@dataclass
class PolyEvent:
    event_slug: str
    event_title: str
    markets: list[EventMarket] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://polymarket.com/event/{self.event_slug}"

    @property
    def date_sorted(self) -> list[EventMarket]:
        return sorted(self.markets, key=lambda m: m.close_time)


def _parse_dt(item: dict) -> datetime:
    for field_name in ("endDate", "end_date_iso", "endDateIso", "end"):
        raw = item.get(field_name)
        if raw:
            try:
                return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
    return datetime(2099, 12, 31, tzinfo=timezone.utc)


def load_events_from_raw(
    raw_events: list[dict],
    min_markets: int = 2,
    min_volume: float = 3_000,
) -> list[PolyEvent]:
    """
    Парсит сырые события с несколькими рынками.
    Возвращает только события с >= min_markets ликвидных рынков.
    """
    stats = {"total": 0, "few_markets": 0, "low_volume": 0, "few_dates": 0, "passed": 0}
    result: list[PolyEvent] = []

    for event in raw_events:
        stats["total"] += 1
        raw_markets = event.get("markets", [])
        if len(raw_markets) < min_markets:
            stats["few_markets"] += 1
            continue

        markets: list[EventMarket] = []
        for m in raw_markets:
            try:
                prices = json.loads(m.get("outcomePrices", "[]"))
                if not prices:
                    continue

                volume = float(
                    m.get("volumeNum", 0) or m.get("volume", 0) or 0
                )
                if volume < min_volume:
                    logger.debug(f"[TC-PARSER] Пропущен рынок {m.get('id','?')}: volume={volume} < {min_volume}")
                    continue

                price_yes = float(prices[0])
                price_no = float(prices[1]) if len(prices) > 1 else 1.0 - price_yes

                tokens = json.loads(m.get("clobTokenIds", "[]"))

                markets.append(EventMarket(
                    market_id=m["id"],
                    question=m.get("question", ""),
                    price_yes=price_yes,
                    price_no=price_no,
                    volume=volume,
                    close_time=_parse_dt(m),
                    token_yes=tokens[0] if tokens else None,
                    token_no=tokens[1] if len(tokens) > 1 else None,
                ))
            except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
                logger.debug(f"[TC-PARSER] Ошибка парсинга рынка {m.get('id','?')}: {e}")
                continue

        if len(markets) < min_markets:
            stats["low_volume"] += 1
            continue

        # Для временного коридора нужно >= 2 рынков С РАЗНЫМИ датами
        dates = {m.close_time.date() for m in markets}
        if len(dates) < min_markets:
            stats["few_dates"] += 1
            continue

        stats["passed"] += 1
        result.append(PolyEvent(
            event_slug=event.get("slug", ""),
            event_title=event.get("title", ""),
            markets=markets,
        ))

    logger.info(f"[TC] Статистика парсинга: {stats}")
    return result
