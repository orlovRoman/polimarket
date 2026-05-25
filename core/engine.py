import threading
import logging
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime

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
            self.active_markets: Dict[str, Any] = {}
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
            self.initialized = True
            init_db()

    def update_state(self, **kwargs):
        self.state.update(kwargs)

    def get_status(self) -> Dict[str, Any]:
        return self.state

    def get_active_markets(self) -> Dict[str, Any]:
        return self.active_markets

    def run_team_discussion(self, log_callback=None, summary_callback=None, category=None, market_id=None, state_callback=None):
        if not self._scan_lock.acquire(blocking=False):
            logger.warning("Сканирование уже выполняется (другой поток). Пропускаем.")
            return 0
        try:
            return self._run_team_discussion_inner(log_callback, summary_callback, category, market_id, state_callback)
        finally:
            self._scan_lock.release()

    def _run_team_discussion_inner(self, log_callback=None, summary_callback=None, category=None, market_id=None, state_callback=None):
        if summary_callback is None:
            summary_callback = send_telegram_alert

        def log(msg):
            logger.info(msg)
            if log_callback:
                try: log_callback(msg)
                except Exception: pass

        cleanup_stale_signals()

        from agents.shared.python.db import get_memory
        scan_limit = get_memory("scan_limit")
        if scan_limit is None: scan_limit = SCAN_LIMIT_DEFAULT
        scan_limit = int(scan_limit)

        def _update_state(**kwargs):
            self.update_state(**kwargs)
            if state_callback:
                try: state_callback(self.state)
                except Exception: pass

        _update_state(category=category or "Авто-микс", stage="Скрининг рынков", total_markets=0, ideas_found=0)

        # 1. Скрининг
        screened_market_ids = run_screening(self.adapter, self.nexus, category, market_id, summary_callback)

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
                except Exception: continue
            selector = MarketSelector(self.adapter)
            markets = selector._filter(raw_markets)[:scan_limit]
        else:
            selector = MarketSelector(self.adapter)
            markets = selector.select(total_limit=scan_limit, category=category)
            if not category:
                log(f"  Категория ротации: {selector.get_auto_category()}")
                
        log(f"  Отобрано рынков: {len(markets)}")
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
                self.active_markets[m.id] = m.title
                
                last_price = get_last_analyzed_price(m.id)
                if last_price is not None and not market_id:
                    if abs(last_price - m.price) >= 0.03: log(f"\n[РЫНОК]: {m.title} (Цена: {last_price} -> {m.price})")
                    else: log(f"\n[РЫНОК]: {m.title} (Кулдаун истек)")
                else:
                    log(f"\n[РЫНОК]: {m.title} (Новый/Точечный)")
                    
                save_price_point(m.id, m.price)
                
                # Параллельный парсинг и оценка
                signal, swing_signal, context = run_agent_evaluation(m, self.scout, self.swing, _update_state)
                
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
                    if m.tokens:
                        try:
                            orderbook = self.adapter.get_orderbook(m.tokens[0])
                        except Exception: pass
                    
                    from agents.shared.python.db import get_price_history
                    price_hist = get_price_history(m.id, hours=24)
                    
                    log("  SHADOW проверяет...")
                    from services.onchain_provider import get_recent_trades, get_top_positions
                    from core.smart_money import analyze_smart_money

                    onchain_trades = get_recent_trades(m.condition_id) if m.condition_id else []
                    onchain_positions = get_top_positions(m.condition_id) if m.condition_id else []
                    smart_money = analyze_smart_money(onchain_trades, onchain_positions)
                    
                    # Добавляем smart_money в контекст
                    context.smart_money = smart_money

                    opinion_shadow = self.shadow.analyze_idea(context, active_signal.details, orderbook=orderbook, price_history=price_hist)
                    status_sh = "✅ Согласен" if (opinion_shadow and opinion_shadow.agree) else "❌ Против"
                    _update_state(shadow_status=f"{status_sh} (Увер: {opinion_shadow.confidence if opinion_shadow else 0})")
                    
                    if opinion_shadow:
                        add_discussion_message(m.id, opinion_shadow.agent_name, opinion_shadow.opinion, opinion_shadow.confidence, opinion_shadow.agree)
                
                process_consensus(m, signal, swing_signal, opinion_shadow, self.state, _update_state, summary_callback)
                mark_market_analyzed(m.id, m.price)
                
            except Exception as e:
                import traceback
                error_msg = f"[ОШИБКА] Рынок {m.title}: {e}\n<pre>{traceback.format_exc()}</pre>"
                log(f"[ОШИБКА] Рынок {m.title}: {e}\n{traceback.format_exc()}")
                if summary_callback:
                    try:
                        summary_callback(error_msg)
                    except:
                        pass
            finally:
                if m.id in self.active_markets:
                    del self.active_markets[m.id]
                
        _update_state(stage="Завершено")
        log("\n✅ Обсуждение завершено.")
        return len(markets)

    async def analyze_post_async(self, post_id: int, chat_id: str):
        """
        Анализ поста Telegram.
        """
        from agents.shared.python.db import get_telegram_post_text, mark_telegram_post_status
        from agents.orchestrator.src.news_processor import NewsProcessor
        from core.context import MarketContext
        from agents.shared.utils.web_search import build_search_query, fetch_wikipedia_context
        
        text = get_telegram_post_text(post_id)
        if not text:
            logger.error(f"Post {post_id} not found in DB.")
            return
        
        np = NewsProcessor(api_key=self.api_key)
        markets = np.find_relevant_markets(text)
        
        if not markets:
            send_telegram_alert("К сожалению, я не нашел связанных рынков на Polymarket для этого поста.", chat_id)
            mark_telegram_post_status(post_id, 'NO_MARKETS')
            return

        send_telegram_alert(f"Нашел {len(markets)} связанных рынков. Анализирую...", chat_id)
        
        def dummy_update(**kwargs):
            pass

        for m in markets:
            try:
                full_m = self.adapter.get_market(m.id)
                if not full_m: continue
                
                search_query = build_search_query(full_m.title)
                wiki_context = fetch_wikipedia_context(search_query)
                news_context = f"КОНТЕКСТ СООБЩЕНИЯ ИЗ TELEGRAM:\n{text}\n\n"
                
                context = MarketContext(
                    market=full_m,
                    news_titles=[news_context],
                    reddit_posts=[],
                    wiki_context=wiki_context
                )
                
                signal = self.scout.estimate_market(context)
                swing_signal = self.swing.estimate_market(context)
                active_signal = signal or swing_signal
                
                opinion_shadow = None
                if active_signal:
                    orderbook = None
                    if full_m.tokens:
                        try: orderbook = self.adapter.get_orderbook(full_m.tokens[0])
                        except: pass
                    
                    from services.onchain_provider import get_recent_trades, get_top_positions
                    from core.smart_money import analyze_smart_money
                    onchain_trades = get_recent_trades(full_m.condition_id) if full_m.condition_id else []
                    onchain_positions = get_top_positions(full_m.condition_id) if full_m.condition_id else []
                    smart_money = analyze_smart_money(onchain_trades, onchain_positions)
                    context.smart_money = smart_money

                    opinion_shadow = self.shadow.analyze_idea(context, active_signal.details, orderbook=orderbook)
                    
                summary_text = f"🗣 <b>Ответ на ваш пост (Рынок: {full_m.title}):</b>\n<a href='{full_m.url}'>{full_m.title}</a>\n\n"
                if active_signal:
                    summary_text += f"💡 <b>Идея:</b> {active_signal.details}\n"
                    if opinion_shadow:
                        summary_text += f"🕵️‍♂️ <b>SHADOW:</b> {opinion_shadow.opinion}\n"
                else:
                    summary_text += "К сожалению, интересного сигнала для входа не найдено (нет edge или хайпа)."
                    
                send_telegram_alert(summary_text, chat_id)
                mark_telegram_post_status(post_id, 'ANALYZED')
                
            except Exception as e:
                logger.error(f"Error processing market {m.id} for post {post_id}: {e}", exc_info=True)
