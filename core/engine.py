import threading
import logging
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from config import GOOGLE_API_KEY, SCAN_LIMIT_DEFAULT
from agents.shared.adapters.polymarket import PolymarketAdapter
from agents.shared.python.db import (
    init_db, cleanup_stale_signals, save_market, get_last_analyzed_price,
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

_markup_cache = {}

def _callback_accepts_reply_markup(func) -> bool:
    """Кэширует результат проверки наличия reply_markup в сигнатуре колбэка."""
    try:
        if func in _markup_cache:
            return _markup_cache[func]
    except TypeError:
        # func нехэшируем (например, functools.partial). Обходим кэш.
        pass

    try:
        sig = inspect.signature(func)
        res = "reply_markup" in sig.parameters
        try:
            _markup_cache[func] = res
        except TypeError:
            pass
        return res
    except (ValueError, TypeError):
        return False

class NoMarketsFoundError(Exception):
    """Исключение, выбрасываемое когда активные рынки по фильтрам не найдены."""
    pass

class CoreEngine:
    _instance = None
    _lock = threading.Lock()
    _scan_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(CoreEngine, cls).__new__(cls)
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
            return 0

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
        _update_state(total_markets=len(markets), stage="Обсуждение (SCOUT + SWING + SHADOW)")
        
        for i, m in enumerate(markets, 1):
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
                
                signal, swing_signal, context = run_agent_evaluation(
                    m, self.scout, self.swing, _update_state,
                    adapter=self.adapter,
                    trigger_type=trigger_type,
                    source_url=source_url,
                    source_text=source_text,
                    triggered_at=triggered_at,
                    price_history=price_hist
                )
                
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
                    
                    orderbook = None
                    target_outcome = getattr(active_signal, 'target_outcome', 'YES')
                    if m.tokens:
                        try:
                            token_idx = 1 if target_outcome.upper() == 'NO' and len(m.tokens) > 1 else 0
                            orderbook = self.adapter.get_orderbook(m.tokens[token_idx])
                        except Exception as e: logger.error(f"Failed to fetch orderbook: {e}")
                    
                    
                    
                    log("  SHADOW проверяет...")
                    from services.onchain_provider import get_recent_trades, get_top_positions
                    from core.smart_money import analyze_smart_money

                    onchain_trades = get_recent_trades(m.condition_id) if m.condition_id else []
                    onchain_positions = get_top_positions(m.condition_id) if m.condition_id else []
                    smart_money = analyze_smart_money(onchain_trades, onchain_positions)
                    
                    # Добавляем smart_money в контекст
                    context.smart_money = smart_money
                    
                    try:
                        scout_opinion = getattr(signal, 'details', '') or getattr(signal, 'signal_cause', '') if signal else ""
                        swing_opinion = getattr(swing_signal, 'details', '') or getattr(swing_signal, 'catalyst', '') if swing_signal else ""
                        combined_opinion = "\n\n".join(filter(None, [scout_opinion, swing_opinion]))
                        opinion_shadow = self.shadow.analyze_idea(context, combined_opinion, orderbook=orderbook, price_history=price_hist)
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
                break
            except Exception as e:
                error_msg = f"[ОШИБКА] Рынок {m.title}: {e}\n<pre>{html.escape(traceback.format_exc())}</pre>"
                log(f"[ОШИБКА] Рынок {m.title}: {e}\n{traceback.format_exc()}")
                if summary_callback:
                    try:
                        summary_callback(error_msg)
                    except Exception as e:
                        logger.error(f"summary_callback error: {e}")
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
                logger.info(f"Post {post_id}: No relevant markets found.")
                mark_telegram_post_status(post_id, 'NO_MARKETS')
                # Уведомляем пользователя, чтобы не было «молчаливого» пропуска
                try:
                    source_hint = ""
                    if source_url:
                        source_hint = f"\n📡 Пост: <a href='{source_url}'>{source_text or 'Источник'}</a>"
                    send_telegram_to_chat(
                        f"⚪️ Не найдено подходящих рынков на Polymarket для этой новости.{source_hint}",
                        chat_id
                    )
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
                except Exception as e:
                    from core.guards import LLMUnavailableError
                    # Проверяем, является ли это ошибкой недоступности LLM
                    is_llm_err = isinstance(e, LLMUnavailableError)
                    if not is_llm_err and hasattr(e, '__cause__'):
                        is_llm_err = isinstance(e.__cause__, LLMUnavailableError)
                        
                    if is_llm_err:
                        logger.warning(f"Gemini API limits hit (429/403) for market {m.id}. Sending fast basic signal fallback.")
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
                        if m.volume:
                            fast_msg += f"📊 <b>Объем:</b> ${m.volume:,.0f}\n"
                            
                        fast_msg += "\n⚠️ <i>Глубокий анализ агентов пропущен из-за превышения лимитов API Gemini (429/403).</i>"
                        
                        mid = m.id[:40]
                        market_action_markup = {
                            "inline_keyboard": [[
                                {"text": "🚫 Игнорировать", "callback_data": f"ignore_mkt_{mid}"},
                                {"text": "👁 Следить", "callback_data": f"watch_mkt_{mid}"}
                            ]]
                        }
                        await asyncio.to_thread(send_telegram_to_chat, fast_msg, chat_id, reply_markup=market_action_markup)
                    else:
                        logger.error(f"analyze_post_async error for {m.id}: {e}")
                finally:
                    # Небольшая пауза между отчетами, чтобы сообщения шли по порядку
                    await asyncio.sleep(2)

            mark_telegram_post_status(post_id, 'ANALYZED')

        except Exception as e:
            logger.error(f"analyze_post_async fatal error for post {post_id}: {e}")
            # Сбрасываем статус, чтобы можно было перезапустить при желании
            mark_telegram_post_status(post_id, 'ERROR')
