import os
import sys
import time
import concurrent.futures
from datetime import datetime, timezone
from typing import Optional, Callable, Set, Dict

from core.models import Market, Signal, SwingSignal, AgentOpinion, IdeaDecision
from core.context import MarketContext


from config import logger, SCREENING_INTERVAL_SEC, SCAN_LIMIT_DEFAULT, MIN_EDGE_DEFAULT, MAX_SCREENING_MARKETS
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

_MAX_CORR_PEERS: int = 5   # топ-N корреляций для math_pre_filter
_SESSION_DEDUP_TTL_SEC: int = 1800
_analyzed_in_session: Dict[str, float] = {}

def _cleanup_session_dedup() -> None:
    """Удаляем устаревшие ключи из in-memory дедупликатора."""
    cutoff = time.monotonic() - _SESSION_DEDUP_TTL_SEC
    expired = [k for k, ts in _analyzed_in_session.items() if ts < cutoff]
    for k in expired:
        del _analyzed_in_session[k]

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
        logger.debug(
            f"[screening] Точечный режим "
            f"(category={category!r}, market_id={market_id!r}), скрининг пропущен"
        )
        return []
        
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
                cached = get_memory("screened_market_ids")
                if isinstance(cached, str):
                    import json
                    try:
                        cached = json.loads(cached)
                    except (json.JSONDecodeError, TypeError):
                        cached = []
                if not isinstance(cached, list):
                    logger.warning(f"[workflow] Unexpected type for screened_market_ids: {type(cached)}. Expected list.")
                return cached if isinstance(cached, list) else []
        except (ValueError, TypeError):
            pass
            
    if needs_screening:
        from core.guards import LLMUnavailableError
        logger.info("--- 0. NEXUS скринирует все рынки ---")
        try:
            all_compact = adapter.list_all_markets_compact()
            logger.info(f"  Загружено {len(all_compact)} рынков для скрининга")

            prefiltered = _prefilter_markets(all_compact)
            prefiltered = prefiltered[:MAX_SCREENING_MARKETS]
            logger.info(f"  Pre-filter: {len(all_compact)} → {len(prefiltered)} рынков перед NEXUS")

            if not prefiltered:
                return []

            from agents.shared.python.db import get_recently_analyzed_market_ids
            already_analyzed = get_recently_analyzed_market_ids(within_seconds=SCREENING_INTERVAL_SEC)
            
            from core.market_scorer import screen_markets_code
            from core.arb_scanner import find_complementary_pairs
            from agents.shared.python.db import save_correlation
            from core.models import MarketCorrelation

            # 1. Скоринг без LLM
            candidates = [m for m in prefiltered if m['id'] not in set(already_analyzed)]
            screened_market_ids = screen_markets_code(candidates, top_n=30)

            # 2. Корреляции без LLM
            #    Нужны полные объекты Market для math_pre_filter
            screened_markets_full = [
                adapter.get_market(mid) for mid in screened_market_ids
            ]
            screened_markets_full = [m for m in screened_markets_full if m is not None]

            arb_pairs = find_complementary_pairs(screened_markets_full, min_spread_pct=5.0)
            correlations_count = len(arb_pairs)

            for (ma, mb, mf) in arb_pairs:
                try:
                    save_correlation(MarketCorrelation(
                        market_id_a=ma.id,
                        market_id_b=mb.id,
                        title_a=ma.title,
                        title_b=mb.title,
                        correlation_type="arbitrage" if mf.has_arbitrage else "thematic",
                        description=mf.reasoning[:200],
                        confidence=min(mf.spread_pct / 20.0, 1.0),  # нормализуем спред → confidence
                    ))
                except Exception as e:
                    logger.error(f"[screening] save_correlation error: {e}")

            save_memory("screened_market_ids", screened_market_ids, category='cache', ttl=SCREENING_INTERVAL_SEC)
            save_memory("last_screen_time", now.isoformat(), category='cache', ttl=SCREENING_INTERVAL_SEC)
            
            logger.info(f"[screening] Код отобрал {len(screened_market_ids)} кандидатов, "
                        f"найдено {correlations_count} арб-пар")
            
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



def _safe_result(future: concurrent.futures.Future, default, timeout: int = 15):
    """Получает результат Future с таймаутом; возвращает default при любой ошибке."""
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        logger.info(f"[workflow] fetch timed out after {timeout}s, using default")
        return default
    except Exception as e:
        logger.warning(f"[workflow] fetch failed unexpectedly: {e}, using default")
        return default


def _fetch_grounded_context(market: Market, api_key: str, model: str) -> str:
    """
    Один LLM-вызов с google_search. Результат используется SCOUT и SWING.
    """
    try:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": (
                f"Search for the latest news and data about: '{market.title}'. "
                f"Return key findings as bullet points with source and date."
            )}]}],
            "tools": [{"google_search": {}}]
        }
        from agents.shared.utils.gemini_client import generate_content_with_fallback, extract_response_text
        result, _ = generate_content_with_fallback(
            api_key=api_key, payload=payload,
            default_model=model, agent_name="GROUNDING", market_id=market.id
        )
        text = extract_response_text(result) if result else ""
        return text or "Grounding: результатов не найдено."
    except Exception as e:
        return f"Grounding: ошибка ({e})"



def run_agent_evaluation(m: Market, scout, swing, update_state: Callable, adapter=None, trigger_type="scheduled", source_url=None, source_text=None, triggered_at=None, price_history=None, pre_orderbook=None):
    _cleanup_session_dedup()

    # In-session дедупликация (быстрая проверка без БД)
    dedup_key = f"{m.id}:{trigger_type}"
    if dedup_key in _analyzed_in_session:
        logger.info(
            f"[workflow] Пропуск дубля (in-session): {m.id} "
            f"({trigger_type}), добавлен {time.monotonic() - _analyzed_in_session[dedup_key]:.0f}с назад"
        )
        return None, None, None
    _analyzed_in_session[dedup_key] = time.monotonic()

    # БД-уровень дедупликации (межсессионная защита)
    last_analysis_key = f"last_analysis:{m.id}"
    last_raw = get_memory(last_analysis_key)
    if last_raw:
        try:
            last_dt = datetime.fromisoformat(last_raw)
            age_sec = (datetime.now(timezone.utc) - last_dt).total_seconds()
            if age_sec < _SESSION_DEDUP_TTL_SEC:
                logger.info(
                    f"[workflow] Дедупликация (БД): рынок {m.id} уже анализировался "
                    f"{age_sec:.0f}с назад (trigger={trigger_type}), пропускаем"
                )
                return None, None, None
        except (ValueError, TypeError):
            pass

    # Записываем ДО анализа — защищает от дублей даже при сбое LLM.
    # Цена: пропуск одного повтора за следующие 10 мин при падении анализа.
    save_memory(last_analysis_key, datetime.now(timezone.utc).isoformat(), category='cache', ttl=_SESSION_DEDUP_TTL_SEC)

    # Guard: проверяем доступность LLM перед запуском
    from config import llm_health_gate  # глобальный инстанс
    if not llm_health_gate.check_availability():
        logger.warning(f"LLM в состоянии DEGRADED, пропускаем рынок {m.id}")
        return None, None, None

    logger.info("  Скачиваем новости (RSS + Reddit + Wikipedia)...")
    
    search_query = build_search_query(m.title)
    logger.info(f"  Поисковый запрос: '{search_query}' (оригинал: '{m.title}')")
    
    from agents.shared.utils.web_search import fetch_wikipedia_context
    
    IS_TECH_MARKET = any(kw in m.title.lower() for kw in ["ai", "llm", "crypto", "bitcoin", "ethereum", "openai", "model"])
    
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    future_rss = executor.submit(fetch_rss_news, search_query)
    future_reddit = executor.submit(fetch_reddit_news, search_query)
    future_wiki = executor.submit(fetch_wikipedia_context, search_query)
    future_hn = executor.submit(fetch_hackernews, search_query) if IS_TECH_MARKET else None
    
    try:
        news_titles = _safe_result(future_rss, default=[], timeout=15)
        reddit_posts = _safe_result(future_reddit, default=[], timeout=15)
        wiki_context = _safe_result(future_wiki, default=[], timeout=20)
        hn_posts = _safe_result(future_hn, default=[], timeout=15) if future_hn else []
    finally:
        future_rss.cancel()
        future_reddit.cancel()
        future_wiki.cancel()
        if future_hn:
            future_hn.cancel()
        if sys.version_info >= (3, 9):
            executor.shutdown(wait=False, cancel_futures=True)
        else:
            executor.shutdown(wait=False)

    # Google Trends — последовательно (не thread-safe)
    trends_data = fetch_google_trends(search_query)

    api_key = getattr(scout, 'api_key', None) or getattr(swing, 'api_key', None)
    model = getattr(scout, 'model', None) or getattr(swing, 'model', None) or "gemini-2.5-flash"
    
    grounded = ""
    if api_key:
        grounded = _fetch_grounded_context(m, api_key, model)
    else:
        grounded = "Grounding не выполнен (нет API-ключа)."

    from core.dedup import deduplicate_headlines
    news_titles = deduplicate_headlines(news_titles, grounded)

    context = MarketContext(
        market=m,
        orderbook=pre_orderbook,
        news_titles=news_titles,
        reddit_posts=reddit_posts,
        wiki_context=wiki_context,
        trends_data=trends_data,
        hn_posts=hn_posts,
        trigger_type=trigger_type,
        source_url=source_url,
        source_text=source_text,
        triggered_at=triggered_at,
        search_query=search_query,
        grounded_context=grounded
    )

    from core.price_velocity import detect_velocity_anomaly
    velocity = detect_velocity_anomaly(price_history or [])
    context.velocity_annotation = velocity.annotation

    from core.orderbook_shape import analyze_orderbook_shape
    ob_dict = {}
    if pre_orderbook:
        ob_dict = {
            "top_bid": pre_orderbook.top_bid,
            "top_ask": pre_orderbook.top_ask,
            "spread_cents": pre_orderbook.spread_cents,
            "bid_depth_5": pre_orderbook.bid_depth_5,
            "ask_depth_5": pre_orderbook.ask_depth_5
        }
    ob_shape = analyze_orderbook_shape(ob_dict, m.price)
    context.orderbook_shape_annotation = ob_shape.annotation

    # ── Вариант 2: обогащаем контекст корреляциями ──────────────────────────
    corr_list = get_market_correlations(m.id)
    if corr_list:
        lines = []
        for c in corr_list[:_MAX_CORR_PEERS]:
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
        
        from core.math_filter import math_pre_filter, FilterDecision
        from core.arb_router import route_ambiguous

        if adapter:
            best_arb_result = None
            for c in corr_list[:_MAX_CORR_PEERS]:          # проверяем топ-N корреляции
                peer_id = c["market_id_b"] if c["market_id_a"] == m.id else c["market_id_a"]
                try:
                    peer_market = adapter.get_market(peer_id)
                    if not peer_market:
                        continue
                    mf = math_pre_filter(m, peer_market)
                    # Роутим AMBIGUOUS через мини-LLM
                    if mf.decision == FilterDecision.AMBIGUOUS:
                        if api_key:
                            arb_verdict = route_ambiguous(mf, m, peer_market, api_key=api_key)
                            if arb_verdict and arb_verdict.get("confirmed_arb"):
                                logger.info(f"[arb_router] Арбитраж подтверждён: {m.title} ↔ {peer_market.title}")
                                # Апгрейдим mf до confirmed для отображения в Telegram
                                from dataclasses import replace
                                mf = replace(mf, decision=FilterDecision.CONFIRMED_ARBITRAGE, has_arbitrage=True)
                        else:
                            logger.warning("[workflow] route_ambiguous пропущен: нет api_key")
                    # Запоминаем лучший результат (с наибольшим спредом)
                    if best_arb_result is None or mf.spread_pct > best_arb_result.spread_pct:
                        best_arb_result = mf
                        context.math_filter_result = mf
                except Exception as e:
                    logger.error(f"[workflow] math_pre_filter error for peer {peer_id}: {e}")
                
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
        has_strong_scout = signal and getattr(signal, 'edge', 0) >= 0.55
        if not velocity.has_anomaly and velocity.suspicion in ("ORGANIC", "NOISE") and not has_strong_scout:
            logger.info(f"  SWING: пропущен (flat price/noise, нет сильного SCOUT-сигнала)")
            swing_signal = None
        else:
            swing_signal = swing.estimate_market(context, price_history=price_history)
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
    swing_rec = getattr(swing_signal, 'recommendation', '').lower() if swing_signal else ''
    valid_swing_buy = swing_signal is not None and swing_rec == 'buy'
    swing_analyzed = swing_signal is not None
    
    if (valid_scout or valid_swing_buy) and shadow_ok:
        status = 'saved'
    elif (valid_scout or valid_swing_buy):
        status = 'no_consensus'
    elif swing_analyzed:
        status = 'no_signal_swing_hold'
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
        if swing_signal and getattr(swing_signal, 'recommendation', '').lower() == 'buy': save_signal(swing_signal)
        update_state(ideas_found=state.get("ideas_found", 0) + 1)
    elif decision.status == 'no_consensus':
        logger.info("  --- Консенсус не достигнут (SHADOW забраковал).")
    else:
        logger.info("  SCOUT и SWING: Идей не найдено.")
        update_state(scout_status="⚪️ Идея не найдена", swing_status="⚪️ Идея не найдена")

    if summary_callback and decision.status in ('saved', 'no_consensus'):
        # Определяем действие
        price_yes = int(m.price * 100)
        price_no = 100 - price_yes
        
        # Заголовок — обсуждение рынка
        summary_text = (
            f"🗣️ <b>Обсуждение рынка:</b>\n"
            f"<a href='{m.url}'>{m.title}</a> (YES: {price_yes}¢ | NO: {price_no}¢)\n\n"
        )
        
        # Источник (если event-driven)
        if context.trigger_type == "event_driven" and context.source_url and context.source_url.strip():
            source_label = context.source_text or "Источник"
            summary_text += f"📡 <b>Триггер:</b> <a href='{context.source_url}'>{source_label}</a>\n\n"
        elif context.trigger_type == "scheduled":
            summary_text += f"🔄 <b>Триггер:</b> Плановый скан\n\n"
        elif context.trigger_type == "manual":
            summary_text += f"👤 <b>Триггер:</b> Ручной запуск\n\n"
        else:
            summary_text += f"⚡ <b>Триггер:</b> {context.trigger_type}\n\n"
        
        # Детальная аналитика по каждому агенту
        if signal:
            summary_text += "🧠 <b>SCOUT (Фундаментал):</b>\n"
            cause = getattr(signal, 'signal_cause', '') or getattr(signal, 'summary', '')
            if cause:
                summary_text += f"🎯 <b>Причина:</b> {cause}\n"
            risk = getattr(signal, 'signal_risk', '') or getattr(signal, 'details', '')
            if risk:
                summary_text += f"⚖️ <b>Риск:</b> {risk}\n"
            oracle_risk = getattr(signal, 'oracle_risk', '')
            if oracle_risk:
                summary_text += f"👁️ <b>Оракул-риск:</b> {oracle_risk}\n"
            verdict = getattr(signal, 'signal_verdict', '') or getattr(signal, 'trade_action', '')
            if verdict:
                summary_text += f"📝 <b>Вердикт:</b> {verdict}\n"
            summary_text += "\n"
        else:
            summary_text += "🧠 <b>SCOUT (Фундаментал):</b>\n⚠️ Ошибка оценки рынка или превышение лимитов запросов к API.\n\n"
        
        if swing_signal:
            summary_text += "🏄 <b>SWING (Хайп):</b>\n"
            catalyst = getattr(swing_signal, 'catalyst', '')
            if catalyst:
                summary_text += f"🚀 <b>Катализатор:</b> {catalyst}\n"
            else:
                quiet_reason = getattr(swing_signal, 'catalyst_absence_reason', '')
                if quiet_reason:
                    summary_text += f"💤 <b>Почему тихо:</b> {quiet_reason}\n"
            risk = getattr(swing_signal, 'swing_risk', '') or getattr(swing_signal, 'details', '')
            if risk:
                summary_text += f"⚖️ <b>Риск:</b> {risk}\n"
            verdict = getattr(swing_signal, 'swing_verdict', '') or getattr(swing_signal, 'recommendation', '')
            if verdict:
                summary_text += f"📝 <b>Вердикт:</b> {verdict}\n"
            summary_text += "\n"
        else:
            summary_text += "🏄 <b>SWING (Хайп):</b>\n⚠️ Ошибка оценки рынка или превышение лимитов запросов к API.\n\n"
            
        if opinion_shadow:
            shadow_status = "✅ СОГЛАСЕН" if opinion_shadow.agree else "❌ ПРОТИВ"
            summary_text += f"🛡️ <b>SHADOW (Инфраструктура):</b> {shadow_status}\n"
            liq = getattr(opinion_shadow, 'liquidity_risk', 'MEDIUM').upper()
            summary_text += f"💧 <b>Риск ликвидности:</b> {liq}\n"
            ob = getattr(opinion_shadow, 'orderbook_facts', '')
            if ob:
                summary_text += f"📊 <b>Ордербук:</b> {ob}\n"
            execution_risk = getattr(opinion_shadow, 'risk_assessment', '')
            if execution_risk:
                summary_text += f"⚖️ <b>Исполнение:</b> {execution_risk}\n"
            verdict = getattr(opinion_shadow, 'shadow_verdict', '') or getattr(opinion_shadow, 'opinion', '')
            if verdict:
                summary_text += f"📝 <b>Вердикт:</b> {verdict}\n"
            summary_text += "\n"
            
        # Арбитраж из math_filter (если есть)
        math_result = getattr(context, 'math_filter_result', None)
        if math_result and math_result.has_arbitrage:
            if math_result.trade_instruction and math_result.trade_instruction.strip():
                summary_text += f"⚡️ <b>Арбитраж ({math_result.spread_pct:.1f}%):</b>\n{math_result.trade_instruction}\n\n"
            else:
                logger.warning(f"[math_filter] has_arbitrage=True but trade_instruction empty for {m.id}")

        # Итоговое решение консенсуса
        if decision.status == 'saved':
            summary_text += "✨ <b>ИТОГ: Идея подтверждена консенсусом и добавлена в список торговых идей /ideas.</b>\n\n"
        else:
            summary_text += "🛑 <b>ИТОГ: Консенсус не достигнут (SHADOW отклонил). Идея НЕ добавляется в список /ideas.</b>\n\n"

        # Кнопки Игнорировать / Следить (market_id трункируется до 40 симв — лимит callback_data 64 байта)
        mid = m.id[:40]
        market_action_markup = {
            "inline_keyboard": [[
                {"text": "🚫 Игнорировать", "callback_data": f"ignore_mkt_{mid}"},
                {"text": "👁 Следить", "callback_data": f"watch_mkt_{mid}"},
                {"text": "📥 В идеи", "callback_data": f"add_idea_{mid}"}
            ]]
        }

        try:
            import inspect
            sig_params = inspect.signature(summary_callback).parameters
            if "reply_markup" in sig_params:
                summary_callback(summary_text, reply_markup=market_action_markup)
            else:
                summary_callback(summary_text)
        except Exception as cb_err:
            logger.error(f"summary_callback error in process_consensus: {cb_err}")
        
    audit = {
        "scout_edge": signal.edge if signal else None,
        "swing_found": 1 if swing_signal else 0,
        "shadow_agree": int(opinion_shadow.agree) if opinion_shadow else None,
        "shadow_confidence": opinion_shadow.confidence if opinion_shadow else None,
        "shadow_reason": (opinion_shadow.opinion or "")[:200] if opinion_shadow else "",
        "final_outcome": decision.status
    }
    try:
        if signal or swing_signal or opinion_shadow:
            save_idea_audit(m.id, m.title, audit)
    except Exception as e:
        logger.error(f"[workflow] save_idea_audit failed for {m.id}: {e}")
    
    from core.checkpoint import save_checkpoint, verify_checkpoint
    save_checkpoint(f"consensus_{m.id}", status="ok")
    saved_ok = verify_checkpoint(f"consensus_{m.id}")
    if not saved_ok:
        logger.warning(f"[CHECKPOINT] Консенсус для {m.id} не сохранён в аудит!")


    # Эпизодическая память агентов (Спринт 7)
    from agents.shared.python.db import save_agent_episode
    try:
        if signal:
            save_agent_episode(
                agent_name="SCOUT",
                event_type="signal_evaluated",
                summary=f"Opinion: {getattr(signal, 'signal_verdict', 'buy')[:80]} | Reason: {getattr(signal, 'signal_cause', getattr(signal, 'details', ''))}",
                market_id=m.id,
                market_title=m.title
            )
            
        if swing_signal:
            save_agent_episode(
                agent_name="SWING",
                event_type="signal_evaluated",
                summary=f"Opinion: {getattr(swing_signal, 'swing_verdict', 'buy')[:80]} | Reason: {getattr(swing_signal, 'catalyst', getattr(swing_signal, 'catalyst_absence_reason', ''))}",
                market_id=m.id,
                market_title=m.title
            )
            
        if opinion_shadow:
            save_agent_episode(
                agent_name="SHADOW",
                event_type="signal_evaluated",
                summary=f"Opinion: {getattr(opinion_shadow, 'shadow_verdict', 'agree')[:80]} | Reason: {getattr(opinion_shadow, 'risk_assessment', getattr(opinion_shadow, 'orderbook_facts', ''))}",
                market_id=m.id,
                market_title=m.title
            )
    except Exception as e:
        logger.error(f"[workflow] save_agent_episode failed for {m.id}: {e}")

def process_arbitrage_signal(
    arb_signal,
    orderbook_a: dict,
    orderbook_b: Optional[dict],
    summary_callback
) -> None:
    """
    Для арбитражных сигналов SHADOW запускается только как liquidity check.
    Не нужен полный LLM-вызов — только check_liquidity_fast() на обоих стаканах.
    """
    from core.liquidity_checker import check_liquidity_fast
    from agents.shared.python.db import save_signal
    from core.models import Signal
    
    liq_a = check_liquidity_fast(orderbook_a)
    liq_b = check_liquidity_fast(orderbook_b) if orderbook_b else liq_a
    
    if liq_a.ok and liq_b.ok:
        signal_compat = Signal(
            id=arb_signal.id,
            type=arb_signal.type,  # CROSS_PLATFORM, SYNTHETIC, TEMPORAL
            market_id=arb_signal.market_id_a,
            platform=arb_signal.platform_a,
            edge=arb_signal.edge,
            confidence=arb_signal.confidence,
            priority="high" if arb_signal.spread_pct > 10.0 else "medium",
            summary=arb_signal.summary,
            details=arb_signal.details,
            target_outcome=arb_signal.target_outcome,
            status="PENDING",
            created_at=arb_signal.created_at
        )
        save_signal(signal_compat)  # сохранить в БД
        
        # Строим красивое сообщение для Telegram
        msg = (
            f"⚡️ <b>АРБИТРАЖНЫЙ СИГНАЛ ({arb_signal.type})</b> ⚡️\n\n"
            f"📊 Спред: <b>{arb_signal.spread_pct:.1f}%</b>\n"
            f"🎯 Исход: <b>{arb_signal.target_outcome}</b>\n"
            f"💰 Макс. ставка: <b>${arb_signal.max_safe_size:.1f}</b>\n\n"
            f"🧠 <b>Суть:</b> {arb_signal.summary}\n"
            f"📝 <b>Детали:</b> {arb_signal.details}\n"
        )
        summary_callback(msg)
    else:
        logger.info(f"Арбитраж отклонён (ликвидность): {arb_signal.id}")
