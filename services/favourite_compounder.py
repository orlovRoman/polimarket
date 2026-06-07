# services/favourite_compounder.py
"""
Favourite Compounding — детерминированный поиск рынков с ≥95¢,
где резолюция практически гарантирована.
БЕЗ LLM. Google Grounding только для валидации «очевидности».
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("NexusPolyBot.FavouriteCompounder")

from agents.shared.python.db import get_compound_settings

# ════════════════════════════════════════════════════
# 1. DATA MODEL
# ════════════════════════════════════════════════════

@dataclass
class FavouriteOpportunity:
    market_id:   str
    title:       str
    url:         str
    price:       float        # текущая цена фаворита (YES или NO)
    volume_usd:  float
    close_time:  datetime
    hours_left:  float
    spread_pct:  Optional[float]
    roi_net_pct: float        # чистый ROI с учётом spread
    confidence:  float        # 0.0–1.0
    obviousness_reason: str
    outcome:     str = "YES"  # целевой исход ("YES" или "NO")

    @property
    def opp_id(self) -> str:
        date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"{self.market_id}_{date_tag}"


# ════════════════════════════════════════════════════
# 2. FILTER — детерминированный, без LLM
# ════════════════════════════════════════════════════

class FavouriteFilter:
    def __init__(
        self,
        min_price: float = 0.95,
        min_volume_usd: float = 1000.0,
        max_hours: float = 48.0,
    ):
        self.min_price = min_price
        self.min_volume_usd = min_volume_usd
        self.max_hours = max_hours

    def scan(self, markets: list) -> list:
        """
        markets — список объектов Market из adaptera.
        Возвращает список кортежей (m, hours_left, fav_outcome, fav_price).
        """
        now = datetime.now(timezone.utc)
        result = []
        for m in markets:
            try:
                if m.price is None:
                    continue
                price_yes = float(m.price)
                if price_yes >= 0.5:
                    fav_price = price_yes
                    fav_outcome = "YES"
                else:
                    fav_price = 1.0 - price_yes
                    fav_outcome = "NO"

                volume_raw = getattr(m, "volume", 0) or 0
                if isinstance(volume_raw, (int, float)):
                    volume = float(volume_raw)
                else:
                    try:
                        volume = float(volume_raw)
                    except Exception:
                        volume = 0.0
                close_time = m.close_time
                if not close_time:
                    continue
                # Нормализуем timezone
                if close_time.tzinfo is None:
                    close_time = close_time.replace(tzinfo=timezone.utc)
                hours_left = (close_time - now).total_seconds() / 3600

                if (
                    fav_price >= self.min_price
                    and volume >= self.min_volume_usd
                    and 0 < hours_left <= self.max_hours
                ):
                    result.append((m, hours_left, fav_outcome, fav_price))
            except Exception as exc:
                logger.debug(f"[Filter] Пропуск {getattr(m,'id','?')}: {exc}")
        logger.info(f"[FavouriteFilter] {len(result)}/{len(markets)} прошли фильтр")
        return result


# ════════════════════════════════════════════════════
# 3. OBVIOUSNESS VALIDATOR — Google Grounding, без LLM
# ════════════════════════════════════════════════════

class ObviousnessValidator:
    """
    Проверяет что исход «очевиден»:
    - событие уже произошло (подтверждено Google)
    - ИЛИ рынок торгуется ≥0.97 (рынок сам подтверждает)
    """

    # Ключевые слова в названии рынка, указывающие на произошедшее событие
    _PAST_TENSE_SIGNALS = [
        "has ", "have ", "did ", "won ", "lost ", "signed ", "passed ",
        "elected ", "named ", "confirmed ", "approved ", "launched ",
        "completed ", "ended ", "finished ", "announced ",
    ]

    def validate(self, market, price: float) -> tuple[float, str]:
        """
        Возвращает (confidence: float, reason: str).
        confidence=1.0 — максимальная уверенность.
        """
        title = (market.title or "").lower()
        confidence = 0.0
        reasons = []

        # Сигнал 1: прошедшее время в названии (событие уже произошло)
        past_hits = [kw for kw in self._PAST_TENSE_SIGNALS if kw in title]
        if past_hits:
            confidence += 0.4
            reasons.append(f"прошедшее время: {past_hits[0].strip()!r}")

        # Сигнал 2: цена очень высокая (рынок сам голосует ≥97¢)
        if price >= 0.97:
            confidence += 0.35
            reasons.append(f"цена {price:.3f} ≥ 0.97")
        elif price >= 0.95:
            confidence += 0.2
            reasons.append(f"цена {price:.3f} ≥ 0.95")

        # Сигнал 3: Google Grounding (лениво, только если нужна доп. проверка)
        if confidence < 0.6:
            grounding_conf, grounding_reason = self._check_google(market.title)
            confidence += grounding_conf
            if grounding_reason:
                reasons.append(grounding_reason)

        confidence = min(confidence, 1.0)
        reason = "; ".join(reasons) if reasons else "нет явных сигналов"
        return round(confidence, 2), reason

    def _check_google(self, title: str) -> tuple[float, str]:
        """Google Grounding через существующий механизм в проекте."""
        try:
            from agents.shared.utils.gemini_client import (
                generate_content_with_fallback, extract_response_text
            )
            from agents.shared.python.db import get_memory
        except ImportError:
            logger.warning("[Validator] gemini_client недоступен, Google Grounding отключён")
            return 0.0, ""

        try:
            # Получаем API-ключ и модель из конфига агентов
            grounding_config = get_memory("agent_config_GROUNDING") or {}
            nexus_config = get_memory("agent_config_NEXUS") or {}
            api_key = grounding_config.get("api_key") or nexus_config.get("api_key")
            model = grounding_config.get("model") or "gemini-2.5-flash"

            if not api_key:
                return 0.0, ""

            payload = {
                "contents": [{"role": "user", "parts": [{"text": (
                    f"Has this prediction market event already happened? "
                    f"Answer yes or no with evidence: '{title}'"
                )}]}],
                "tools": [{"google_search": {}}]
            }
            result, _ = generate_content_with_fallback(
                api_key=api_key, payload=payload,
                default_model=model, agent_name="GROUNDING_FAV",
                market_id=title[:40]
            )
            snippet = extract_response_text(result) if result else ""
            if not snippet:
                return 0.0, ""

            snippet_lower = snippet.lower()
            confirm = ["yes", "confirmed", "happened", "occurred", "won", "passed",
                       "approved", "signed", "elected", "announced"]
            hits = [w for w in confirm if w in snippet_lower]
            if len(hits) >= 2:
                return 0.3, f"Google: {hits[0]}, {hits[1]}"
            elif len(hits) == 1:
                return 0.15, f"Google: {hits[0]}"
        except Exception as exc:
            logger.debug(f"[Validator] Google Grounding недоступен: {exc}")
        return 0.0, ""


# ════════════════════════════════════════════════════
# 4. ROI CALCULATOR
# ════════════════════════════════════════════════════

class ROICalculator:
    """
    Рассчитывает чистый ROI с учётом:
    - spread (bid/ask)
    - временно́го decay (чем меньше времени — тем выше ROI/риск)
    - комиссии Polymarket (2% от winnings)
    """
    POLY_FEE_PCT = 0.02  # 2% fee on profit

    def compute(
        self,
        price: float,
        hours_left: float,
        spread_pct: Optional[float] = None,
    ) -> dict:
        # Gross ROI: (1 - price) / price
        gross_roi = (1.0 - price) / price

        spread_val = spread_pct if spread_pct is not None else 0.005
        spread_cost = price * spread_val / 2  # half-spread
        net_price = price + spread_cost
        net_price = min(net_price, 0.999)

        # Gross net ROI
        net_roi = (1.0 - net_price) / net_price

        # Вычитаем комиссию Polymarket
        net_roi_after_fee = net_roi * (1.0 - self.POLY_FEE_PCT)

        # Annualized ROI (для сравнения стратегий)
        hours_safe = max(hours_left, 0.5)
        annualized = net_roi_after_fee * (8760 / hours_safe)

        return {
            "roi_gross_pct": round(gross_roi * 100, 2),
            "roi_net_pct":   round(net_roi_after_fee * 100, 2),
            "roi_annual_pct": round(annualized * 100, 1),
            "spread_cost_pct": round(spread_cost * 100, 3),
            "net_price":      round(net_price, 4),
        }


# ════════════════════════════════════════════════════
# 5. СУПЕР-КАЛИБРОВКА (Self-calibrating threshold)
# ════════════════════════════════════════════════════

def calibrate_confidence_threshold() -> float:
    """Динамически калибрует порог уверенности на основе Rolling 30d Win Rate."""
    from agents.shared.python.db import get_connection, save_compound_setting
    cfg = get_compound_settings()
    current_threshold = cfg.get("min_confidence", 0.5)
    
    # Получаем win_rate за 30 дней из strategy_metrics
    try:
        with get_connection() as conn:
            row = conn.execute("""
                SELECT win_rate, total_signals
                FROM strategy_metrics
                WHERE strategy_type = 'FAVOURITE_COMPOUND'
                ORDER BY created_at DESC LIMIT 1
            """).fetchone()
    except Exception as exc:
        logger.error(f"[Compounder] Ошибка при чтении strategy_metrics для калибровки: {exc}")
        return current_threshold
        
    if not row or (row["total_signals"] or 0) < 5:
        # Недостаточно данных для калибровки
        return current_threshold
        
    win_rate = row["win_rate"]
    if win_rate is None:
        return current_threshold
        
    new_threshold = current_threshold
    if win_rate > 0.85:
        new_threshold = max(0.4, current_threshold - 0.05)
    elif win_rate < 0.70:
        new_threshold = min(0.8, current_threshold + 0.05)
        
    new_threshold = round(new_threshold, 2)
    if new_threshold != current_threshold:
        save_compound_setting("min_confidence", str(new_threshold))
        logger.info(f"[Compounder] Автокалибровка порога уверенности: {current_threshold} -> {new_threshold} (win_rate={win_rate:.1%})")
        
    return new_threshold


# ════════════════════════════════════════════════════
# 6. ПУБЛИЧНЫЙ API
# ════════════════════════════════════════════════════

def run_favourite_scan(
    markets: list,
    min_confidence: Optional[float] = None,
) -> list[FavouriteOpportunity]:
    """
    Главная точка входа.
    Принимает список Market, возвращает список FavouriteOpportunity.
    """
    cfg = get_compound_settings()

    # Запускаем авто-калибровку
    calibrated_conf = calibrate_confidence_threshold()
    min_conf = min_confidence if min_confidence is not None else calibrated_conf

    filt = FavouriteFilter(
        min_price=cfg["min_price"],
        min_volume_usd=cfg["min_volume"],
        max_hours=cfg["max_hours"],
    )
    validator = ObviousnessValidator()
    calc = ROICalculator()

    candidates = filt.scan(markets)
    opportunities: list[FavouriteOpportunity] = []

    for market, hours_left, fav_outcome, fav_price in candidates:
        confidence, reason = validator.validate(market, fav_price)

        if confidence < min_conf:
            logger.debug(f"[Compounder] {market.id}: confidence={confidence:.2f} < {min_conf} — пропуск")
            continue

        # Spread из orderbook (если доступен)
        spread_pct = _get_spread(market)
        roi_data = calc.compute(fav_price, hours_left, spread_pct)

        # Минимальный net ROI — должен быть > 0.3% чтобы покрыть gas/slippage
        if roi_data["roi_net_pct"] < 0.3:
            continue

        volume_raw = getattr(market, "volume", 0) or 0
        try:
            volume_usd = float(volume_raw)
        except Exception:
            volume_usd = 0.0

        opp = FavouriteOpportunity(
            market_id=market.id,
            title=market.title,
            url=market.url,
            price=fav_price,
            volume_usd=volume_usd,
            close_time=market.close_time,
            hours_left=round(hours_left, 1),
            spread_pct=spread_pct,
            roi_net_pct=roi_data["roi_net_pct"],
            confidence=confidence,
            obviousness_reason=reason,
            outcome=fav_outcome,
        )
        opportunities.append(opp)
        logger.info(
            f"[Compounder] ✅ {market.title[:50]} | "
            f"outcome={fav_outcome} | price={fav_price:.3f} | ROI={roi_data['roi_net_pct']:.2f}% | "
            f"conf={confidence:.2f} | {hours_left:.1f}h left"
        )

    # Сортируем по ROI desc
    opportunities.sort(key=lambda o: o.roi_net_pct, reverse=True)
    return opportunities


def _get_spread(market) -> Optional[float]:
    """Пытается получить spread из orderbook. Возвращает None при ошибке."""
    try:
        orderbook = getattr(market, "_orderbook", None)
        if orderbook and orderbook.get("bids") and orderbook.get("asks"):
            best_bid = float(orderbook["bids"][0]["price"])
            best_ask = float(orderbook["asks"][0]["price"])
            mid = (best_bid + best_ask) / 2
            return round((best_ask - best_bid) / mid, 4) if mid > 0 else None
    except Exception:
        pass
    return None
