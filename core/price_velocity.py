from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

@dataclass
class VelocitySignal:
    has_anomaly: bool
    direction: str          # "UP" | "DOWN" | "FLAT"
    magnitude: float        # % изменение за период
    period_hours: int
    suspicion: str          # "ORGANIC" | "PUMP" | "DUMP" | "NOISE"
    annotation: str         # одна строка для промпта

def detect_velocity_anomaly(
    price_history: list[dict],
    threshold_2h: float = 0.35,   # +35% за 2ч = аномалия
    threshold_6h: float = 0.25,   # +25% за 6ч = аномалия
) -> VelocitySignal:
    """
    Ищет аномальные движения цены.
    price_history: [{price: float, timestamp: datetime | str}, ...]
    """
    if len(price_history) < 3:
        return VelocitySignal(False, "FLAT", 0.0, 0, "NOISE", "")
    
    # Конвертируем timestamps в datetime объекты и цены во float
    parsed_hist = []
    for p in price_history:
        ts = p.get("timestamp") or p.get("recorded_at")
        if not ts:
            continue
        if isinstance(ts, str):
            try:
                # Убираем суффикс Z или знак временной зоны, если он есть, для совместимости
                clean_ts = ts.replace("Z", "+00:00")
                dt = datetime.fromisoformat(clean_ts)
            except ValueError:
                continue
        else:
            dt = ts
        
        parsed_hist.append({
            "price": float(p["price"]),
            "timestamp": dt
        })
        
    if len(parsed_hist) < 2:
        return VelocitySignal(False, "FLAT", 0.0, 0, "NOISE", "")
        
    sorted_hist = sorted(parsed_hist, key=lambda x: x["timestamp"])
    latest = sorted_hist[-1]["price"]
    now = sorted_hist[-1]["timestamp"]
    
    # Проверяем интервалы 2 часа и 6 часов
    for threshold, hours in [(threshold_2h, 2), (threshold_6h, 6)]:
        cutoff = now - timedelta(hours=hours)
        past = [p for p in sorted_hist if p["timestamp"] >= cutoff]
        if len(past) < 2:
            continue
        base_price = past[0]["price"]
        if base_price < 0.01:
            continue
        change = (latest - base_price) / base_price
        if abs(change) >= threshold:
            direction = "UP" if change > 0 else "DOWN"
            suspicion = "PUMP" if direction == "UP" else "DUMP"
            annotation = (
                f"⚡ Velocity: {change*100:+.0f}% за {hours}ч "
                f"({base_price:.2f}→{latest:.2f})"
            )
            return VelocitySignal(True, direction, abs(change), hours, suspicion, annotation)
    
    return VelocitySignal(False, "FLAT", 0.0, 0, "ORGANIC", "")
