import threading
import logging
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from config import GOOGLE_API_KEY, SCAN_LIMIT_DEFAULT
from agents.shared.adapters.polymarket import PolymarketAdapter
from agents.shared.python.db import (
    init_db, save_market, get_last_analyzed_price,
    save_price_point, add_discussion_message, mark_market_analyzed,
    save_agent_episode
)
from core.models import Market
from agents.polymarket_mispricing_agent.src.agent import ScoutAgent
from agents.polymarket_swing_agent.src.agent import SwingAgent
from agents.polymarket_insider_agent.src.agent import ShadowAgent
from agents.orchestrator.src.agent import NexusAgent
from agents.shared.python.market_selector import MarketSelector
from core.workflow import run_screening, run_agent_evaluation, process_consensus
from services.notifications import send_telegram as send_telegram_alert

logger = logging.getLogger("NexusPolyBot.CoreEngine")

import inspect
import traceback
import html

from core.guards import LLMUnavailableError

from core.utils import _callback_accepts_reply_markup

class NoMarketsFoundError(Exception):
    """Исключение, выбрасываемое когда активные рынки по фильтрам не найдены."""
    pass

async def _run_math_gate(
    markets: list[Market],
    api_key: str,
    notify_fn,          # функция отправки сигнала в Telegram
    min_spread_pct: float = 5.0,
    cancellation_token: threading.Event = None,
) -> list[str]:
    """
    Детерминированный gate. Запускается ДО всех агентов.
    Возвращает список market_id, которые уже обработаны — агентам пропускать.
    """
    from core.event_cluster import cluster_by_event_slug, iter_cluster_pairs
    from core.arb_router import route_ambiguous
    from core.math_filter import FilterDecision, math_pre_filter
    from services.market_matcher import get_matched_pairs
    from core.math_filter_metrics import log_filter_result
    import inspect

    processed_ids: list[str] = []

    async def _notify_helper(fn, signal_type: str, market: Market, details):
        sig = inspect.signature(fn)
        if "signal_type" in sig.parameters:
            if inspect.iscoroutinefunction(fn):
                await fn(signal_type=signal_type, market=market, details=details)
            else:
                fn(signal_type=signal_type, market=market, details=details)
        else:
            emoji = "🚨" if signal_type == "MATH_ARB" else "⚡️"
            desc = "МАТЕМАТИЧЕСКИЙ АРБИТРАЖ" if signal_type == "MATH_ARB" else "ПОДТВЕРЖДЕННЫЙ МАТЕМАТИЧЕСКИЙ АРБИТРАЖ"
            text = (
                f"{emoji} <b>{desc}</b> {emoji}\n\n"
                f"📍 <b>Рынок A:</b> <a href='{market.url}'>{market.title}</a> (Цена: {market.price})\n"
                f"💡 <b>Тип:</b> {details.arbitrage_type}\n"
                f"📈 <b>Разрыв (Spread):</b> {details.spread_pct:.1f}%\n"
                f"🧠 <b>Логика:</b> {details.reasoning}\n"
                f"⚡ <b>Трейд:</b> {details.trade_instruction}\n"
            )
            if inspect.iscoroutinefunction(fn):
                await fn(text)
            else:
                fn(text)

    # 1. Кластеризация внутриплатформенных пар
    clusters = cluster_by_event_slug(markets)
    logger.info(f"[math_gate] Кластеров: {len(clusters)}, рынков в парах: "
                f"{sum(len(v) for v in clusters.values())}")

    for market_a, market_b, mf in iter_cluster_pairs(
        clusters, min_spread_pct=min_spread_pct
    ):
        if cancellation_token and cancellation_token.is_set():
            logger.info("[math_gate] Cancellation requested, stopping cluster pairs loop.")
            break

        log_filter_result(market_a.id, market_b.id, mf)

        if mf.decision == FilterDecision.CONFIRMED_ARBITRAGE:
            await _notify_helper(
                notify_fn,
                signal_type="MATH_ARB",
                market=market_a,
                details=mf,
            )
            processed_ids.extend([market_a.id, market_b.id])

        elif mf.decision == FilterDecision.AMBIGUOUS:
            result = await asyncio.to_thread(
                route_ambiguous, mf, market_a, market_b, api_key
            )
            if result and result.get("confirmed_arb"):
                await _notify_helper(
                    notify_fn,
                    signal_type="MATH_ARB_CONFIRMED",
                    market=market_a,
                    details=mf,
                )
                processed_ids.extend([market_a.id, market_b.id])

    # Кросс-платформенное сканирование удалено

    return processed_ids


class CoreEngine:
    _instance = None
    _lock = threading.Lock()
    _scan_lock = threading.Lock()
    _penny_scan_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "initialized"):
            try:
                self.active_markets: Dict[str, Any] = {}
                self._markets_lock = threading.Lock()
                self._state_lock = threading.Lock()
                self.state: Dict[str, Any] = {
                    "category": "Авто-микс",
                    "stage": "Инициализация",
                    "total_markets": 0,
                    "current_market_index": 0,
                    "current_market_title": "Ожидание...",
                    "current_market_url": "",
                    "scout_status": "⏳ Ожидает",
                    "swing_status": "⏳ Ожидает",
                    "shadow_status": "⏳ Ожидает",
                    "ideas_found": 0
                }
                self.api_key = GOOGLE_API_KEY
                if not self.api_key:
                    logger.error("ОШИБКА: GOOGLE_API_KEY не установлен.")
                from agents.shared.python.db import get_memory
                def get_am(agent_name):
                    return get_memory(f"agent_config_{agent_name}", {}).get("model", "gemini-2.5-flash")
                
                self.scout = ScoutAgent(api_key=self.api_key, model=get_am("SCOUT"))
                self.swing = SwingAgent(api_key=self.api_key, model=get_am("SWING"))
                self.shadow = ShadowAgent(api_key=self.api_key, model=get_am("SHADOW"))
                self.nexus = NexusAgent(api_key=self.api_key, model_name=get_am("NEXUS"))
                self.adapter = PolymarketAdapter()
                init_db()
                self.initialized = True
            except Exception as e:
                logger.error(f"Ошибка инициализации CoreEngine: {e}", exc_info=True)
                for attr in ("scout", "swing", "shadow", "nexus", "adapter"):
                    obj = getattr(self, attr, None)
                    if obj and hasattr(obj, "close"):
                        try: obj.close()
                        except Exception: pass
                CoreEngine._instance = None  # позволяет пересоздать после исправления конфига
                raise

    def update_state(self, **kwargs):
        with self._state_lock:
            self.state.update(kwargs)

    def get_status(self) -> Dict[str, Any]:
        with self._state_lock:
            return self.state.copy()

    def get_active_markets(self) -> Dict[str, Any]:
        with self._markets_lock:
            return self.active_markets.copy()

    def run_team_discussion(self, log_callback=None, summary_callback=None, category=None, market_id=None, state_callback=None, **kwargs):
        if market_id is None:
            lock_to_use = self._penny_scan_lock if category == "penny_stocks" else self._scan_lock
            if not lock_to_use.acquire(blocking=False):
                scan_type = "Penny Stocks " if category == "penny_stocks" else ""
                logger.warning(f"Сканирование {scan_type}уже выполняется (другой поток). Пропускаем.")
                raise RuntimeError(f"Сканирование {scan_type}уже выполняется в фоновом режиме. Пожалуйста, подождите завершения текущего цикла.")
            try:
                return self._run_team_discussion_inner(log_callback, summary_callback, category, market_id, state_callback, **kwargs)
            finally:
                lock_to_use.release()
        else:
            # Для точечного анализа (из Telegram) пропускаем глобальный лок сканирования
            return self._run_team_discussion_inner(log_callback, summary_callback, category, market_id, state_callback, **kwargs)

    def _run_team_discussion_inner(self, log_callback=None, summary_callback=None, category=None, market_id=None, state_callback=None, **kwargs):
        import asyncio
        loop = None
        try:
            loop = asyncio.new_event_loop()
            from core.guards import LLMUnavailableError
            
            # ── АВТОРЕЗОЛЮЦИЯ: закрываем истёкшие PENDING-сигналы ──
            try:
                from services.signal_resolver import resolve_pending_signals
                n_resolved = resolve_pending_signals()
                if n_resolved:
                    logger.info(f"[Engine] Авторезолюция: закрыто {n_resolved} сигналов перед сканированием")
            except Exception as _e:
                logger.warning(f"[Engine] Авторезолюция не выполнена: {_e}")
            # ────────────────────────────────────────────────────────
            
            if summary_callback is None:
                summary_callback = send_telegram_alert

            def log(msg):
                logger.info(msg)
                if log_callback:
                    try: log_callback(msg)
                    except Exception as e: logger.error(f"log_callback error: {e}")

            scan_limit = self._get_scan_limit()

            def _update_state(**kwargs):
                self.update_state(**kwargs)
                if state_callback:
                    try: state_callback(self.state)
                    except Exception as e: logger.error(f"state_callback error: {e}")

            _update_state(category=category or "Авто-микс", stage="Скрининг рынков", total_markets=0, ideas_found=0)

            # 1. Скрининг
            try:
                screened_market_ids = run_screening(self.adapter, category, market_id, summary_callback)
            except LLMUnavailableError as e:
                agent_name = getattr(e, "agent_name", "NEXUS")
                log(f"🔴 LLM API недоступен для агента {agent_name} во время скрининга.")
                if summary_callback:
                    try:
                        reply_markup = {
                            "inline_keyboard": [
                                [{"text": f"🔄 Сменить модель для {agent_name}", "callback_data": f"set_model_{agent_name}"}]
                            ]
                        }
                        text = f"🔴 <b>LLM недоступна у агента {agent_name}</b>. Сканирование остановлено. Попробуйте позже."
                        if _callback_accepts_reply_markup(summary_callback):
                            summary_callback(text, reply_markup=reply_markup)
                        else:
                            summary_callback(text)
                    except Exception as cb_err:
                        logger.error(f"summary_callback error: {cb_err}")
                _update_state(stage="Ошибка (LLM недоступна)")
                raise e

            # 2. Отбор
            cat_msg = f" в категории '{category}'" if category else " (авто-микс)"
            if market_id: cat_msg = f" (точечный анализ {market_id})"
            log(f"\n--- 1. Поиск новых рынков{cat_msg} ---")
            _update_state(stage="Отбор рынков")
            
            markets = self._select_markets(screened_market_ids, scan_limit, category, market_id, log)

            if not markets:
                _update_state(stage="Нет рынков")
                raise NoMarketsFoundError(f"Нет активных рынков для анализа{cat_msg}.")

            for m in markets: save_market(m)

            # 3. Обсуждение
            log("\n--- 2. Обсуждение идей (SCOUT + SWING + SHADOW) ---")
            
            processed_ids = self._run_math_gate_sync(markets, summary_callback)

            import uuid
            run_id = str(uuid.uuid4())[:8]

            remaining_markets = [m for m in markets if m.id not in processed_ids]
            _update_state(
                total_markets=len(remaining_markets),
                current_market_index=0,
                stage="Обсуждение (SCOUT + SWING + SHADOW)"
            )
            
            for i, m in enumerate(remaining_markets, 1):
                import config
                if getattr(config, "shutdown_requested", False):
                    log("🛑 Прерывание сканирования: запрошена остановка системы.")
                    break
                self._process_single_market(m, i, summary_callback, _update_state, log, market_id=market_id, category=category, run_id=run_id, loop=loop, **kwargs)
                    
            _update_state(
                stage="Завершено",
                current_market_index=0,
                total_markets=0,
                current_market_title="",
                current_market_url="",
                scout_status="⏳ Ожидает",
                swing_status="⏳ Ожидает",
                shadow_status="⏳ Ожидает",
            )
            log("\n✅ Обсуждение завершено.")
            return len(markets)
        finally:
            if loop and not loop.is_closed():
                loop.close()

    def _get_scan_limit(self) -> int:
        from agents.shared.python.db import get_scout_settings
        settings = get_scout_settings()
        scan_limit = settings.get("scan_limit", SCAN_LIMIT_DEFAULT)
        try:
            scan_limit = int(scan_limit)
        except (TypeError, ValueError):
            scan_limit = SCAN_LIMIT_DEFAULT
            
        if scan_limit <= 0:
            scan_limit = SCAN_LIMIT_DEFAULT
        return scan_limit

    def _select_markets(self, screened_market_ids: list[str], scan_limit: int, category: Optional[str], market_id: Optional[str], log) -> list[Market]:
        markets = []
        if market_id:
            try:
                m = self.adapter.get_market(market_id)
                if m: markets.append(m)
            except Exception as e:
                log(f"  Ошибка загрузки рынка {market_id}: {e}")
        elif screened_market_ids and not category:
            raw_markets = []
            for mid in screened_market_ids[:scan_limit * 2]:
                try:
                    m = self.adapter.get_market(mid)
                    if m: raw_markets.append(m)
                except Exception as e:
                    logger.debug(f"Failed to fetch market {mid}: {e}")
                    continue
            selector = MarketSelector(self.adapter)
            markets = selector._filter(raw_markets, scan_category=category)[:scan_limit]
        else:
            selector = MarketSelector(self.adapter)
            markets = selector.select(total_limit=scan_limit, category=category)
            if not category:
                log(f"  Категория ротации: {selector.get_auto_category()}")
        return markets

    def _run_math_gate_sync(self, markets: list[Market], summary_callback) -> list[str]:
        """Всегда запускает в изолированном потоке — предсказуемо."""
        import threading
        from concurrent.futures import Future
        
        cancel_event = threading.Event()
        
        def _factory():
            return _run_math_gate(
                markets=markets, api_key=self.api_key,
                notify_fn=summary_callback, min_spread_pct=5.0,
                cancellation_token=cancel_event
            )
        
        fut: Future = Future()
        def _runner(f, factory):
            try:
                import asyncio
                f.set_result(asyncio.run(factory()))
            except Exception as ex:
                f.set_exception(ex)
        
        t = threading.Thread(target=_runner, args=(fut, _factory), daemon=True)
        t.start()
        t.join(timeout=120)  # таймаут на весь math_gate
        if not t.is_alive():
            return fut.result()
        logger.error("[math_gate] Timeout exceeded (120s), cancelling task...")
        cancel_event.set()
        return []

    def _fetch_pre_orderbook(self, market: Market) -> Optional[Any]:
        pre_orderbook = None
        if market.tokens:
            try:
                ob_raw = self.adapter.get_orderbook(market.tokens[0])
                if ob_raw:
                    from core.context import OrderbookSnapshot
                    pre_orderbook = OrderbookSnapshot(
                        top_bid=ob_raw.get("top_bid"),
                        top_ask=ob_raw.get("top_ask"),
                        spread_cents=round(ob_raw["spread"] * 100, 4) if ob_raw.get("spread") is not None else None,
                        bid_depth_5=ob_raw.get("bid_depth_5"),
                        ask_depth_5=ob_raw.get("ask_depth_5"),
                    )
            except Exception as e:
                logger.error(f"Failed to fetch pre-orderbook: {e}")
        return pre_orderbook

    def _run_shadow_analysis(self, m: Market, active_signal, signal, swing_signal, context, price_hist, _update_state, log):
        from core.guards import LLMUnavailableError
        from core.checkpoint import save_checkpoint
        opinion_shadow = None
        if active_signal:
            if signal:
                log(f"  SCOUT: Edge: {signal.edge:.2f}")
                _update_state(scout_status=f"🟢 Edge ({signal.edge:.2f})")
            else: _update_state(scout_status="⚪️ Нет фундамента")
                
            if swing_signal:
                log("  SWING: Хайп найден!")
                _update_state(swing_status="🚀 Ждет памп")
            else: _update_state(swing_status="⚪️ Нет хайпа")
                
            _update_state(shadow_status="🔄 Проверяет ордербук...")
            
            from core.constants import Outcome
            target_outcome = getattr(active_signal, 'target_outcome', Outcome.YES)
            # Если выбран исход NO и у нас есть соответствующий токен (tokens[1]),
            # дозагружаем ордербук для NO, чтобы скорректировать контекст для SHADOW.
            # В противном случае context.orderbook уже содержит YES-стакан (pre_orderbook).
            if target_outcome.upper() == Outcome.NO and len(m.tokens) > 1:
                try:
                    ob_raw = self.adapter.get_orderbook(m.tokens[1])
                    if ob_raw:
                        from core.context import OrderbookSnapshot
                        context.orderbook = OrderbookSnapshot(
                            top_bid=ob_raw.get("top_bid"),
                            top_ask=ob_raw.get("top_ask"),
                            spread_cents=round(ob_raw["spread"] * 100, 4) if ob_raw.get("spread") is not None else None,
                            bid_depth_5=ob_raw.get("bid_depth_5"),
                            ask_depth_5=ob_raw.get("ask_depth_5"),
                        )
                except Exception as e:
                    logger.error(f"Failed to fetch orderbook for NO: {e}")

            # Для обратной совместимости с check_liquidity_fast формируем orderbook dict из context.orderbook
            orderbook = None
            if context.orderbook is not None:
                orderbook = {
                    "top_bid": context.orderbook.top_bid,
                    "top_ask": context.orderbook.top_ask,
                    "spread": round(context.orderbook.spread_cents / 100, 4) if context.orderbook.spread_cents is not None else None,
                    "bid_depth_5": context.orderbook.bid_depth_5,
                    "ask_depth_5": context.orderbook.ask_depth_5,
                }
            
            log("  SHADOW проверяет...")
            from services.onchain_provider import get_recent_trades, get_top_positions
            from core.smart_money import analyze_smart_money

            onchain_trades = get_recent_trades(m.condition_id) if m.condition_id else []
            onchain_positions = get_top_positions(m.condition_id) if m.condition_id else []
            smart_money = analyze_smart_money(onchain_trades, onchain_positions)
            
            # Добавляем smart_money в контекст
            context.smart_money = smart_money
            
            from services.wallet_tracker import ingest_trades
            ingest_trades(m.id, onchain_trades)
            
            from core.onchain_scorer import compute_onchain_score
            oc_score = compute_onchain_score(smart_money, target_outcome=target_outcome)
            
            # Корректируем edge детерминированно (без LLM)
            if active_signal and signal and oc_score and getattr(oc_score, 'confidence', 0.0) > 0.3:
                if oc_score.direction == "CONFIRM":
                    signal.edge = min(signal.edge * (1 + oc_score.score * 0.2), 0.95)
                elif oc_score.direction == "CONTRA":
                    signal.edge = signal.edge * (1 - abs(oc_score.score) * 0.3)
            
            # В контекст уходит только аннотация — 1 строка, ~10 токенов
            context.onchain_annotation = getattr(oc_score, 'annotation', '')
            
            try:
                scout_opinion = getattr(signal, 'details', '') or getattr(signal, 'signal_cause', '') if signal else ""
                swing_opinion = getattr(swing_signal, 'details', '') or getattr(swing_signal, 'catalyst', '') if swing_signal else ""
                combined_opinion = "\n\n".join(filter(None, [scout_opinion, swing_opinion]))
                
                from core.liquidity_checker import check_liquidity_fast
                liq = check_liquidity_fast(orderbook)
                has_smart_money = bool(smart_money and getattr(smart_money, 'available', False))
                
                if not liq.ok and not has_smart_money:
                    from core.models import AgentOpinion
                    opinion_shadow = AgentOpinion(
                        agent_name="SHADOW",
                        market_id=m.id,
                        opinion=liq.reason,
                        confidence=liq.confidence,
                        agree=False,
                        orderbook_facts=liq.reason,
                        risk_assessment="Ордербук пуст, Smart Money отсутствуют",
                        shadow_verdict="SHADOW: авто-отклонение (нет данных)",
                        liquidity_risk=liq.liquidity_risk
                    )
                    logger.info(f"  [SHADOW fast-path] Авто-отклонение: {liq.reason}")
                    # Сохраняем эпизод fast-path авто-отклонения
                    try:
                        save_agent_episode(
                            agent_name="SHADOW",
                            event_type="signal_evaluated",
                            market_id=m.id,
                            market_title=m.title,
                            summary=f"Agree=False, confidence={liq.confidence:.2%}, target={str(target_outcome)}, price={m.price:.2f} (fast-path: {liq.reason})",
                            context={
                                "price": m.price,
                                "target_outcome": str(target_outcome),
                                "agree": False,
                                "confidence": liq.confidence,
                                "liquidity_risk": liq.liquidity_risk,
                                "opinion": liq.reason
                            },
                            outcome="unknown"
                        )
                    except Exception as ep_err:
                        logger.error(f"[SHADOW fast-path] Ошибка сохранения эпизода: {ep_err}")
                else:
                    from core.whale_gate import check_whale_gate
                    gate = check_whale_gate(oc_score)
                    if not gate.allow:
                        gate_confidence = getattr(oc_score, 'confidence', 0.5) if oc_score else 0.5
                        from core.models import AgentOpinion
                        opinion_shadow = AgentOpinion(
                            agent_name="SHADOW",
                            market_id=m.id,
                            opinion=gate.reason,
                            confidence=gate_confidence,
                            agree=False,
                            orderbook_facts="Whale Gate active",
                            risk_assessment="Крупные кошельки торгуют против идеи",
                            shadow_verdict=gate.reason,
                            liquidity_risk="HIGH"
                        )
                        logger.info(f"  [WHALE GATE] {gate.reason}")
                        # Сохраняем эпизод Whale Gate блокировки
                        try:
                            save_agent_episode(
                                agent_name="SHADOW",
                                event_type="signal_evaluated",
                                market_id=m.id,
                                market_title=m.title,
                                summary=f"Agree=False, confidence={gate_confidence:.2%}, target={str(target_outcome)}, price={m.price:.2f} (Whale Gate: {gate.reason})",
                                context={
                                    "price": m.price,
                                    "target_outcome": str(target_outcome),
                                    "agree": False,
                                    "confidence": gate_confidence,
                                    "liquidity_risk": "HIGH",
                                    "opinion": gate.reason
                                },
                                outcome="unknown"
                            )
                        except Exception as ep_err:
                            logger.error(f"[SHADOW WhaleGate] Ошибка сохранения эпизода: {ep_err}")
                    else:
                        opinion_shadow = self.shadow.analyze_idea(context, combined_opinion, price_history=price_hist)
                save_checkpoint(f"shadow_{m.id}", status="ok")
            except LLMUnavailableError:
                save_checkpoint(f"shadow_{m.id}", status="llm_unavailable")
                raise
            except Exception as e:
                save_checkpoint(f"shadow_{m.id}", status="error", error=str(e))
                opinion_shadow = None
            status_sh = "✅ Согласен" if (opinion_shadow and opinion_shadow.agree) else "❌ Против"
            _update_state(shadow_status=f"{status_sh} (Увер: {opinion_shadow.confidence if opinion_shadow else 0})")
            
            if opinion_shadow:
                add_discussion_message(m.id, opinion_shadow.agent_name, opinion_shadow.opinion, opinion_shadow.confidence, opinion_shadow.agree)
        return opinion_shadow

    def _process_single_market(self, m: Market, i: int, summary_callback, _update_state, log, **kwargs):
        from core.guards import LLMUnavailableError
        try:
            _update_state(
                current_market_index=i, current_market_title=m.title, current_market_url=m.url,
                scout_status="⏳ Ожидает", swing_status="⏳ Ожидает", shadow_status="⏳ Ожидает"
            )
            with self._markets_lock:
                self.active_markets[m.id] = m.title
            
            last_price = get_last_analyzed_price(m.id)
            market_id = kwargs.get("market_id")
            if last_price is not None and not market_id:
                if abs(last_price - m.price) >= 0.03: log(f"\n[РЫНОК]: {m.title} (Цена: {last_price} -> {m.price})")
                else: log(f"\n[РЫНОК]: {m.title} (Кулдаун истек)")
            else:
                log(f"\n[РЫНОК]: {m.title} (Новый/Точечный)")
                
            save_price_point(m.id, m.price)
            
            # Параллельный парсинг и оценка
            trigger_type = kwargs.get("trigger_type", "scheduled")
            source_url = kwargs.get("source_url")
            source_text = kwargs.get("source_text")
            triggered_at = kwargs.get("triggered_at")
            
            from agents.shared.python.db import get_price_history
            price_hist = get_price_history(m.id, hours=24)
            
            # Получаем базовый ордербук для YES (по умолчанию) ДО запуска агентов
            pre_orderbook = self._fetch_pre_orderbook(m)

            import asyncio
            loop = None
            should_close_loop = False
            try:
                loop = kwargs.get("loop")
                if not loop:
                    loop = asyncio.new_event_loop()
                    should_close_loop = True
                else:
                    should_close_loop = False

                signal, swing_signal, context = loop.run_until_complete(run_agent_evaluation(
                    m, self.scout, self.swing, _update_state,
                    adapter=self.adapter, trigger_type=trigger_type,
                    source_url=source_url, source_text=source_text,
                    triggered_at=triggered_at, price_history=price_hist,
                    pre_orderbook=pre_orderbook,
                    scan_category=kwargs.get("category"),
                    run_id=kwargs.get("run_id")
                ))
            finally:
                if should_close_loop and loop and not loop.is_closed():
                    loop.close()

            if context is None:
                logger.info(f"  Рынок {m.id} пропущен (дедупликация)")
                return

            active_signal = signal or swing_signal
            opinion_shadow = self._run_shadow_analysis(
                m=m,
                active_signal=active_signal,
                signal=signal,
                swing_signal=swing_signal,
                context=context,
                price_hist=price_hist,
                _update_state=_update_state,
                log=log
            )
            
            if active_signal:
                process_consensus(context, signal, swing_signal, opinion_shadow, self.state, _update_state, summary_callback, api_key=self.api_key)
            else:
                log(f"  Нет сигнала для {m.id}, пропускаем консенсус.")
                _update_state(scout_status="⚪️ Нет сигнала", swing_status="⚪️ Нет сигнала")
            
            mark_market_analyzed(m.id, m.price)
            
        except LLMUnavailableError as e:
            agent_name = getattr(e, "agent_name", "UNKNOWN")
            log(f"🔴 LLM API недоступен для агента {agent_name}. Сканирование прервано.")
            if summary_callback:
                try:
                    reply_markup = {
                        "inline_keyboard": [
                            [{"text": f"🔄 Сменить модель для {agent_name}", "callback_data": f"set_model_{agent_name}"}]
                        ]
                    }
                    text = f"🔴 <b>LLM недоступна у агента {agent_name}</b>. Сканирование остановлено. Попробуйте позже."
                    if _callback_accepts_reply_markup(summary_callback):
                        summary_callback(text, reply_markup=reply_markup)
                    else:
                        summary_callback(text)
                except Exception as cb_err:
                    logger.error(f"summary_callback error: {cb_err}")
            raise e
        except Exception as e:
            error_msg = f"[ОШИБКА] Рынок {m.title}: {e}\n<pre>{html.escape(traceback.format_exc())}</pre>"
            log(f"[ОШИБКА] Рынок {m.title}: {e}\n{traceback.format_exc()}")
            if summary_callback:
                try:
                    summary_callback(error_msg)
                except Exception as cb_err:
                    logger.error(f"summary_callback error: {cb_err}")
        finally:
            with self._markets_lock:
                if m.id in self.active_markets:
                    del self.active_markets[m.id]

    async def analyze_post_async(
        self, post_id: int, chat_id: str,
        source_username: str | None = None,
        source_message_id: int | None = None,
        source_url: str | None = None,
        source_text: str | None = None
    ):
        """
        Анализ поста Telegram. Защищён от дублирования через статус PROCESSING.
        (NewsProcessor был удален, функция сохранена для совместимости)
        """
        from agents.shared.python.db import (
            get_telegram_post_info, mark_telegram_post_status
        )
        from services.notifications import send_telegram_to_chat

        post_info = await asyncio.to_thread(get_telegram_post_info, post_id)
        if not post_info:
            logger.error(f"Post {post_id} not found in DB.")
            return

        # ── Дедупликация: пропускаем посты, которые уже обрабатываются или обработаны ──
        current_status = post_info.get('status', 'NEW')
        if current_status in ('PROCESSING', 'ANALYZED'):
            logger.info(f"Post {post_id} already in status '{current_status}', skipping duplicate run.")
            return

        # Сразу помечаем как «в обработке», чтобы следующий дубль-вызов был отклонён
        await asyncio.to_thread(mark_telegram_post_status, post_id, 'PROCESSING')

        text = post_info.get('text', '')
        if not text:
            logger.error(f"Post {post_id} text is empty.")
            await asyncio.to_thread(mark_telegram_post_status, post_id, 'ANALYZED')
            return

        logger.info(f"Post {post_id}: NewsProcessor removed, skipping legacy news analysis.")
        await asyncio.to_thread(mark_telegram_post_status, post_id, 'NO_MARKETS')

    async def _send_fast_signal(
        self, m: Market, source_url: Optional[str], source_text: Optional[str], chat_id: Any
    ) -> None:
        """
        Строит и отправляет быстрое сообщение (fallback) при недоступности LLM.
        """
        from services.notifications import send_telegram_to_chat
        price_yes = int(m.price * 100)
        price_no = 100 - price_yes
        
        fast_msg = (
            "⚡️ <b>Быстрый сигнал (Gemini лимиты 429/403):</b>\n"
            f"<a href='{m.url}'>{m.title}</a>\n"
            f"🟢 YES: {price_yes}¢ | 🔴 NO: {price_no}¢\n"
            f"📅 Закрытие: {m.close_time.strftime('%Y-%m-%d %H:%M') if m.close_time else 'Unknown'}\n"
        )
        if source_url:
            fast_msg += f"📡 <b>Триггер:</b> <a href='{source_url}'>{source_text or 'Пост'}</a>\n"
        if m.volume is not None:
            try:
                fast_msg += f"📊 <b>Объем:</b> ${float(m.volume):,.0f}\n"
            except (TypeError, ValueError):
                pass
            
        fast_msg += "\n⚠️ <i>Глубокий анализ агентов пропущен из-за превышения лимитов API Gemini (429/403).</i>"
        
        mid = m.id[:40]
        market_action_markup = {
            "inline_keyboard": [[
                {"text": "🚫 Игнорировать", "callback_data": f"ignore_mkt_{mid}"},
                {"text": "🔍 Проанализировать", "callback_data": f"analyze_mkt_{mid}"},
                {"text": "📥 В идеи", "callback_data": f"add_idea_{mid}"}
            ]]
        }
        await asyncio.to_thread(send_telegram_to_chat, fast_msg, chat_id, reply_markup=market_action_markup)
