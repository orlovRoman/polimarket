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
    get_memory, save_memory, get_markets_on_cooldown, get_last_analyzed_prices,
    get_all_listed_market_ids
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
        
        now = datetime.now(timezone.utc)
        
        if category:
            # Ручной скан: одна категория
            raw = self._fetch_category(category, total_limit * 2, now=now, min_hours=12)
            filtered = self._filter(raw, category, min_hours=12, now=now)
        else:
            # Автоскан: микс стратегий
            regular_raw = self._fetch_mixed_no_ending(total_limit * 3)
            
            per_strategy = int(max((total_limit * 3) // 4, 5))
            ending_raw = []
            try:
                ending_raw = self.adapter.list_markets_ending_soon(limit=per_strategy)
            except Exception as e:
                print(f"[MarketSelector] Ошибка стратегии ending_soon: {e}")
                
            filtered_regular = self._filter(regular_raw, category, min_hours=12, now=now)
            filtered_ending = self._filter(ending_raw, category, min_hours=1, now=now)
            filtered = filtered_regular + filtered_ending

        # Scoring + дедупликация
        seen_ids = set()
        scored = []
        for m in filtered:
            if m.id not in seen_ids:
                seen_ids.add(m.id)
                scored.append((self._score_market(m, category, now=now), m))

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
            cat_markets = self._fetch_category(cat, per_strategy, min_hours=12)
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

    def _fetch_category(self, category: str, limit: int, now: datetime = None, min_hours: int = 12) -> List[Market]:
        """Получает рынки по категории."""
        if now is None:
            now = datetime.now(timezone.utc)
        try:
            if category == "penny_stocks":
                # Ищем среди топ-1000 рынков по объему.
                # Используем честную пагинацию по 100 элементов из-за ограничений Gamma API
                from config import logger
                all_markets = []
                for offset in range(0, 1000, 100):
                    try:
                        chunk = self.adapter.list_markets_paged(limit=100, offset=offset, order="volume")
                        if not chunk:
                            break
                        all_markets.extend(chunk)
                    except Exception as e:
                        logger.warning(f"[MarketSelector] Ошибка загрузки страницы {offset // 100} для penny_stocks: {e}")
                        continue
                if len(all_markets) < 300:
                    logger.warning(f"[MarketSelector] Загружено мало рынков для penny_stocks ({len(all_markets)} < 300). Возможна потеря данных.")
                # Предфильтр: убираем уже закрытые до price-фильтра
                alive = [m for m in all_markets if (m.close_time - now).total_seconds() > min_hours * 3600]
                penny = [m for m in alive if (0.01 <= m.price <= 0.10) or (0.90 <= m.price <= 0.99)]
                return penny[:limit]
            
            if category in ("favourite_compound", "favourite_compounding"):
                # Для Favourite Compounding сканируем компактные рынки, как в main.py
                from agents.shared.python.db import get_compound_settings
                cfg = get_compound_settings()
                min_vol = cfg.get("min_volume", 1000.0)
                min_p = cfg.get("min_price", 0.95)
                max_h = cfg.get("max_hours", 48.0)

                all_compact = self.adapter.list_all_markets_compact()
                candidates_ids = []
                for cm in all_compact:
                    try:
                        price_yes = float(cm["p"])
                        fav_price = price_yes if price_yes >= 0.5 else (1.0 - price_yes)
                        volume = float(cm["vol"])
                        end_raw = cm["end"]
                        if not end_raw:
                            continue
                        close_time = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
                        hours_left = (close_time - now).total_seconds() / 3600
                        
                        if min_p <= fav_price <= 0.99 and volume >= min_vol and min_hours <= hours_left <= max_h:
                            candidates_ids.append(cm["id"])
                    except Exception:
                        continue
                
                compounds = []
                for m_id in candidates_ids[:limit]:
                    try:
                        m_obj = self.adapter.get_market(m_id)
                        if m_obj:
                            compounds.append(m_obj)
                    except Exception as exc:
                        print(f"[MarketSelector] Ошибка загрузки рынка {m_id} для Favourite Compounding: {exc}")
                return compounds
            
            return self.adapter.list_markets(limit=limit, category=category)
        except Exception as e:
            print(f"[MarketSelector] Ошибка загрузки категории '{category}': {e}")
            return []

    def _filter(self, markets: List[Market], scan_category: str = None, min_hours: int = 12, now: datetime = None) -> List[Market]:
        """
        Фильтрует рынки:
        - Убирает истёкшие или закрывающиеся менее чем через min_hours часов рынки
        - Убирает абсолютно мертвые цены (< 0.01 или > 0.99)
        - Убирает на cooldown, ЕСЛИ их цена не изменилась значительно (>= 3%)
        - Убирает рынки из списков 'Игнорировать' и 'Следить'
        - Убирает рынки, содержащие заблокированные теги
        """
        if now is None:
            now = datetime.now(timezone.utc)
        cooldown_ids = get_markets_on_cooldown(MARKET_COOLDOWN_HOURS)
        listed_ids = get_all_listed_market_ids()
        last_prices = get_last_analyzed_prices(cooldown_ids)
        
        blacklisted = []
        if scan_category != "penny_stocks":
            from agents.shared.python.db import get_blacklist_tags
            blacklisted = [t.lower() for t in get_blacklist_tags()]
        
        filtered = []
        for m in markets:
            # Фильтр черного списка тегов (по слагу и заголовку)
            if blacklisted:
                slug_lower = (m.event_slug or m.id or "").lower()
                title_lower = (m.title or "").lower()
                if any(tag in slug_lower or tag in title_lower for tag in blacklisted):
                    continue

            # Рынок уже закрыт или закроется в течение min_hours часов
            if (m.close_time - now).total_seconds() < min_hours * 3600:
                continue
            
            # Рынок в списке Игнорировать или Следить — пропускаем при стандартном скане
            if m.id in listed_ids['ignored'] or m.id in listed_ids['watching']:
                continue
            
            # Абсолютно мертвые цены (кроме penny_stocks)
            if scan_category != "penny_stocks":
                if m.price < 0.01 or m.price > 0.99:
                    continue
                
            # Рынок на cooldown
            if m.id in cooldown_ids:
                continue
            
            filtered.append(m)
        
        return filtered

    def _score_market(self, market: Market, scan_category: str = None, now: datetime = None) -> float:
        """
        Scoring-функция. Чем выше скор — тем интереснее рынок для анализа.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        score = 0.0

        # Если это режим penny_stocks, даем им максимальный приоритет
        if scan_category == "penny_stocks":
            if (0.01 <= market.price <= 0.10) or (0.90 <= market.price <= 0.99):
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
