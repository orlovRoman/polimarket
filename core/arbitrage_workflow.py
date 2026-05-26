"""
Оркестратор кросс-платформенного арбитражного скана.

Поток выполнения:
  PolymarketAdapter + KalshiAdapter
        ↓
  find_candidate_pairs() — keyword-матчинг
        ↓
  manual_market_pairs.json — ручные пары (всегда приоритет)
        ↓
  verify_pair_with_llm() — для «серой зоны» (score 0.50–0.72)
        ↓
  ArbitrageAgent.analyze_cross_platform() — 3 типа арбитража
        ↓
  save_cross_arbitrage() + Telegram-алерт (если спред ≥ min_spread_alert)
"""
import os
from typing import Optional

from agents.shared.adapters.polymarket import PolymarketAdapter
from agents.shared.adapters.kalshi import KalshiAdapter
from agents.shared.adapters.base_adapter import BaseMarketAdapter
from agents.polymarket_arbitrage_agent.src.agent import ArbitrageAgent
from agents.shared.python.db import save_cross_arbitrage, mark_cross_arbitrage_alerted
from core.models import CrossArbitrageSignal, Market
from services.market_matcher import find_candidate_pairs, load_manual_pairs, verify_pair_with_llm


def run_cross_platform_scan(
    api_key: Optional[str] = None,
    adapters: Optional[list[BaseMarketAdapter]] = None,
    poly_limit: int = 100,
    kalshi_limit: int = 100,
    min_match_score: float = 0.50,
    min_spread_alert: float = 5.0,
    dry_run: bool = False,
) -> list[CrossArbitrageSignal]:
    """
    Запускает полный цикл поиска кросс-платформенного арбитража.

    :param api_key: Google AI / Gemini API ключ (по умолчанию из env GOOGLE_API_KEY)
    :param adapters: Список адаптеров. По умолчанию [PolymarketAdapter, KalshiAdapter].
                     Добавьте ManifoldAdapter() для расширения на 3-ю платформу.
    :param poly_limit: Лимит рынков с Polymarket
    :param kalshi_limit: Лимит рынков с Kalshi
    :param min_match_score: Минимальная схожесть названий (0–1)
    :param min_spread_alert: Минимальный спред (%) для отправки алерта в Telegram
    :param dry_run: Если True — только показывает кандидатов без вызова LLM
    :return: Список найденных арбитражных сигналов
    """
    api_key = api_key or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY не задан")

    # 1. Загружаем рынки со всех платформ
    if adapters is None:
        adapters = [PolymarketAdapter(), KalshiAdapter()]

    all_markets: dict[str, list[Market]] = {}
    for adapter in adapters:
        print(f"[SCAN] Загружаю {adapter.name}...")
        limit = poly_limit if adapter.name == "polymarket" else kalshi_limit
        try:
            all_markets[adapter.name] = adapter.list_markets(limit=limit)
            print(f"[SCAN] {adapter.name}: {len(all_markets[adapter.name])} рынков")
        except Exception as e:
            print(f"[SCAN] Ошибка загрузки {adapter.name}: {e}")
            all_markets[adapter.name] = []

    # 2. Ручные пары — высший приоритет
    manual_raw = load_manual_pairs()
    manual_pairs: list[tuple[Market, Market, float]] = []
    if manual_raw:
        poly_by_id = {m.id: m for m in all_markets.get("polymarket", [])}
        kalshi_by_id = {m.id: m for m in all_markets.get("kalshi", [])}
        for entry in manual_raw:
            ma = poly_by_id.get(entry.get("poly_id", ""))
            mb = kalshi_by_id.get(entry.get("kalshi_id", ""))
            if ma and mb:
                manual_pairs.append((ma, mb, 1.0))
            else:
                print(f"[SCAN] Ручная пара не найдена в рынках: {entry}")

    # 3. Автоматический keyword-матчинг
    poly_markets = all_markets.get("polymarket", [])
    kalshi_markets = all_markets.get("kalshi", [])

    auto_pairs = find_candidate_pairs(poly_markets, kalshi_markets, min_score=min_match_score)

    # Дедупликация с ручными парами
    manual_ids = {(ma.id, mb.id) for ma, mb, _ in manual_pairs}
    auto_pairs = [(ma, mb, s) for ma, mb, s in auto_pairs if (ma.id, mb.id) not in manual_ids]

    all_candidates = manual_pairs + auto_pairs
    print(f"[SCAN] Итого кандидатов: {len(all_candidates)} "
          f"(manual={len(manual_pairs)}, auto={len(auto_pairs)})")

    # Dry run — только показываем кандидатов, не вызываем LLM
    if dry_run:
        print("\n=== КАНДИДАТЫ ДЛЯ РУЧНОГО КОНФИГА ===")
        for i, (ma, mb, score) in enumerate(auto_pairs[:20]):
            print(f"{i+1}. [{score:.2f}] POLY:   {ma.title[:60]}")
            print(f"         KALSHI: {mb.title[:60]}")
            print(f"   Ценовой спред: {abs(ma.price - mb.price) * 100:.1f}¢")
            print()
        return []

    # 4. LLM-верификация «серой зоны» для автоматических пар
    verified: list[tuple[Market, Market, float]] = list(manual_pairs)
    for ma, mb, score in auto_pairs[:50]:
        if score >= 0.72:
            # Высокая уверенность — берём без LLM
            verified.append((ma, mb, score))
        elif score >= 0.50:
            # Серая зона — спрашиваем LLM
            try:
                llm_result = verify_pair_with_llm(ma, mb, api_key)
                if llm_result.get("is_same_event") and llm_result.get("confidence", 0) >= 0.75:
                    verified.append((ma, mb, llm_result["confidence"]))
            except Exception as e:
                print(f"[SCAN] Ошибка LLM-верификации пары: {e}")

    print(f"[SCAN] Верифицировано пар: {len(verified)}")

    # 5. Анализ каждой пары арбитражным агентом
    agent = ArbitrageAgent(api_key=api_key)
    found: list[CrossArbitrageSignal] = []

    for ma, mb, match_score in verified:
        try:
            signal = agent.analyze_cross_platform(ma, mb, match_score)
        except Exception as e:
            print(f"[SCAN] Ошибка анализа пары {ma.id} / {mb.id}: {e}")
            continue

        if not signal:
            continue

        save_cross_arbitrage(signal)

        if signal.has_arbitrage and signal.spread_percent >= min_spread_alert:
            found.append(signal)
            print(f"[SCAN] 🔥 АРБИТРАЖ: {signal.arbitrage_type} "
                  f"спред={signal.spread_percent:.1f}%  "
                  f"POLY: {signal.market_a_title[:35]} / KALSHI: {signal.market_b_title[:35]}")

    print(f"[SCAN] Итого арбитражей: {len(found)}")
    return found
