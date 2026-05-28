import os
import sys
import concurrent.futures
from datetime import datetime, timezone
from typing import Optional, Callable

from core.models import Market, Signal, SwingSignal, AgentOpinion, IdeaDecision
from core.context import MarketContext
from config import logger, SCREENING_INTERVAL_SEC, SCAN_LIMIT_DEFAULT, MIN_EDGE_DEFAULT
from agents.shared.adapters.polymarket import PolymarketAdapter
from agents.shared.python.db import (
    save_market, get_last_analyzed_price, mark_market_analyzed, 
    save_price_point, get_price_history, get_new_correlations, 
    mark_correlations_notified, add_discussion_message, save_signal,
    get_memory, save_memory, save_idea_audit, get_market_correlations
)
from agents.shared.python.market_selector import MarketSelector
from agents.shared.utils.web_search import (
    fetch_rss_news, fetch_reddit_news, build_search_query,
    fetch_google_trends, fetch_hackernews
)

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
    now = datetime.now(timezone.utc)
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

            if not prefiltered:
                return []

            screen_result = nexus.screen_markets(prefiltered, top_n=30)
            screened_market_ids = screen_result.get("top_candidates", [])
            correlations_count = len(screen_result.get("correlations", []))
            
            save_memory("screened_market_ids", screened_market_ids, category='cache', ttl=SCREENING_INTERVAL_SEC)
            save_memory("last_screen_time", now.isoformat(), category='cache', ttl=SCREENING_INTERVAL_SEC)
            
            logger.info(f"  NEXUS отобрал {len(screened_market_ids)} кандидатов, найдено {correlations_count} корреляций")
            
            if correlations_count > 0 and summary_callback:
                from services.notifications import send_correlation_alerts
                send_correlation_alerts(summary_callback)
                
            from core.checkpoint import save_checkpoint
            save_checkpoint("screening", status="ok", markets_found=len(screened_market_ids))
                
            return screened_market_ids
        except LLMUnavailableError as e:
            from core.checkpoint import save_checkpoint
            save_checkpoint("screening", status="llm_unavailable", error=str(e))
            raise
        except Exception as e:
            from core.checkpoint import save_checkpoint
            save_checkpoint("screening", status="error", error=str(e))
            logger.error(f"Ошибка скрининга: {e}", exc_info=True)
            return []



def run_agent_evaluation(m, scout, swing, update_state,
                         trigger_type="scheduled",
                         source_url=None,
                         source_text=None,
                         triggered_at=None):
    logger.info("  Скачиваем новости (RSS + Reddit + Wikipedia)...")
    
    search_query = build_search_query(m.title)
    logger.info(f"  Поисковый запрос: '{search_query}' (оригинал: '{m.title}')")
    
    from agents.shared.utils.web_search import fetch_wikipedia_context
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_rss = executor.submit(fetch_rss_news, search_query)
        future_reddit = executor.submit(fetch_reddit_news, search_query)
        future_wiki = executor.submit(fetch_wikipedia_context, search_query)
        future_trends = executor.submit(fetch_google_trends, search_query)
        future_hn = executor.submit(fetch_hackernews, search_query)
        
        news_titles = future_rss.result()
        reddit_posts = future_reddit.result()
        wiki_context = "\n".join(future_wiki.result())
        trends_data = future_trends.result()
        hn_posts = future_hn.result()

    context = MarketContext(
        market=m,
        news_titles=news_titles,
        reddit_posts=reddit_posts,
        wiki_context=wiki_context,
        trends_data=trends_data,
        hn_posts=hn_posts,
        trigger_type=trigger_type,
        source_url=source_url,
        source_text=source_text,
        triggered_at=triggered_at
    )

    # ── Вариант 2: обогащаем контекст корреляциями ──────────────────────────
    corr_list = get_market_correlations(m.id)
    if corr_list:
        lines = []
        for c in corr_list[:3]:
            conf_pct = int(float(c["confidence"]) * 100) if float(c["confidence"]) <= 1.0 \
                       else int(c["confidence"])
            peer_title = c["title_b"] if c["market_id_a"] == m.id else c["title_a"]
            lines.append(
                f"  • [{c['correlation_type'].upper()} {conf_pct}%] "
                f"«{peer_title}» — {c['description']}"
            )
        context.correlation_hint = (
            "🔗 СВЯЗАННЫЕ РЫНКИ (используй как anchor для калибровки вероятности):\n"
            + "\n".join(lines)
        )
        logger.info(f"  Correlation hint: {len(corr_list)} связей для {m.id}")
    # ────────────────────────────────────────────────────────────────────────


    logger.info("  SCOUT и SWING оценивают...")
    update_state(scout_status="🔄 Считает вероятности...", swing_status="🔄 Оценивает хайп...")

    from core.guards import LLMUnavailableError
    from core.checkpoint import save_checkpoint
    
    # SCOUT
    try:
        signal = scout.estimate_market(context)
        save_checkpoint(f"scout_{m.id}", status="ok", edge=signal.edge if signal else None)
    except LLMUnavailableError:
        save_checkpoint(f"scout_{m.id}", status="llm_unavailable")
        raise
    except Exception as e:
        save_checkpoint(f"scout_{m.id}", status="error", error=str(e))
        signal = None
        
    # SWING
    try:
        swing_signal = swing.estimate_market(context)
        save_checkpoint(f"swing_{m.id}", status="ok")
    except LLMUnavailableError:
        save_checkpoint(f"swing_{m.id}", status="llm_unavailable")
        raise
    except Exception as e:
        save_checkpoint(f"swing_{m.id}", status="error", error=str(e))
        swing_signal = None
        
    return signal, swing_signal, context

def make_consensus(context: MarketContext, signal: Optional[Signal], swing_signal: Optional[SwingSignal], opinion_shadow: Optional[AgentOpinion]) -> IdeaDecision:
    m = context.market
    # Решение принимает SHADOW через поле agree. Проверка liquidity_risk убрана,
    # т.к. пользователь оперирует микро-банком ($10-100) и high liquidity_risk — норма.
    shadow_ok = opinion_shadow and opinion_shadow.agree
    
    valid_scout = signal is not None
    valid_swing = swing_signal is not None and getattr(swing_signal, 'recommendation', '') == 'buy'
    
    if (valid_scout or valid_swing) and shadow_ok:
        status = 'saved'
    elif (valid_scout or valid_swing):
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

def process_consensus(context: MarketContext, signal: Optional[Signal], swing_signal: Optional[SwingSignal], opinion_shadow: Optional[AgentOpinion], state: dict, update_state: Callable, summary_callback: Optional[Callable]):
    m = context.market
    decision = make_consensus(context, signal, swing_signal, opinion_shadow)
    
    if decision.status == 'saved':
        logger.info("  !!! ИДЕЯ ПОДТВЕРЖДЕНА КОНСЕНСУСОМ.")
        if signal: save_signal(signal)
        if swing_signal and getattr(swing_signal, 'recommendation', '') == 'buy': save_signal(swing_signal)
        update_state(ideas_found=state.get("ideas_found", 0) + 1)
    elif decision.status == 'no_consensus':
        logger.info("  --- Консенсус не достигнут (SHADOW забраковал).")
    else:
        logger.info("  SCOUT и SWING: Идей не найдено.")
        update_state(scout_status="⚪️ Идея не найдена", swing_status="⚪️ Идея не найдена")

    if summary_callback:
        # Богатый формат для Telegram
        summary_text = f"🗣 <b>Обсуждение рынка:</b>\n"
        
        if context.trigger_type == "event_driven":
            if context.source_url:
                triggered_time = context.triggered_at.strftime("%d %b %H:%M") if context.triggered_at else "сейчас"
                source_label = context.source_text or "Источник"
                summary_text += f"📡 <b>Источник:</b> <a href='{context.source_url}'>{source_label}</a> · {triggered_time}\n"
            else:
                summary_text += f"⚠️ <b>Внимание:</b> источник события не определён (деградация до scheduled)\n"
                
        summary_text += f"<a href='{m.url}'>{m.title}</a>\n\n"
        
        if signal:
            summary_text += f"🧠 <b>SCOUT (Фундаментал):</b>\n"
            summary_text += f"🎯 Причина: {getattr(signal, 'signal_cause', 'N/A')}\n"
            summary_text += f"⚖️ Риск: {getattr(signal, 'signal_risk', 'N/A')}\n"
            if getattr(signal, 'oracle_risk', ''):
                summary_text += f"👁 Оракул-риск: {getattr(signal, 'oracle_risk', '')}\n"
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
            summary_text += f"💧 Риск ликвидности: {liq_risk}\n"
            summary_text += f"📊 Ордербук: {getattr(opinion_shadow, 'orderbook_facts', 'N/A')}\n"
            summary_text += f"⚖️ Исполнение: {getattr(opinion_shadow, 'risk_assessment', 'N/A')}\n"
            summary_text += f"📝 Вердикт: {getattr(opinion_shadow, 'shadow_verdict', 'N/A')}\n\n"
        
        if decision.status == 'saved':
            initiators = []
            if signal: initiators.append("SCOUT")
            if swing_signal and getattr(swing_signal, 'recommendation', '') == 'buy': initiators.append("SWING")
            initiators_str = " + ".join(initiators)
            summary_text += f"✨ <b>ИТОГ: Консенсус достигнут (Инициатор: {initiators_str})! Идея сохранена.</b>"
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
    
    from core.checkpoint import save_checkpoint, verify_checkpoint
    save_checkpoint(f"consensus_{m.id}", status="ok")
    saved_ok = verify_checkpoint(f"consensus_{m.id}")
    if not saved_ok:
        logger.warning(f"[CHECKPOINT] Консенсус для {m.id} не сохранён в аудит!")


    # Эпизодическая память агентов (Спринт 7)
    from agents.shared.python.db import save_agent_episode
    if signal:
        save_agent_episode(
            agent_name="SCOUT",
            event_type="signal_evaluated",
            summary=f"Opinion: {getattr(signal, 'signal_verdict', 'buy')} | Reason: {getattr(signal, 'signal_cause', getattr(signal, 'details', ''))}",
            market_id=m.id,
            market_title=m.title
        )
        
    if swing_signal:
        save_agent_episode(
            agent_name="SWING",
            event_type="signal_evaluated",
            summary=f"Opinion: {getattr(swing_signal, 'swing_verdict', 'buy')} | Reason: {getattr(swing_signal, 'catalyst', getattr(swing_signal, 'catalyst_absence_reason', ''))}",
            market_id=m.id,
            market_title=m.title
        )
        
    if opinion_shadow:
        save_agent_episode(
            agent_name="SHADOW",
            event_type="signal_evaluated",
            summary=f"Opinion: {getattr(opinion_shadow, 'shadow_verdict', 'agree')} | Reason: {getattr(opinion_shadow, 'risk_assessment', getattr(opinion_shadow, 'orderbook_facts', ''))}",
            market_id=m.id,
            market_title=m.title
        )
