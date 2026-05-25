import os
import sys
import concurrent.futures
from datetime import datetime

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
            
            screen_result = nexus.screen_markets(all_compact, top_n=30)
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

def run_agent_evaluation(m, scout, swing, update_state):
    logger.info("  Скачиваем новости (RSS + Reddit)...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_rss = executor.submit(fetch_rss_news, m.title)
        future_reddit = executor.submit(fetch_reddit_news, m.title)
        news_titles = future_rss.result()
        reddit_posts = future_reddit.result()

    logger.info("  SCOUT и SWING оценивают...")
    update_state(scout_status="🔄 Считает вероятности...", swing_status="🔄 Оценивает хайп...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_scout = executor.submit(scout.estimate_market, m, news_titles, reddit_posts)
        future_swing = executor.submit(swing.estimate_market, m, news_titles, reddit_posts)
        signal = future_scout.result()
        swing_signal = future_swing.result()
        
    return signal, swing_signal

def process_consensus(m, signal, swing_signal, opinion_shadow, state, update_state, summary_callback):
    if signal or swing_signal:
        if opinion_shadow and opinion_shadow.agree and opinion_shadow.confidence > 0.6:
            logger.info("  !!! ИДЕЯ ПОДТВЕРЖДЕНА КОНСЕНСУСОМ.")
            if signal: save_signal(signal)
            if swing_signal: save_signal(swing_signal)
            update_state(ideas_found=state.get("ideas_found", 0) + 1)
        else:
            logger.info("  --- Консенсус не достигнут.")
    else:
        logger.info("  SCOUT и SWING: Идей не найдено.")
        update_state(scout_status="⚪️ Идея не найдена", swing_status="⚪️ Идея не найдена")
        
    if summary_callback:
        summary_text = f"🗣 <b>Обсуждение рынка:</b>\n<a href='{m.url}'>{m.title}</a>\n\n"
        if signal:
            summary_text += f"<b>SCOUT</b> 🟢 {signal.summary}\n\n"
        else:
            summary_text += f"<b>SCOUT</b> ⚪️ Идея не найдена.\n\n"
            
        if swing_signal:
            summary_text += f"<b>SWING</b> 🚀 Ждет памп\n\n"
        else:
            summary_text += f"<b>SWING</b> ⚪️ Нет хайпа.\n\n"
        
        if opinion_shadow:
            status = "✅ СОГЛАСЕН" if opinion_shadow.agree else "❌ ПРОТИВ"
            summary_text += f"<b>SHADOW</b> {status} (Увер: {opinion_shadow.confidence})\n<i>{opinion_shadow.opinion}</i>\n\n"
        
        if (signal or swing_signal) and opinion_shadow and opinion_shadow.agree and opinion_shadow.confidence > 0.6:
            summary_text += "✨ <b>ИТОГ: Консенсус достигнут! Идея сохранена.</b>"
        elif (signal or swing_signal):
            summary_text += "🛑 <b>ИТОГ: Консенсус не достигнут.</b>"
        else:
            summary_text += "🛑 <b>ИТОГ: Нет предмета для обсуждения.</b>"
            
        summary_callback(summary_text)
        
    audit = {
        "scout_edge": signal.edge if signal else None,
        "swing_found": 1 if swing_signal else 0,
        "shadow_agree": int(opinion_shadow.agree) if opinion_shadow else None,
        "shadow_confidence": opinion_shadow.confidence if opinion_shadow else None,
        "shadow_reason": (opinion_shadow.opinion or "")[:200] if opinion_shadow else "",
        "final_outcome": "saved" if (signal or swing_signal) and opinion_shadow and opinion_shadow.agree and opinion_shadow.confidence > 0.6 else ("no_consensus" if (signal or swing_signal) else "no_signal")
    }
    save_idea_audit(m.id, m.title, audit)
