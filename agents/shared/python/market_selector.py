"""
MarketSelector — умный отбор рынков для анализа.
Комбинирует несколько стратегий и ранжирует по scoring-функции.
Заменяет прямой вызов adapter.list_markets() в run_team.py.
"""
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from config import (
    MARKET_COOLDOWN_HOURS, MARKET_OFFSET_MAX,
    PRICE_RANGE_MIN, PRICE_RANGE_MAX, SCAN_CATEGORIES
)
from agents.shared.python.db import (
    get_memory, save_memory, get_markets_on_cooldown
)
from agents.shared.python.models import Market


class MarketSelector:
    """
    Умный отбор рынков для анализа.
    Комбинирует несколько стратегий, фильтрует по close_time и cooldown,
    ранжирует по scoring-функции.
    """

    def __init__(self, adapter):
        self.adapter = adapter

    def select(self, total_limit: int = 10, category: str = None) -> List[Market]:
        """
        Возвращает total_limit рынков, собранных по разным стратегиям.
        
        При ручном скане с category — все рынки из этой категории.
        При автоскане (category=None) — микс из разных стратегий с ротацией.
        """
        if category:
            # Ручной скан: одна категория
            raw = self._fetch_category(category, total_limit * 2)
        else:
            # Автоскан: микс стратегий
            raw = self._fetch_mixed(total_limit * 3)

        # Фильтрация
        filtered = self._filter(raw)

        # Scoring + дедупликация
        seen_ids = set()
        scored = []
        for m in filtered:
            if m.id not in seen_ids:
                seen_ids.add(m.id)
                scored.append((self._score_market(m), m))

        # Сортируем по убыванию скора
        scored.sort(key=lambda x: x[0], reverse=True)
        
        return [m for _, m in scored[:total_limit]]

    def _fetch_mixed(self, fetch_limit: int) -> List[Market]:
        """Собирает рынки из нескольких стратегий."""
        per_strategy = max(fetch_limit // 4, 5)
        all_markets = []

        # Стратегия 1: Mid-volume с offset (ротация)
        try:
            offset = get_memory("market_scan_offset") or 0
            mid = self.adapter.list_markets_paged(
                limit=per_strategy, offset=offset, order="volume"
            )
            all_markets.extend(mid)
            # Инкрементируем offset для следующего скана
            next_offset = (offset + per_strategy) % MARKET_OFFSET_MAX
            save_memory("market_scan_offset", next_offset, category='cache')
        except Exception as e:
            print(f"[MarketSelector] Ошибка стратегии mid_volume: {e}")

        # Стратегия 2: Закрывающиеся скоро (волатильность)
        try:
            ending = self.adapter.list_markets_ending_soon(limit=per_strategy)
            all_markets.extend(ending)
        except Exception as e:
            print(f"[MarketSelector] Ошибка стратегии ending_soon: {e}")

        # Стратегия 3: Категория из ротации
        try:
            cat_idx = get_memory("category_rotation_idx") or 0
            cat = SCAN_CATEGORIES[cat_idx % len(SCAN_CATEGORIES)]
            cat_markets = self._fetch_category(cat, per_strategy)
            all_markets.extend(cat_markets)
            save_memory("category_rotation_idx", cat_idx + 1, category='cache')
        except Exception as e:
            print(f"[MarketSelector] Ошибка стратегии category_rotation: {e}")

        # Стратегия 4: Top volume (fallback, текущее поведение)
        try:
            top = self.adapter.list_markets(limit=per_strategy)
            all_markets.extend(top)
        except Exception as e:
            print(f"[MarketSelector] Ошибка стратегии top_volume: {e}")

        return all_markets

    def _fetch_category(self, category: str, limit: int) -> List[Market]:
        """Получает рынки по категории."""
        try:
            return self.adapter.list_markets(limit=limit, category=category)
        except Exception as e:
            print(f"[MarketSelector] Ошибка загрузки категории '{category}': {e}")
            return []

    def _filter(self, markets: List[Market]) -> List[Market]:
        """
        Фильтрует рынки:
        - Убирает истёкшие (close_time в прошлом)
        - Убирает на cooldown (анализировались недавно)
        - Убирает крайние цены (< 0.05 или > 0.95 — скорее всего верно оценены)
        """
        now = datetime.now(timezone.utc)
        cooldown_ids = get_markets_on_cooldown(MARKET_COOLDOWN_HOURS)
        
        filtered = []
        for m in markets:
            # Рынок уже закрыт
            if m.close_time <= now:
                continue
            # Рынок на cooldown
            if m.id in cooldown_ids:
                continue
            # Крайние цены (почти наверняка верные)
            if m.price < 0.05 or m.price > 0.95:
                continue
            filtered.append(m)
        
        return filtered

    def _score_market(self, market: Market) -> float:
        """
        Scoring-функция. Чем выше скор — тем интереснее рынок для анализа.
        """
        score = 0.0
        now = datetime.now(timezone.utc)

        # Цена в зоне неопределённости (0.15–0.85) → интереснее
        if PRICE_RANGE_MIN <= market.price <= PRICE_RANGE_MAX:
            score += 2.0
        # Максимальная неопределённость (0.30–0.70)
        if 0.30 <= market.price <= 0.70:
            score += 1.0

        # Закрывается скоро → повышенная волатильность
        days_to_close = (market.close_time - now).days
        if 0 < days_to_close <= 1:
            score += 3.0  # Завтра закрывается
        elif 1 < days_to_close <= 7:
            score += 2.0  # На этой неделе
        elif 7 < days_to_close <= 30:
            score += 1.0  # В этом месяце
        # Очень далёкие рынки (> 180 дней) — менее интересны
        elif days_to_close > 180:
            score -= 1.0

        return score

    def get_auto_category(self) -> Optional[str]:
        """Возвращает текущую категорию из ротации (для логирования)."""
        cat_idx = get_memory("category_rotation_idx") or 0
        return SCAN_CATEGORIES[(cat_idx - 1) % len(SCAN_CATEGORIES)]
