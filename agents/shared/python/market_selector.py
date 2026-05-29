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
    get_memory, save_memory, get_markets_on_cooldown, get_last_analyzed_price,
    is_in_market_list
)
from core.models import Market


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
        total_limit = int(total_limit)  # Защита от float из json.loads
        
        if category:
            # Ручной скан: одна категория
            raw = self._fetch_category(category, total_limit * 2)
            filtered = self._filter(raw, category, min_hours=12)
        else:
            # Автоскан: микс стратегий
            regular_raw = self._fetch_mixed_no_ending(total_limit * 3)
            
            per_strategy = int(max((total_limit * 3) // 4, 5))
            ending_raw = []
            try:
                ending_raw = self.adapter.list_markets_ending_soon(limit=per_strategy)
            except Exception as e:
                print(f"[MarketSelector] Ошибка стратегии ending_soon: {e}")
                
            filtered_regular = self._filter(regular_raw, category, min_hours=12)
            filtered_ending = self._filter(ending_raw, category, min_hours=1)
            filtered = filtered_regular + filtered_ending

        # Scoring + дедупликация
        seen_ids = set()
        scored = []
        for m in filtered:
            if m.id not in seen_ids:
                seen_ids.add(m.id)
                scored.append((self._score_market(m, category), m))

        # Сортируем по убыванию скора
        scored.sort(key=lambda x: x[0], reverse=True)
        
        return [m for _, m in scored[:total_limit]]

    def _fetch_mixed_no_ending(self, fetch_limit: int) -> List[Market]:
        """Собирает регулярные рынки из нескольких стратегий (без ending_soon)."""
        per_strategy = int(max(fetch_limit // 3, 5))
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

        # Стратегия 2: Категория из ротации
        try:
            cat_idx = get_memory("category_rotation_idx") or 0
            cat = SCAN_CATEGORIES[cat_idx % len(SCAN_CATEGORIES)]
            cat_markets = self._fetch_category(cat, per_strategy)
            all_markets.extend(cat_markets)
            save_memory("category_rotation_idx", cat_idx + 1, category='cache')
        except Exception as e:
            print(f"[MarketSelector] Ошибка стратегии category_rotation: {e}")

        # Стратегия 3: Top volume (fallback, текущее поведение)
        try:
            top = self.adapter.list_markets(limit=per_strategy)
            all_markets.extend(top)
        except Exception as e:
            print(f"[MarketSelector] Ошибка стратегии top_volume: {e}")

        return all_markets

    def _fetch_category(self, category: str, limit: int) -> List[Market]:
        """Получает рынки по категории."""
        try:
            if category == "penny_stocks":
                # Ищем среди топ-1000 рынков по объему, так как дешевые редко в топе
                all_markets = self.adapter.list_markets_paged(limit=500, offset=0, order="volume")
                all_markets += self.adapter.list_markets_paged(limit=500, offset=500, order="volume")
                penny = [m for m in all_markets if 0.01 <= m.price <= 0.05 or 0.95 <= m.price <= 0.99]
                return penny[:limit]
            
            return self.adapter.list_markets(limit=limit, category=category)
        except Exception as e:
            print(f"[MarketSelector] Ошибка загрузки категории '{category}': {e}")
            return []

    def _filter(self, markets: List[Market], scan_category: str = None, min_hours: int = 12) -> List[Market]:
        """
        Фильтрует рынки:
        - Убирает истёкшие или закрывающиеся менее чем через min_hours часов рынки
        - Убирает абсолютно мертвые цены (< 0.01 или > 0.99)
        - Убирает на cooldown, ЕСЛИ их цена не изменилась значительно (>= 3%)
        - Убирает рынки из списков 'Игнорировать' и 'Следить'
        """
        now = datetime.now(timezone.utc)
        cooldown_ids = get_markets_on_cooldown(MARKET_COOLDOWN_HOURS)
        
        filtered = []
        for m in markets:
            # Рынок уже закрыт или закроется в течение min_hours часов
            if (m.close_time - now).total_seconds() < min_hours * 3600:
                continue
            
            # Рынок в списке Игнорировать или Следить — пропускаем при стандартном скане
            if is_in_market_list(m.id, 'ignored') or is_in_market_list(m.id, 'watching'):
                continue
            
            # Абсолютно мертвые цены (кроме penny_stocks)
            if scan_category != "penny_stocks":
                if m.price < 0.01 or m.price > 0.99:
                    continue
                
            # Рынок на cooldown
            if m.id in cooldown_ids:
                last_price = get_last_analyzed_price(m.id)
                if last_price is not None:
                    price_diff = abs(last_price - m.price)
                    if price_diff < 0.03:
                        # Цена стабильна, оставляем в кулдауне (пропускаем)
                        continue
                    else:
                        # Цена изменилась >= 3%, ИГНОРИРУЕМ кулдаун и пропускаем дальше!
                        pass
                        
            filtered.append(m)
        
        return filtered

    def _score_market(self, market: Market, scan_category: str = None) -> float:
        """
        Scoring-функция. Чем выше скор — тем интереснее рынок для анализа.
        """
        score = 0.0
        now = datetime.now(timezone.utc)

        # Если это режим penny_stocks, даем им максимальный приоритет
        if scan_category == "penny_stocks":
            if 0.01 <= market.price <= 0.05 or 0.95 <= market.price <= 0.99:
                score += 10.0
        else:
            # Обычный режим: Цена в зоне неопределённости (0.15–0.85) → интересно для SCOUT
            if PRICE_RANGE_MIN <= market.price <= PRICE_RANGE_MAX:
                score += 10.0
            else:
                # Рынки < 0.15 или > 0.85 получают меньший приоритет
                score += 2.0
            
            # Сильный перекос (≤ 0.10 или ≥ 0.90) → очень интересно для SWING_TRADER
            if market.price <= 0.10 or market.price >= 0.90:
                score += 3.0

        # Закрывается скоро → повышенная волатильность
        days_to_close = (market.close_time - now).total_seconds() / 86400
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
