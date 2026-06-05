import os
import sys
import time
import concurrent.futures
from datetime import datetime, timezone
from typing import Optional, Callable, Set, Dict

from core.models import Market, Signal, SwingSignal, AgentOpinion, IdeaDecision
from core.context import MarketContext


from config import (
    logger, SCREENING_INTERVAL_SEC, SCAN_LIMIT_DEFAULT, MIN_EDGE_DEFAULT,
    MAX_SCREENING_MARKETS, PRICE_RANGE_MIN, PRICE_RANGE_MAX, MIN_MARKET_VOLUME_USD,
    ARB_MIN_SPREAD_PCT
)
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
import threading
_analyzed_in_session: Dict[str, float] = {}
_dedup_lock = threading.Lock()

def _cleanup_session_dedup() -> None:
    """Удаляем устаревшие ключи из in-memory дедупликатора."""
    cutoff = time.monotonic() - _SESSION_DEDUP_TTL_SEC
    with _dedup_lock:
        expired = [k for k, ts in _analyzed_in_session.items() if ts < cutoff]
        for k in expired:
            del _analyzed_in_session[k]

def _prefilter_markets(markets_compact: list) -> list:
    """
    Уровень 1 (без LLM): базовая фильтрация по объёму, цене и времени.
    Сокращает ~1000 рынков до ~100-150 перед передачей в NEXUS.
    """
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    min_close = now + timedelta(days=3)
    filtered = []

    for m in markets_compact:
        price = m.get('price', m.get('p', 0.5))
        v_val = m.get('volume')
        if v_val is None:
            v_val = m.get('vol')
        if v_val is None:
            continue
            
        try:
            volume = float(v_val)
        except (ValueError, TypeError):
            continue
            
        close_str = m.get('close_time', m.get('end', ''))

        # Фильтр: цена в диапазоне интереса
        if not (PRICE_RANGE_MIN <= price <= PRICE_RANGE_MAX):
            continue
        # Фильтр: объём (есть ликвидность)
        if volume < MIN_MARKET_VOLUME_USD:
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


def _fetch_markets_parallel(adapter, market_ids: list, max_workers: int = 8) -> list:
    """Параллельно загружает рынки, игнорирует ошибки отдельных запросов."""
    import concurrent.futures as cf
    results = []
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(adapter.get_market, mid): mid for mid in market_ids}
        try:
            for fut in cf.as_completed(futures, timeout=30):
                try:
                    m = fut.result()
                    if m:
                        results.append(m)
                except Exception as e:
                    logger.debug(f"get_market failed for {futures[fut]}: {e}")
        except cf.TimeoutError:
            logger.warning("Timeout in _fetch_markets_parallel after 30s")
    return results

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
            if last_screen.tzinfo is None:
                last_screen = last_screen.replace(tzinfo=timezone.utc)
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
                    logger.warning(f"[workflow] Unexpected type for screened_market_ids: {type(cached)}, сбрасываем кэш")
                    save_memory("screened_market_ids", [], category='cache', ttl=SCREENING_INTERVAL_SEC)
                    return []
                return cached
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
            screened_markets_full = _fetch_markets_parallel(adapter, screened_market_ids)

            arb_pairs = find_complementary_pairs(screened_markets_full, min_spread_pct=ARB_MIN_SPREAD_PCT)
            correlations_count = len(arb_pairs)
            saved_count = 0

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
                    saved_count += 1
                    
                    if mf.has_arbitrage:
                        # Загружаем стаканы ног
                        ob_a = adapter.get_orderbook(ma.tokens[0]) if ma.tokens else None
                        ob_b = adapter.get_orderbook(mb.tokens[0]) if mb.tokens else None
                        
                        from core.models import ArbitrageSignal
                        
                        arb_sig = ArbitrageSignal(
                            id=f"sig-arb-intra-{ma.id}-{mb.id}-{int(now.timestamp())}",
                            type="SYNTHETIC",
                            market_id_a=ma.id,
                            market_id_b=mb.id,
                            platform_a=ma.platform,
                            platform_b=mb.platform,
                            spread_pct=mf.spread_pct,
                            target_outcome="YES",
                            max_safe_size=100.0,
                            edge=mf.spread_pct / 100.0,
                            confidence=min(mf.spread_pct / 20.0, 1.0),
                            summary=f"Внутриплатформенный арбитраж на Polymarket ({mf.spread_pct:.1f}%)",
                            details=mf.trade_instruction + "\n\n" + mf.reasoning,
                            status="PENDING"
                        )
                        
                        if summary_callback:
                            process_arbitrage_signal(arb_sig, ob_a, ob_b, summary_callback)
                except Exception as e:
                    logger.error(f"[screening] save_correlation/arbitrage error: {e}", exc_info=True)

            save_memory("screened_market_ids", screened_market_ids, category='cache', ttl=SCREENING_INTERVAL_SEC)
            save_memory("last_screen_time", now.isoformat(), category='cache', ttl=SCREENING_INTERVAL_SEC)
            
            logger.info(f"[screening] Код отобрал {len(screened_market_ids)} кандидатов, "
                        f"найдено {correlations_count} арб-пар")
            
            if saved_count > 0 and summary_callback:
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



def _safe_result(future: concurrent.futures.Future, default, timeout: int = 15, label: str = "unknown"):
    """Получает результат Future с таймаутом; возвращает default при любой ошибке."""
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        logger.warning(f"[workflow] '{label}' timed out after {timeout}s")
        return default
    except Exception as e:
        logger.warning(f"[workflow] '{label}' failed: {e}")
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



async def run_agent_evaluation(m: Market, scout, swing, update_state: Callable, adapter=None, trigger_type="scheduled", source_url=None, source_text=None, triggered_at=None, price_history=None, pre_orderbook=None, scan_category: Optional[str] = None):
    import config
    if getattr(config, "shutdown_requested", False):
        logger.info("[workflow] Прерывание оценки: запрошена остановка системы.")
        return None, None, None

    _cleanup_session_dedup()

    from core.price_velocity import detect_velocity_anomaly
    velocity = detect_velocity_anomaly(price_history or [])
    has_anomaly = getattr(velocity, 'has_anomaly', False)

    last_analysis_key = f"last_analysis:{m.id}"

    if not has_anomaly:
        # In-session дедупликация (быстрая проверка без БД)
        dedup_key = f"{m.id}:{trigger_type}"
        with _dedup_lock:
            if dedup_key in _analyzed_in_session:
                logger.info(
                    f"[workflow] Пропуск дубля (in-session): {m.id} "
                    f"({trigger_type}), добавлен {time.monotonic() - _analyzed_in_session[dedup_key]:.0f}с назад"
                )
                return None, None, None
            _analyzed_in_session[dedup_key] = time.monotonic()

        # БД-уровень дедупликации (межсессионная защита)
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
    else:
        logger.info(f"[workflow] Обход дедупликации для рынка {m.id} из-за аномальной скорости изменения цены.")


    # Guard: проверяем доступность LLM перед запуском
    from config import llm_health_gate  # глобальный инстанс
    llm_health_gate.check_availability()
    # Временный лок перед медленной генерацией LLM
    save_memory(last_analysis_key, datetime.now(timezone.utc).isoformat(),
                category='cache', ttl=300)

    logger.info("  Скачиваем новости (RSS + Reddit + Wikipedia)...")
    
    search_query = build_search_query(m.title)
    logger.info(f"  Поисковый запрос: '{search_query}' (оригинал: '{m.title}')")
    
    from agents.shared.utils.web_search import fetch_wikipedia_context
    
    IS_TECH_MARKET = any(kw in m.title.lower() for kw in ["ai", "llm", "crypto", "bitcoin", "ethereum", "openai", "model"])
    IS_NICHE_MARKET = not any(
        kw in m.title.lower()
        for kw in ["crypto", "bitcoin", "ethereum", "politics", "election", "trump", "biden", "sports", "cup", "game", "league", "ai", "llm", "openai"]
    )
    
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    try:
        try:
            future_rss = executor.submit(fetch_rss_news, search_query)
            future_reddit = executor.submit(fetch_reddit_news, search_query)
            future_wiki = executor.submit(fetch_wikipedia_context, search_query) if IS_NICHE_MARKET else None
            future_hn = executor.submit(fetch_hackernews, search_query) if IS_TECH_MARKET else None
        except RuntimeError as e:
            if "interpreter shutdown" in str(e) or "cannot schedule" in str(e):
                logger.warning(f"[workflow] Executor shutdown during scan: {e}")
                future_rss = future_reddit = future_wiki = future_hn = None
            else:
                raise
        
        news_titles = _safe_result(future_rss, default=[], timeout=15, label="RSS") if future_rss else []
        reddit_posts = _safe_result(future_reddit, default=[], timeout=15, label="Reddit") if future_reddit else []
        wiki_context = _safe_result(future_wiki, default=[], timeout=20, label="Wikipedia") if future_wiki else []
        hn_posts = _safe_result(future_hn, default=[], timeout=15, label="HackerNews") if future_hn else []
    finally:
        shutdown_kwargs = {"wait": False, "cancel_futures": True} \
            if sys.version_info >= (3, 9) else {"wait": False}
        executor.shutdown(**shutdown_kwargs)

    # Google Trends — последовательно (не thread-safe)
    trends_data = fetch_google_trends(search_query)

    api_key = getattr(scout, 'api_key', None) or getattr(swing, 'api_key', None)
    
    grounding_config = get_memory("agent_config_GROUNDING") or {}
    nexus_config = get_memory("agent_config_NEXUS") or {}
    grounding_model = grounding_config.get("model") or nexus_config.get("model") or "gemini-2.5-flash"
    
    grounded = ""
    if api_key:
        grounded = _fetch_grounded_context(m, api_key, grounding_model)
    else:
        grounded = "Grounding не выполнен (нет API-ключа)."

    from core.dedup import deduplicate_headlines
    news_titles = deduplicate_headlines(news_titles, grounded)

    # Разбиваем grounded текст на отдельные строки для сопоставления по MD5 хэшам
    grounded_lines = []
    if grounded:
        for line in grounded.split('\n'):
            line = line.strip().lstrip('-*•1234567890.').strip()
            if line:
                grounded_lines.append(line)

    from agents.shared.utils.web_search import deduplicate_rss_against_grounding
    # Дедуплицируем news_titles относительно grounded_lines (убираем дубликаты без смешивания)
    news_titles = deduplicate_rss_against_grounding(news_titles, grounded_lines)
    
    # Дедуплицируем посты в reddit_posts
    from agents.shared.utils.web_search import deduplicate_list
    reddit_posts = deduplicate_list(reddit_posts)
    
    from core.dedup import deduplicate_reddit_posts
    reddit_posts = deduplicate_reddit_posts(reddit_posts, grounded)

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

    # velocity уже вычислен в начале функции run_agent_evaluation
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


    # ── ON-CHAIN GATEKEEPER (Итерация 17) ───────────────────────────────────
    from core.onchain_gate import check_onchain_gate
    from core.smart_money import fetch_smart_money_sync
    from core.onchain_scorer import compute_onchain_score
    from agents.shared.python.db import save_gate_metrics
    import uuid

    sm_data = fetch_smart_money_sync(m.id)
    total_vol = (
        (sm_data.total_yes_usd + sm_data.total_no_usd)
        if sm_data and sm_data.available
        else (m.volume or 0.0)
    )
    oc_score = compute_onchain_score(sm_data) if sm_data else None
    market_tag = scan_category or getattr(m, "category", "default") or "default"

    gate = check_onchain_gate(oc_score, m.id, total_vol, market_tag)

    if not gate.allow:
        logger.info(f"[SwingGate] ⛔ {m.title[:60]!r} — {gate.reason}")
        save_gate_metrics(
            run_id=str(uuid.uuid4())[:8],
            total=1, passed=0,
            blocked_no_volume=int(gate.blocked_by == "volume"),
            blocked_no_whales=int(gate.blocked_by == "whales")
        )
        return None, None, None
    else:
        save_gate_metrics(
            run_id=str(uuid.uuid4())[:8],
            total=1, passed=1,
            blocked_no_volume=0,
            blocked_no_whales=0
        )
    # ────────────────────────────────────────────────────────────────────────


    logger.info("  SCOUT и SWING оценивают...")
    update_state(scout_status="🔄 Считает вероятности...", swing_status="🔄 Оценивает хайп...")

    from core.guards import LLMUnavailableError
    from core.checkpoint import save_checkpoint
    
    # SCOUT
    try:
        signal = await scout.estimate_market(context, price_history=price_history or [])
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
        is_flat_or_noise = not velocity.has_anomaly and velocity.suspicion in ("ORGANIC", "NOISE")
        has_enough_history = len(price_history or []) >= 3
        if is_flat_or_noise and has_enough_history and not has_strong_scout:
            logger.info(f"  SWING: пропущен (flat price/noise с достаточной историей, нет сильного SCOUT-сигнала)")
            context.swing_skipped = True
            context.swing_skip_reason = "Пропущен (flat price/noise с достаточной историей, нет сильного SCOUT-сигнала)"
            swing_signal = None
        else:
            swing_signal = await swing.estimate_market(context, price_history=price_history)
        save_checkpoint(f"swing_{m.id}", status="ok")
    except LLMUnavailableError:
        save_checkpoint(f"swing_{m.id}", status="llm_unavailable")
        raise
    except Exception as e:
        save_checkpoint(f"swing_{m.id}", status="error", error=str(e))
        swing_signal = None
        
    if signal is not None or swing_signal is not None:
        save_memory(last_analysis_key, datetime.now(timezone.utc).isoformat(),
                    category='cache', ttl=_SESSION_DEDUP_TTL_SEC)
    else:
        save_memory(last_analysis_key, datetime.now(timezone.utc).isoformat(),
                    category='cache', ttl=300)

    return signal, swing_signal, context

def make_consensus(context: MarketContext, signal: Optional[Signal], swing_signal: Optional[SwingSignal], opinion_shadow: Optional[AgentOpinion]) -> IdeaDecision:
    m = context.market
    # Решение принимает SHADOW через поле agree. Проверка liquidity_risk убрана,
    # т.к. пользователь оперирует микро-банком ($10-100) и high liquidity_risk — норма.
    shadow_ok = opinion_shadow and opinion_shadow.agree
    
    from core.config_provider import ConfigProvider
    MIN_SCOUT_EDGE = ConfigProvider.get_min_edge_sync("scout")
    valid_scout = signal is not None and getattr(signal, 'edge', 0) >= MIN_SCOUT_EDGE
    swing_rec = getattr(swing_signal, 'recommendation', '').lower() if swing_signal else ''
    MIN_SWING_CONFIDENCE = 0.40  # минимум 40% уверенности для BUY
    valid_swing_buy = (
        swing_signal is not None
        and swing_rec == 'buy'
        and getattr(swing_signal, 'confidence', 0) >= MIN_SWING_CONFIDENCE
    )
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

def process_consensus(context: MarketContext, signal: Optional[Signal], swing_signal: Optional[SwingSignal], opinion_shadow: Optional[AgentOpinion], state: dict, update_state: Callable, summary_callback: Optional[Callable], api_key: Optional[str] = None):
    m = context.market
    decision = make_consensus(context, signal, swing_signal, opinion_shadow)
    
    if decision.status == 'saved':
        logger.info("  !!! ИДЕЯ ПОДТВЕРЖДЕНА КОНСЕНСУСОМ.")
        if signal:
            save_signal(signal)
            try:
                from core.eval.signal_logger import SignalLogger, StrategyType
                logger_eval = SignalLogger()
                logger_eval.log_signal(
                    signal_id=signal.id,
                    strategy_type=StrategyType.SCOUT,
                    market_id=signal.market_id,
                    predicted_probability=getattr(signal, 'confidence', 0.5),
                    market_price_at_signal=m.price,
                    edge_at_signal=getattr(signal, 'edge', 0.0) or 0.0,
                    metadata={
                        "target_outcome": signal.target_outcome,
                        "priority": signal.priority,
                        "summary": signal.summary,
                        "platform": signal.platform,
                    }
                )
            except Exception as e:
                logger.error(f"Ошибка логирования Scout-сигнала в Evaluation Engine: {e}", exc_info=True)
        if swing_signal and getattr(swing_signal, 'recommendation', '').lower() == 'buy':
            swing_as_signal = Signal(
                id=swing_signal.id,
                type=swing_signal.type,
                market_id=swing_signal.market_id,
                platform=swing_signal.platform,
                target_outcome=swing_signal.target_outcome,
                edge=swing_signal.edge,
                confidence=swing_signal.confidence,
                priority=swing_signal.priority,
                summary=swing_signal.summary,
                details=swing_signal.details,
            )
            save_signal(swing_as_signal)
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
        elif getattr(context, 'swing_skipped', False):
            reason = getattr(context, 'swing_skip_reason', 'пропущен (flat price/noise)')
            summary_text += f"🏄 <b>SWING (Хайп):</b>\n💤 {reason}\n\n"
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
        from core.math_filter import FilterDecision
        math_result = getattr(context, 'math_filter_result', None)
        if math_result:
            if math_result.has_arbitrage:
                instruction = math_result.trade_instruction.strip() if (math_result.trade_instruction and math_result.trade_instruction.strip()) else None
                if not instruction:
                    instruction = "Купить YES (арбитраж)"
                    try:
                        from agents.shared.python.db import get_last_analyzed_price
                        last_price = get_last_analyzed_price(m.id)
                        if last_price:
                            instruction += f" при цене {last_price:.3f}"
                    except Exception as db_err:
                        logger.error(f"Ошибка получения последней цены из БД для fallback-инструкции: {db_err}")
                summary_text += f"⚡️ <b>Арбитраж ({math_result.spread_pct:.1f}%):</b>\n{instruction}\n\n"
            elif math_result.decision == FilterDecision.AMBIGUOUS and math_result.spread_pct > 0:
                if not api_key:
                    summary_text += f"⚠️ <b>Возможен арбитраж ({math_result.spread_pct:.1f}%):</b>\nНе подтверждён (нет API-ключа для проверки)\n\n"
                else:
                    summary_text += f"⚠️ <b>Возможен арбитраж ({math_result.spread_pct:.1f}%):</b>\nНе подтверждён проверкой агента\n\n"

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
            from core.utils import _callback_accepts_reply_markup
            if _callback_accepts_reply_markup(summary_callback):
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
    for attempt in range(3):
        try:
            save_checkpoint(f"consensus_{m.id}", status="ok")
            if verify_checkpoint(f"consensus_{m.id}"):
                break
        except Exception as e:
            logger.error(f"[CHECKPOINT] Ошибка сохранения {m.id}: {e}", exc_info=True)
        logger.warning(f"[CHECKPOINT] Retry {attempt+1}/3 для {m.id}")
    else:
        logger.error(f"[CHECKPOINT] КРИТИЧНО: консенсус {m.id} не сохранён после 3 попыток")
        try:
            from services.notifications import send_telegram
            alert_msg = f"❌ <b>[CHECKPOINT ERROR]</b>\nКРИТИЧНО: консенсус для рынка {m.id} ('{m.title}') не сохранён после 3 попыток!"
            send_telegram(alert_msg)
        except Exception as alert_err:
            logger.error(f"Не удалось отправить алерт об ошибке чекпоинта: {alert_err}")


    # Эпизодическая память агентов (Спринт 7)
    from agents.shared.python.db import save_agent_episode
    try:
        if signal:
            verdict_scout = getattr(signal, 'signal_verdict', None)
            if verdict_scout is None:
                verdict_scout = getattr(signal, 'trade_action', None)
            if verdict_scout is None:
                verdict_scout = 'buy'
            save_agent_episode(
                agent_name="SCOUT",
                event_type="signal_evaluated",
                summary=f"Opinion: {str(verdict_scout)[:80]} | Reason: {getattr(signal, 'signal_cause', getattr(signal, 'details', ''))}",
                market_id=m.id,
                market_title=m.title
            )
            
        if swing_signal:
            verdict_swing = getattr(swing_signal, 'swing_verdict', None)
            if verdict_swing is None:
                verdict_swing = getattr(swing_signal, 'recommendation', None)
            if verdict_swing is None:
                verdict_swing = 'buy'
            save_agent_episode(
                agent_name="SWING",
                event_type="signal_evaluated",
                summary=f"Opinion: {str(verdict_swing)[:80]} | Reason: {getattr(swing_signal, 'catalyst', getattr(swing_signal, 'catalyst_absence_reason', ''))}",
                market_id=m.id,
                market_title=m.title
            )
            
        if opinion_shadow:
            verdict_shadow = getattr(opinion_shadow, 'shadow_verdict', None)
            if verdict_shadow is None:
                verdict_shadow = getattr(opinion_shadow, 'opinion', None)
            if verdict_shadow is None:
                verdict_shadow = 'agree'
            save_agent_episode(
                agent_name="SHADOW",
                event_type="signal_evaluated",
                summary=f"Opinion: {str(verdict_shadow)[:80]} | Reason: {getattr(opinion_shadow, 'risk_assessment', getattr(opinion_shadow, 'orderbook_facts', ''))}",
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
    from agents.shared.python.db import save_arbitrage_signal_to_db
    
    liq_a = check_liquidity_fast(orderbook_a)
    if orderbook_b is None:
        logger.warning(f"[arbitrage] Нет данных стакана для второй ноги — отклоняем {arb_signal.id}")
        return
        
    liq_b = check_liquidity_fast(orderbook_b)
    
    if liq_a.ok and liq_b.ok:
        save_arbitrage_signal_to_db(arb_signal)  # сохранить в БД cross_arbitrage_signals
        
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
