import threading
import logging
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from config import GOOGLE_API_KEY, SCAN_LIMIT_DEFAULT
from agents.shared.adapters.polymarket import PolymarketAdapter
from agents.shared.python.db import (
    init_db, save_market, get_last_analyzed_price,
    save_price_point, add_discussion_message, mark_market_analyzed
)
from core.models import Market
from agents.polymarket_mispricing_agent.src.agent import ScoutAgent
from agents.polymarket_swing_agent.src.agent import SwingAgent
from agents.polymarket_insider_agent.src.agent import ShadowAgent
from agents.orchestrator.src.agent import NexusAgent
from agents.shared.python.market_selector import MarketSelector
from core.workflow import run_screening, run_agent_evaluation, process_consensus
from services.notifications import send_telegram as send_telegram_alert

logger = logging.getLogger("CoreEngine")

import inspect
import traceback
import html

from core.guards import LLMUnavailableError

_markup_cache: dict = {}
_markup_cache_lock = threading.Lock()

def _callback_accepts_reply_markup(func) -> bool:
    """Кэширует результат проверки наличия reply_markup в сигнатуре колбэка."""
    try:
        with _markup_cache_lock:
            if func in _markup_cache:
                return _markup_cache[func]
    except TypeError:
        pass  # нехэшируемая функция — идём дальше без кэша

    try:
        sig = inspect.signature(func)
        res = "reply_markup" in sig.parameters
    except (ValueError, TypeError):
        return False

    try:
        with _markup_cache_lock:
            _markup_cache.setdefault(func, res)  # атомарная запись только если нет
    except TypeError:
        pass
    return res

class NoMarketsFoundError(Exception):
    """Исключение, выбрасываемое когда активные рынки по фильтрам не найдены."""
    pass

async def _run_math_gate(
    markets: list[Market],
    api_key: str,
    notify_fn,          # функция отправки сигнала в Telegram
    min_spread_pct: float = 5.0,
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

    # 2. Кросс-платформенные пары
    markets_poly = [m for m in markets if m.platform == "polymarket"]
    markets_kalshi = [m for m in markets if m.platform == "kalshi"]
    
    if markets_poly and markets_kalshi:
        cross_platform_pairs = get_matched_pairs(markets_poly, markets_kalshi)
        logger.info(f"[math_gate] Найдено кросс-платформенных кандидатов: {len(cross_platform_pairs)}")
        for market_a, market_b in cross_platform_pairs:
            if market_a.id in processed_ids or market_b.id in processed_ids:
                continue

            # Минуя _quick_pair_check (пары уже матчнуты), передаем с порогом 3.0
            mf = math_pre_filter(market_a, market_b, min_spread_pct=3.0)
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

    return processed_ids


class CoreEngine:
    _instance = None
    _lock = threading.Lock()
    _scan_lock = threading.Lock()

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
                    
                self.scout = ScoutAgent(api_key=self.api_key)
                self.swing = SwingAgent(api_key=self.api_key)
                self.shadow = ShadowAgent(api_key=self.api_key)
                self.nexus = NexusAgent(api_key=self.api_key)
                self.adapter = PolymarketAdapter()
                init_db()
                self.initialized = True
            except Exception:
                CoreEngine._instance = None  # позволяет пересоздать после исправления конфига
                raise

    def update_state(self, **kwargs):
        self.state.update(kwargs)

    def get_status(self) -> Dict[str, Any]:
        return self.state

    def get_active_markets(self) -> Dict[str, Any]:
        with self._markets_lock:
            return self.active_markets.copy()

    def run_team_discussion(self, log_callback=None, summary_callback=None, category=None, market_id=None, state_callback=None, **kwargs):
        if market_id is None:
            if not self._scan_lock.acquire(blocking=False):
                logger.warning("Сканирование уже выполняется (другой поток). Пропускаем.")
                raise RuntimeError("Сканирование уже выполняется в фоновом режиме (возможно, по расписанию). Пожалуйста, подождите завершения текущего цикла.")
            try:
                return self._run_team_discussion_inner(log_callback, summary_callback, category, market_id, state_callback, **kwargs)
            finally:
                self._scan_lock.release()
        else:
            # Для точечного анализа (из Telegram) пропускаем глобальный лок сканирования
            return self._run_team_discussion_inner(log_callback, summary_callback, category, market_id, state_callback, **kwargs)

    def _run_team_discussion_inner(self, log_callback=None, summary_callback=None, category=None, market_id=None, state_callback=None, **kwargs):
        from core.guards import LLMUnavailableError
        from core.checkpoint import save_checkpoint
        
        if summary_callback is None:
            summary_callback = send_telegram_alert

        def log(msg):
            logger.info(msg)
            if log_callback:
                try: log_callback(msg)
                except Exception as e: logger.error(f"log_callback error: {e}")

        # cleanup_stale_signals() удалено, перенесено в еженедельный cron и /cleanup

        from agents.shared.python.db import get_memory
        scan_limit_raw = get_memory("scan_limit")
        try:
            scan_limit = int(scan_limit_raw) if scan_limit_raw is not None else SCAN_LIMIT_DEFAULT
        except (TypeError, ValueError):
            logger.warning(f"Invalid scan_limit in memory: {scan_limit_raw!r}, using default")
            scan_limit = SCAN_LIMIT_DEFAULT
        if scan_limit <= 0:
            logger.warning(f"scan_limit={scan_limit} <= 0, using default {SCAN_LIMIT_DEFAULT}")
            scan_limit = SCAN_LIMIT_DEFAULT

        def _update_state(**kwargs):
            self.update_state(**kwargs)
            if state_callback:
                try: state_callback(self.state)
                except Exception as e: logger.error(f"state_callback error: {e}")

        _update_state(category=category or "Авто-микс", stage="Скрининг рынков", total_markets=0, ideas_found=0)

        # 1. Скрининг
        try:
            screened_market_ids = run_screening(self.adapter, self.nexus, category, market_id, summary_callback)
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
            markets = selector._filter(raw_markets)[:scan_limit]
        else:
            selector = MarketSelector(self.adapter)
            markets = selector.select(total_limit=scan_limit, category=category)
            if not category:
                log(f"  Категория ротации: {selector.get_auto_category()}")
                
        log(f"  Отобрано рынков: {len(markets)}")
        if not markets:
            msg = "Рынки по заданным фильтрам не найдены (возможно, в этой категории сейчас нет активных подходящих рынков)."
            log(f"⚠️ {msg}")
            _update_state(stage="Завершено (Рынков не найдено)")
            raise NoMarketsFoundError(msg)
            
        for m in markets: save_market(m)

        # 3. Обсуждение
        log(f"\n--- 2. Обсуждение идей (SCOUT + SWING + SHADOW) ---")
        
        # Run Math Gate
        processed_ids = []
        try:
            loop = asyncio.new_event_loop()
            try:
                processed_ids = loop.run_until_complete(_run_math_gate(
                    markets=markets,
                    api_key=self.api_key,
                    notify_fn=summary_callback,
                    min_spread_pct=5.0
                ))
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"Error running math gate: {e}", exc_info=True)
            processed_ids = []

        remaining_markets = [m for m in markets if m.id not in processed_ids]
        _update_state(total_markets=len(remaining_markets), stage="Обсуждение (SCOUT + SWING + SHADOW)")
        
        for i, m in enumerate(remaining_markets, 1):

            try:
                _update_state(
                    current_market_index=i, current_market_title=m.title, current_market_url=m.url,
                    scout_status="⏳ Ожидает", swing_status="⏳ Ожидает", shadow_status="⏳ Ожидает"
                )
                with self._markets_lock:
                    self.active_markets[m.id] = m.title
                
                last_price = get_last_analyzed_price(m.id)
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
                pre_orderbook = None
                if m.tokens:
                    try:
                        ob_raw = self.adapter.get_orderbook(m.tokens[0])
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

                signal, swing_signal, context = run_agent_evaluation(
                    m, self.scout, self.swing, _update_state,
                    adapter=self.adapter,
                    trigger_type=trigger_type,
                    source_url=source_url,
                    source_text=source_text,
                    triggered_at=triggered_at,
                    price_history=price_hist,
                    pre_orderbook=pre_orderbook
                )

                if context is None:
                    logger.info(f"  Рынок {m.id} пропущен (дедупликация)")
                    continue

                active_signal = signal or swing_signal
                opinion_shadow = None
                if active_signal:
                    if signal:
                        log(f"  SCOUT: Edge: {signal.edge:.2f}")
                        _update_state(scout_status=f"🟢 Edge ({signal.edge:.2f})")
                    else: _update_state(scout_status="⚪️ Нет фундамента")
                        
                    if swing_signal:
                        log(f"  SWING: Хайп найден!")
                        _update_state(swing_status=f"🚀 Ждет памп")
                    else: _update_state(swing_status="⚪️ Нет хайпа")
                        
                    _update_state(shadow_status="🔄 Проверяет ордербук...")
                    
                    target_outcome = getattr(active_signal, 'target_outcome', 'YES')
                    # Если выбран исход NO и у нас есть соответствующий токен (tokens[1]),
                    # дозагружаем ордербук для NO, чтобы скорректировать контекст для SHADOW.
                    # В противном случае context.orderbook уже содержит YES-стакан (pre_orderbook).
                    if target_outcome.upper() == 'NO' and len(m.tokens) > 1:
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
                    ingest_trades(m.id, onchain_trades, onchain_positions)
                    
                    from core.onchain_scorer import compute_onchain_score
                    target = getattr(active_signal, 'target_outcome', 'YES')
                    oc_score = compute_onchain_score(smart_money, target_outcome=target)
                    
                    # Корректируем edge детерминированно (без LLM)
                    if active_signal and signal and oc_score.confidence > 0.3:
                        if oc_score.direction == "CONFIRM":
                            signal.edge = min(signal.edge * (1 + oc_score.score * 0.2), 0.95)
                        elif oc_score.direction == "CONTRA":
                            signal.edge = signal.edge * (1 - abs(oc_score.score) * 0.3)
                    
                    # В контекст уходит только аннотация — 1 строка, ~10 токенов
                    context.onchain_annotation = oc_score.annotation
                    
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
                        else:
                            from core.whale_gate import check_whale_gate
                            gate = check_whale_gate(oc_score)
                            if not gate.allow:
                                from core.models import AgentOpinion
                                opinion_shadow = AgentOpinion(
                                    agent_name="SHADOW",
                                    market_id=m.id,
                                    opinion=gate.reason,
                                    confidence=0.9,
                                    agree=False,
                                    orderbook_facts="Whale Gate active",
                                    risk_assessment="Крупные кошельки торгуют против идеи",
                                    shadow_verdict=gate.reason,
                                    liquidity_risk="HIGH"
                                )
                                logger.info(f"  [WHALE GATE] {gate.reason}")
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
                
                if active_signal:
                    process_consensus(context, signal, swing_signal, opinion_shadow, self.state, _update_state, summary_callback)
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
                
        _update_state(stage="Завершено")
        log("\n✅ Обсуждение завершено.")
        return len(markets)

    async def analyze_post_async(
        self, post_id: int, chat_id: str,
        source_chat_id: str = "", source_username: str | None = None,
        source_message_id: int | None = None,
        source_url: str | None = None,
        source_text: str | None = None
    ):
        """
        Анализ поста Telegram. Защищён от дублирования через статус PROCESSING.
        """
        from agents.shared.python.db import (
            get_telegram_post_info, mark_telegram_post_status
        )
        from agents.orchestrator.src.news_processor import NewsProcessor
        from services.notifications import send_telegram_to_chat

        post_info = get_telegram_post_info(post_id)
        if not post_info:
            logger.error(f"Post {post_id} not found in DB.")
            return

        # ── Дедупликация: пропускаем посты, которые уже обрабатываются или обработаны ──
        current_status = post_info.get('status', 'NEW')
        if current_status in ('PROCESSING', 'ANALYZED'):
            logger.info(f"Post {post_id} already in status '{current_status}', skipping duplicate run.")
            return

        # Сразу помечаем как «в обработке», чтобы следующий дубль-вызов был отклонён
        mark_telegram_post_status(post_id, 'PROCESSING')

        text = post_info.get('text', '')
        message_id = post_info.get('message_id')

        if not text:
            logger.error(f"Post {post_id} text is empty.")
            mark_telegram_post_status(post_id, 'ANALYZED')
            return

        try:
            np = NewsProcessor(api_key=self.api_key)
            markets = np.find_relevant_markets(text)

            if not markets:
                logger.info(f"Post {post_id}: No relevant markets found. Reason: {np.failure_reason}")
                mark_telegram_post_status(post_id, 'NO_MARKETS')
                try:
                    source_hint = ""
                    if source_url:
                        source_hint = f"\n📡 Пост: <a href='{source_url}'>{source_text or 'Источник'}</a>"
                    
                    if np.failure_reason == "MARKET_CLOSED" and np.closed_markets:
                        m_closed = np.closed_markets[0]
                        close_date = m_closed.close_time.strftime("%d %b") if m_closed.close_time else "недавно"
                        msg = f"⚠️ Рынок <b><a href='{m_closed.url}'>{m_closed.title}</a></b> найден, но уже закрылся {close_date}. Анализ невозможен.{source_hint}"
                    elif np.failure_reason == "IRRELEVANT":
                        msg = f"⚪️ Тема новости оценена как нерелевантная для торговли рынками предсказаний.{source_hint}"
                    else:
                        msg = f"⚪️ Не найдено подходящих рынков на Polymarket для этой новости.{source_hint}"
                        
                    send_telegram_to_chat(msg, chat_id)
                except Exception as e:
                    logger.error(f"Failed to send NO_MARKETS notification: {e}")
                return

            logger.info(f"Post {post_id}: Found {len(markets)} markets, starting analysis.")

            effective_message_id = source_message_id or message_id
            if not source_url and effective_message_id:
                if source_username:
                    clean_username = source_username.lstrip('@')
                    source_url = f"https://t.me/{clean_username}/{effective_message_id}"
                else:
                    db_chat_id = post_info.get('chat_id')
                    if db_chat_id:
                        clean_id = str(db_chat_id).replace('-100', '')
                        source_url = f"https://t.me/c/{clean_id}/{effective_message_id}"

            def _notify(msg: str, reply_markup: dict = None) -> None:
                send_telegram_to_chat(msg, chat_id, reply_markup=reply_markup)

            if len(markets) > 3:
                logger.info(
                    f"Post {post_id}: найдено {len(markets)} рынков, "
                    f"анализируем первые 3 (остальные пропущены)."
                )

            for m in markets[:3]:
                try:
                    await asyncio.to_thread(
                        self.run_team_discussion,
                        None,
                        _notify,
                        None,
                        m.id,
                        None,
                        trigger_type="event_driven",
                        source_url=source_url,
                        source_text=source_text,
                        triggered_at=datetime.now(timezone.utc)
                    )
                except NoMarketsFoundError as e:
                    await asyncio.to_thread(send_telegram_to_chat, f"⚠️ {e}", chat_id)
                except RuntimeError as e:
                    await asyncio.to_thread(send_telegram_to_chat, f"⚠️ {e}", chat_id)
                    break
                except LLMUnavailableError as e:
                    logger.warning(f"Gemini API limits hit для market {m.id}. Fast fallback.")
                    await self._send_fast_signal(m, source_url, source_text, chat_id)
                except Exception as e:
                    logger.error(f"analyze_post_async error for {m.id}: {e}")
                finally:
                    # Небольшая пауза между отчетами, чтобы сообщения шли по порядку
                    await asyncio.sleep(2)

            mark_telegram_post_status(post_id, 'ANALYZED')

        except Exception as e:
            logger.error(f"analyze_post_async fatal error for post {post_id}: {e}")
            # Сбрасываем статус, чтобы можно было перезапустить при желании
            mark_telegram_post_status(post_id, 'ERROR')

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
            f"⚡️ <b>Быстрый сигнал (Gemini лимиты 429/403):</b>\n"
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
                {"text": "👁 Следить", "callback_data": f"watch_mkt_{mid}"},
                {"text": "📥 В идеи", "callback_data": f"add_idea_{mid}"}
            ]]
        }
        await asyncio.to_thread(send_telegram_to_chat, fast_msg, chat_id, reply_markup=market_action_markup)
