import os
import sys
import concurrent.futures
from datetime import datetime
from typing import Optional, Callable

from core.models import Market, Signal, SwingSignal, AgentOpinion, IdeaDecision
from core.context import MarketContext
from config import logger, SCREENING_INTERVAL_SEC, SCAN_LIMIT_DEFAULT, MIN_EDGE_DEFAULT
from agents.shared.adapters.polymarket import PolymarketAdapter
from agents.shared.python.db import (
    save_market, get_last_analyzed_price, mark_market_analyzed, 
    save_price_point, get_price_history, get_new_correlations, 
    mark_correlations_notified, add_discussion_message, save_signal,
    get_memory, save_memory, save_idea_audit
)
from agents.shared.python.market_selector import MarketSelector
from agents.shared.utils.web_search import fetch_rss_news, fetch_reddit_news

from agents.polymarket_mispricing_agent.src.agent import ScoutAgent
from agents.polymarket_swing_agent.src.agent import SwingAgent
from agents.polymarket_insider_agent.src.agent import ShadowAgent
from agents.orchestrator.src.agent import NexusAgent

def _prefilter_markets(markets_compact: list) -> list:
    """
    Уровень 1 (без LLM): базовая фильтрация по объёму, цене и времени.
    Сокращает ~1000 рынков до ~100-150 перед передачей в NEXUS.
    """
    from config import PRICE_RANGE_MIN, PRICE_RANGE_MAX
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    min_close = now + timedelta(days=3)
    filtered = []

    for m in markets_compact:
        price = m.get('price', m.get('p', 0.5))
        volume = m.get('volume', m.get('vol', 0))
        close_str = m.get('close_time', m.get('end', ''))

        # Фильтр: цена в диапазоне интереса
        if not (PRICE_RANGE_MIN <= price <= PRICE_RANGE_MAX):
            continue
        # Фильтр: объём > $5000 (есть ликвидность)
        if volume and volume < 5000:
            continue
        # Фильтр: рынок закроется не раньше чем через 3 дня
        if close_str:
            try:
                close_dt = datetime.fromisoformat(str(close_str).replace('Z', '+00:00'))
                if close_dt < min_close:
                    continue
            except (ValueError, AttributeError):
                pass

        filtered.append(m)

    return filtered


def run_screening(adapter: PolymarketAdapter, nexus: NexusAgent, category: str, market_id: str, summary_callback=None) -> list:

    if category or market_id:
        return None
        
    last_screen_raw = get_memory("last_screen_time")
    now = datetime.utcnow()
    needs_screening = True
    
    if last_screen_raw:
        try:
            last_screen = datetime.fromisoformat(last_screen_raw)
            elapsed = (now - last_screen).total_seconds()
            if elapsed < SCREENING_INTERVAL_SEC:
                needs_screening = False
                logger.info(f"Скрининг не требуется (прошло {elapsed/60:.0f} мин из {SCREENING_INTERVAL_SEC/60:.0f})")
                return get_memory("screened_market_ids")
        except (ValueError, TypeError):
            pass
            
    if needs_screening:
        logger.info("--- 0. NEXUS скринирует все рынки ---")
        try:
            all_compact = adapter.list_all_markets_compact()
            logger.info(f"  Загружено {len(all_compact)} рынков для скрининга")

            prefiltered = _prefilter_markets(all_compact)
            logger.info(f"  Pre-filter: {len(all_compact)} → {len(prefiltered)} рынков перед NEXUS")

            screen_result = nexus.screen_markets(prefiltered, top_n=30)

            screened_market_ids = screen_result.get("top_candidates", [])
            correlations_count = len(screen_result.get("correlations", []))
            
            save_memory("screened_market_ids", screened_market_ids, category='cache', ttl=SCREENING_INTERVAL_SEC)
            save_memory("last_screen_time", now.isoformat(), category='cache', ttl=SCREENING_INTERVAL_SEC)
            
            logger.info(f"  NEXUS отобрал {len(screened_market_ids)} кандидатов, найдено {correlations_count} корреляций")
            
            if correlations_count > 0 and summary_callback:
                from services.notifications import send_correlation_alerts
                send_correlation_alerts(summary_callback)
                
            return screened_market_ids
        except Exception as e:
            logger.error(f"Ошибка скрининга: {e}")
    return None

def _build_search_query(market_title: str) -> str:
    """Строит короткий поисковый запрос из заголовка рынка."""
    import re
    # Убираем вопросительные слова и стоп-слова
    stopwords = ["will", "who", "what", "when", "is", "are", "does", "can",
                 "the", "a", "an", "in", "of", "to", "by", "for",
                 "будет", "ли", "что", "кто", "когда", "выиграет"]
    words = re.sub(r'[^\w\s]', '', market_title.lower()).split()
    keywords = [w for w in words if w not in stopwords and len(w) > 2]
    return " ".join(keywords[:6])

def run_agent_evaluation(m, scout, swing, update_state):
    logger.info("  Скачиваем новости (RSS + Reddit + Wikipedia)...")
    
    search_query = _build_search_query(m.title)
    logger.info(f"  Поисковый запрос: '{search_query}' (оригинал: '{m.title}')")
    
    from agents.shared.utils.web_search import fetch_wikipedia_context
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_rss = executor.submit(fetch_rss_news, search_query)
        future_reddit = executor.submit(fetch_reddit_news, search_query)
        future_wiki = executor.submit(fetch_wikipedia_context, search_query)
        
        news_titles = future_rss.result()
        reddit_posts = future_reddit.result()
        wiki_context = "\n".join(future_wiki.result())

    context = MarketContext(
        market=m,
        news_titles=news_titles,
        reddit_posts=reddit_posts,
        wiki_context=wiki_context
    )

    logger.info("  SCOUT и SWING оценивают...")
    update_state(scout_status="🔄 Считает вероятности...", swing_status="🔄 Оценивает хайп...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_scout = executor.submit(scout.estimate_market, context)
        future_swing = executor.submit(swing.estimate_market, context)
        signal = future_scout.result()
        swing_signal = future_swing.result()
        
    return signal, swing_signal, context

def make_consensus(m: Market, signal: Optional[Signal], swing_signal: Optional[SwingSignal], opinion_shadow: Optional[AgentOpinion]) -> IdeaDecision:
    shadow_ok = opinion_shadow and opinion_shadow.agree and getattr(opinion_shadow, 'liquidity_risk', 'medium') != "high"
    
    if (signal or swing_signal) and shadow_ok:
        status = 'saved'
    elif (signal or swing_signal):
        status = 'no_consensus'
    else:
        status = 'no_signal'
        
    return IdeaDecision(
        market_id=m.id,
        status=status,
        scout_signal=signal,
        swing_signal=swing_signal,
        shadow_opinion=opinion_shadow
    )

def process_consensus(m: Market, signal: Optional[Signal], swing_signal: Optional[SwingSignal], opinion_shadow: Optional[AgentOpinion], state: dict, update_state: Callable, summary_callback: Optional[Callable]):
    decision = make_consensus(m, signal, swing_signal, opinion_shadow)
    
    if decision.status == 'saved':
        logger.info("  !!! ИДЕЯ ПОДТВЕРЖДЕНА КОНСЕНСУСОМ.")
        if signal: save_signal(signal)
        if swing_signal: save_signal(swing_signal)
        update_state(ideas_found=state.get("ideas_found", 0) + 1)
    elif decision.status == 'no_consensus':
        logger.info("  --- Консенсус не достигнут (SHADOW забраковал).")
    else:
        logger.info("  SCOUT и SWING: Идей не найдено.")
        update_state(scout_status="⚪️ Идея не найдена", swing_status="⚪️ Идея не найдена")

    if summary_callback:
        # Богатый формат для Telegram
        summary_text = f"🗣 <b>Обсуждение рынка:</b>\n<a href='{m.url}'>{m.title}</a>\n\n"
        
        if signal:
            summary_text += f"🧠 <b>SCOUT (Фундаментал):</b>\n"
            summary_text += f"🎯 Причина: {getattr(signal, 'signal_cause', 'N/A')}\n"
            summary_text += f"⚖️ Риск: {getattr(signal, 'signal_risk', 'N/A')}\n"
            summary_text += f"📝 Вердикт: {getattr(signal, 'signal_verdict', 'N/A')}\n\n"
        else:
            summary_text += f"🧠 <b>SCOUT:</b> ⚪️ Расхождение < MIN_EDGE\n\n"
            
        if swing_signal:
            summary_text += f"🏄‍♂️ <b>SWING (Хайп):</b>\n"
            if getattr(swing_signal, 'recommendation', '') == 'buy':
                summary_text += f"🔥 Катализатор: {getattr(swing_signal, 'catalyst', 'N/A')}\n"
            else:
                summary_text += f"💤 Почему тихо: {getattr(swing_signal, 'catalyst_absence_reason', 'N/A')}\n"
            summary_text += f"⚖️ Риск: {getattr(swing_signal, 'swing_risk', 'N/A')}\n"
            summary_text += f"📝 Вердикт: {getattr(swing_signal, 'swing_verdict', 'N/A')}\n\n"
        else:
            summary_text += f"🏄‍♂️ <b>SWING:</b> ⚪️ Сигнал не сформирован\n\n"
            
        if opinion_shadow:
            status = "✅ СОГЛАСЕН" if opinion_shadow.agree else "❌ ПРОТИВ"
            liq_risk = getattr(opinion_shadow, 'liquidity_risk', 'medium').upper()
            summary_text += f"🛡 <b>SHADOW (Инфраструктура):</b> {status}\n"
            summary_text += f"💧 Ликвидность: {liq_risk}\n"
            summary_text += f"📊 Ордербук: {getattr(opinion_shadow, 'orderbook_facts', 'N/A')}\n"
            summary_text += f"⚖️ Исполнение: {getattr(opinion_shadow, 'risk_assessment', 'N/A')}\n"
            summary_text += f"📝 Вердикт: {getattr(opinion_shadow, 'shadow_verdict', 'N/A')}\n\n"
        
        if decision.status == 'saved':
            summary_text += "✨ <b>ИТОГ: Консенсус достигнут! Идея сохранена.</b>"
        elif decision.status == 'no_consensus':
            summary_text += "🛑 <b>ИТОГ: Консенсус не достигнут (SHADOW отклонил).</b>"
        else:
            summary_text += "🛑 <b>ИТОГ: Нет предмета для обсуждения.</b>"
            
        summary_callback(summary_text)
        
    audit = {
        "scout_edge": signal.edge if signal else None,
        "swing_found": 1 if swing_signal else 0,
        "shadow_agree": int(opinion_shadow.agree) if opinion_shadow else None,
        "shadow_confidence": opinion_shadow.confidence if opinion_shadow else None,
        "shadow_reason": (opinion_shadow.opinion or "")[:200] if opinion_shadow else "",
        "final_outcome": decision.status
    }
    save_idea_audit(m.id, m.title, audit)

    # Эпизодическая память агентов (Спринт 7)
    from agents.shared.python.db import save_agent_episode
    if signal:
        save_agent_episode(
            market_id=m.id,
            agent="SCOUT",
            opinion=getattr(signal, 'signal_verdict', 'buy'),
            reasoning=getattr(signal, 'signal_cause', getattr(signal, 'details', ''))
        )
        
    if swing_signal:
        save_agent_episode(
            market_id=m.id,
            agent="SWING",
            opinion=getattr(swing_signal, 'swing_verdict', 'buy'),
            reasoning=getattr(swing_signal, 'catalyst', getattr(swing_signal, 'catalyst_absence_reason', ''))
        )
        
    if opinion_shadow:
        save_agent_episode(
            market_id=m.id,
            agent="SHADOW",
            opinion=getattr(opinion_shadow, 'shadow_verdict', 'agree'),
            reasoning=getattr(opinion_shadow, 'risk_assessment', getattr(opinion_shadow, 'orderbook_facts', ''))
        )
