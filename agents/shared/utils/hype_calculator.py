# agents/shared/utils/hype_calculator.py
"""
Вычисляет числовую оценку хайп-потенциала на основе сигналов.
LLM получает готовое число + декомпозицию — не выдумывает его самостоятельно.
"""
import math
from dataclasses import dataclass
from typing import Optional

_LOG_REDDIT_MAX = math.log10(5000)


@dataclass
class HypeMetrics:
    trends_score: int           # 0–100 от Google Trends
    trends_delta: int           # изменение за 24ч (может быть отрицательным)
    reddit_top_score: int       # лучший пост на Reddit (upvotes)
    recent_news_count: int      # новостей за последние 6ч
    price_delta_6h: float       # изменение цены за 6ч (доли единицы, напр. +0.05)
    hours_to_close: float       # часов до закрытия рынка


def calculate_hype_potential(m: HypeMetrics) -> tuple[float, str]:
    """
    Возвращает (hype_score: float 0.0–1.0, breakdown: str).
    breakdown — объяснение для LLM: откуда взялась каждая составляющая.
    """
    # Веса компонентов
    W_TRENDS    = 0.25
    W_REDDIT    = 0.20
    W_NEWS      = 0.25
    W_PRICE     = 0.20
    W_TIMING    = 0.10

    # Нормализация trends (0–100 → 0.0–1.0) с бонусом за рост
    trends_norm = min(m.trends_score / 100, 1.0)
    trends_delta_bonus = min(max(m.trends_delta / 50, -0.3), 0.3)
    trends_component = max(0, min(trends_norm + trends_delta_bonus, 1.0))

    # Reddit (логарифмическая шкала: 0→0, 100→0.3, 1000→0.6, 5000→1.0)
    reddit_norm = min(math.log10(max(m.reddit_top_score, 1)) / _LOG_REDDIT_MAX, 1.0)

    # Новости за 6ч (0→0, 1→0.4, 3→0.7, 5+→1.0)
    news_norm = min(m.recent_news_count / 5, 1.0)

    # Движение цены (|delta| > 0.10 = сильный сигнал)
    price_norm = min(abs(m.price_delta_6h) / 0.15, 1.0)

    # Тайминг (оптимум 12–48ч до закрытия)
    if 12 <= m.hours_to_close <= 48:
        timing_norm = 1.0
    elif m.hours_to_close < 6:
        timing_norm = 0.1  # слишком мало времени
    elif m.hours_to_close > 168:
        timing_norm = 0.3  # слишком далеко
    else:
        timing_norm = 0.6

    score = (
        W_TRENDS * trends_component +
        W_REDDIT * reddit_norm +
        W_NEWS   * news_norm +
        W_PRICE  * price_norm +
        W_TIMING * timing_norm
    )
    score = round(min(max(score, 0.0), 1.0), 3)

    breakdown = (
        f"hype_potential={score:.3f} (рассчитано Python, не LLM)\n"
        f"  Trends:  {trends_component:.2f} × {W_TRENDS} "
        f"(score={m.trends_score}, Δ={m.trends_delta:+d})\n"
        f"  Reddit:  {reddit_norm:.2f} × {W_REDDIT} "
        f"(top_score={m.reddit_top_score})\n"
        f"  Новости: {news_norm:.2f} × {W_NEWS} "
        f"(за 6ч: {m.recent_news_count} шт.)\n"
        f"  Цена:    {price_norm:.2f} × {W_PRICE} "
        f"(Δ6ч={m.price_delta_6h:+.3f})\n"
        f"  Тайминг: {timing_norm:.2f} × {W_TIMING} "
        f"({m.hours_to_close:.0f}ч до закрытия)\n"
        f"ЗАДАЧА: Используй hype_potential={score:.3f} как базовое значение. "
        f"Можешь скорректировать ±0.10 с объяснением в reasoning.\n"
    )
    return score, breakdown


def format_hype_scorecard(m: HypeMetrics, score: float) -> str:
    """Компактная таблица для промпта. LLM не пересказывает — только ссылается."""
    def s(val, thr): return "✅" if val >= thr else "❌"
    lines = [
        f"=== HYPE SCORECARD: {score:.2f}/1.0 ===",
        f"{s(m.trends_score, 20)} Trends:       {m.trends_score}/100  (вход ≥20)",
        f"{s(m.reddit_top_score, 50)} Reddit top:   {m.reddit_top_score} upv (вход ≥50)",
        f"{s(m.recent_news_count, 1)} Новости 6ч:  {m.recent_news_count} шт. (вход ≥1)",
        f"{s(abs(m.price_delta_6h), 0.02)} Движение цены:{m.price_delta_6h:+.4f} (вход ≥0.02)",
        f"{s(m.hours_to_close, 24)} До закрытия:  {m.hours_to_close:.0f}ч (вход ≥24ч)",
    ]
    return "\n".join(lines)

