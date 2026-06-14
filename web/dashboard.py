# web/dashboard.py
import asyncio
import logging
import functools
import time
import threading
from pathlib import Path
from aiohttp import web
from web import data_provider

logger = logging.getLogger("NexusPolyBot.Dashboard")
TEMPLATES_DIR = Path(__file__).parent / "templates"

def render_template(page_name: str) -> str:
    """Читает base.html и вставляет контент конкретной страницы."""
    base_path = TEMPLATES_DIR / "base.html"
    page_path = TEMPLATES_DIR / page_name
    
    if not base_path.exists():
        return f"Error: base.html not found in {TEMPLATES_DIR}"
    if not page_path.exists():
        return f"Error: {page_name} not found in {TEMPLATES_DIR}"
        
    with open(base_path, "r", encoding="utf-8") as f:
        base_html = f.read()
    with open(page_path, "r", encoding="utf-8") as f:
        page_html = f.read()
        
    return base_html.replace("<!-- PAGE_CONTENT -->", page_html)


# === HTML хэндлеры ===

async def handle_favicon(request):
    favicon_path = Path(__file__).parent / "templates" / "favicon.png"
    if favicon_path.exists():
        return web.FileResponse(favicon_path, headers={"Content-Type": "image/png"})
    return web.Response(status=404)

async def handle_overview(request):
    html = await asyncio.to_thread(render_template, "overview.html")
    return web.Response(text=html, content_type="text/html")

async def handle_penny_stocks(request):
    html = await asyncio.to_thread(render_template, "penny_stocks.html")
    return web.Response(text=html, content_type="text/html")

async def handle_favourite_compounding(request):
    html = await asyncio.to_thread(render_template, "favourite_compounding.html")
    return web.Response(text=html, content_type="text/html")

async def handle_scout(request):
    html = await asyncio.to_thread(render_template, "scout.html")
    return web.Response(text=html, content_type="text/html")

async def handle_whale(request):
    html = await asyncio.to_thread(render_template, "whale.html")
    return web.Response(text=html, content_type="text/html")

async def handle_corridors(request):
    html = await asyncio.to_thread(render_template, "corridors.html")
    return web.Response(text=html, content_type="text/html")

# === JSON API хэндлеры ===

async def api_overview(request):
    data = await asyncio.to_thread(data_provider.get_overview_stats)
    return web.json_response(data)

async def api_eval_status(request):
    data = await asyncio.to_thread(data_provider.get_eval_status)
    return web.json_response(data)

def get_int_query(request, name, default):
    val_str = request.query.get(name)
    if not val_str:
        return default
    try:
        return int(val_str)
    except ValueError:
        return default

async def api_penny_stocks(request):
    active_page = get_int_query(request, "active_page", 1)
    active_limit = get_int_query(request, "active_limit", 50)
    resolved_page = get_int_query(request, "resolved_page", 1)
    resolved_limit = get_int_query(request, "resolved_limit", 50)
    history_page = get_int_query(request, "history_page", 1)
    history_limit = get_int_query(request, "history_limit", 50)
    
    data = await asyncio.to_thread(
        data_provider.get_penny_stocks_dashboard,
        active_page=active_page,
        active_limit=active_limit,
        resolved_page=resolved_page,
        resolved_limit=resolved_limit,
        history_page=history_page,
        history_limit=history_limit
    )
    with _jobs_lock:
        running_ids = [mid for mid, job in analysis_jobs.items() if job.get("status") == "running"]
    data["running_analyses"] = running_ids
    return web.json_response(data)

async def api_favourite_compounding(request):
    active_page = get_int_query(request, "active_page", 1)
    active_limit = get_int_query(request, "active_limit", 50)
    resolved_page = get_int_query(request, "resolved_page", 1)
    resolved_limit = get_int_query(request, "resolved_limit", 50)
    history_page = get_int_query(request, "history_page", 1)
    history_limit = get_int_query(request, "history_limit", 50)
    
    data = await asyncio.to_thread(
        data_provider.get_compounding_dashboard,
        active_page=active_page,
        active_limit=active_limit,
        resolved_page=resolved_page,
        resolved_limit=resolved_limit,
        history_page=history_page,
        history_limit=history_limit
    )
    return web.json_response(data)

async def api_equity_curve(request):
    strategy = request.query.get("strategy", "all")
    days_str = request.query.get("days", "30")
    try:
        days = int(days_str)
        days = max(1, min(days, 365))
    except ValueError:
        days = 30
    data = await asyncio.to_thread(data_provider.get_equity_curve, strategy, days)
    return web.json_response(data)

async def api_signals(request):
    strategy = request.query.get("strategy")
    if not strategy:
        return web.json_response({"error": "strategy parameter is required"}, status=400)
    days_str = request.query.get("days", "30")
    limit_str = request.query.get("limit", "50")
    page_str = request.query.get("page")
    sort_by = request.query.get("sort_by")
    sort_dir = request.query.get("sort_dir")
    
    if days_str == "all" or days_str == "None":
        days = None
    else:
        try:
            days = int(days_str)
        except ValueError:
            days = 30
            
    try:
        limit = int(limit_str)
    except ValueError:
        limit = 50
        
    page = None
    if page_str:
        try:
            page = int(page_str)
        except ValueError:
            page = 1
            
    data = await asyncio.to_thread(
        data_provider.get_strategy_signals,
        strategy,
        days,
        limit,
        page,
        sort_by,
        sort_dir
    )
    return web.json_response(data)

async def api_corridors(request):
    synthetic_page = get_int_query(request, "synthetic_page", 1)
    synthetic_limit = get_int_query(request, "synthetic_limit", 50)
    temporal_page = get_int_query(request, "temporal_page", 1)
    temporal_limit = get_int_query(request, "temporal_limit", 50)
    cross_page = get_int_query(request, "cross_page", 1)
    cross_limit = get_int_query(request, "cross_limit", 50)
    
    data = await asyncio.to_thread(
        data_provider.get_corridors_dashboard,
        synthetic_page=synthetic_page,
        synthetic_limit=synthetic_limit,
        temporal_page=temporal_page,
        temporal_limit=temporal_limit,
        cross_page=cross_page,
        cross_limit=cross_limit
    )
    return web.json_response(data)

async def api_delete_market(request):
    try:
        body = await request.json()
        table_name = body.get("table_name")
        record_id = body.get("record_id")
        if not table_name or record_id is None:
            return web.json_response({"error": "table_name and record_id are required"}, status=400)
    except Exception as e:
        return web.json_response({"error": f"Invalid JSON body: {e}"}, status=400)
        
    from agents.shared.python.db import delete_market_record
    
    success = await asyncio.to_thread(delete_market_record, table_name, str(record_id))
    if success:
        return web.json_response({"status": "ok"})
    else:
        return web.json_response({"error": "Record not found"}, status=404)

async def api_buy_penny_stock(request):
    try:
        try:
            body = await request.json()
        except Exception as json_err:
            return web.json_response({"error": f"Invalid JSON body: {json_err}"}, status=400)

        market_id = body.get("market_id")
        price_val = body.get("price")
        
        if not market_id or price_val is None:
            return web.json_response({"error": "market_id and price are required"}, status=400)
            
        try:
            price = float(price_val)
        except (ValueError, TypeError) as num_err:
            return web.json_response({"error": f"Invalid price format: {num_err}"}, status=400)
            
    except (KeyError, TypeError) as req_err:
        return web.json_response({"error": f"Malformed request parameters: {req_err}"}, status=400)

    try:
        from agents.shared.python.db import buy_virtual_penny_stock
        await asyncio.to_thread(buy_virtual_penny_stock, market_id, price)
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.exception("Error in api_buy_penny_stock")
        return web.json_response({"error": f"Internal server error: {e}"}, status=500)

async def api_sell_penny_stock(request):
    try:
        try:
            body = await request.json()
        except Exception as json_err:
            return web.json_response({"error": f"Invalid JSON body: {json_err}"}, status=400)

        market_id = body.get("market_id")
        if not market_id:
            return web.json_response({"error": "market_id is required"}, status=400)
            
    except (KeyError, TypeError) as req_err:
        return web.json_response({"error": f"Malformed request parameters: {req_err}"}, status=400)

    try:
        from agents.shared.python.db import sell_virtual_penny_stock
        await asyncio.to_thread(sell_virtual_penny_stock, market_id)
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.exception("Error in api_sell_penny_stock")
        return web.json_response({"error": f"Internal server error: {e}"}, status=500)

async def api_buy_compound_opportunity(request):
    try:
        try:
            body = await request.json()
        except Exception as json_err:
            return web.json_response({"error": f"Invalid JSON body: {json_err}"}, status=400)

        opp_id = body.get("opp_id")
        price_val = body.get("price")
        
        if not opp_id or price_val is None:
            return web.json_response({"error": "opp_id and price are required"}, status=400)
            
        try:
            price = float(price_val)
        except (ValueError, TypeError) as num_err:
            return web.json_response({"error": f"Invalid price format: {num_err}"}, status=400)
            
    except (KeyError, TypeError) as req_err:
        return web.json_response({"error": f"Malformed request parameters: {req_err}"}, status=400)

    try:
        from agents.shared.python.db import buy_virtual_compound_opportunity
        await asyncio.to_thread(buy_virtual_compound_opportunity, opp_id, price)
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.exception("Error in api_buy_compound_opportunity")
        return web.json_response({"error": f"Internal server error: {e}"}, status=500)

async def api_sell_compound_opportunity(request):
    try:
        try:
            body = await request.json()
        except Exception as json_err:
            return web.json_response({"error": f"Invalid JSON body: {json_err}"}, status=400)

        opp_id = body.get("opp_id")
        price_val = body.get("price")
        
        if not opp_id or price_val is None:
            return web.json_response({"error": "opp_id and price are required"}, status=400)
            
        try:
            price = float(price_val)
        except (ValueError, TypeError) as num_err:
            return web.json_response({"error": f"Invalid price format: {num_err}"}, status=400)
            
    except (KeyError, TypeError) as req_err:
        return web.json_response({"error": f"Malformed request parameters: {req_err}"}, status=400)

    try:
        from agents.shared.python.db import sell_virtual_compound_opportunity
        await asyncio.to_thread(sell_virtual_compound_opportunity, opp_id, price)
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.exception("Error in api_sell_compound_opportunity")
        return web.json_response({"error": f"Internal server error: {e}"}, status=500)

async def background_analyze_penny_markets(markets):
    try:
        from core.singleton import get_core_engine
        from core.workflow import run_agent_evaluation
        from agents.shared.python.db import get_connection
        
        def update_predictions_in_db(market_id, outcome, edge, confidence):
            with get_connection() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='penny_stocks_monitoring'"
                ).fetchone()
                if not exists:
                    logger.warning("[PennyBg] Table penny_stocks_monitoring not found, skipping")
                    return
                conn.execute("""
                    UPDATE penny_stocks_monitoring
                    SET predicted_outcome = ?,
                        edge = ?,
                        confidence = ?
                    WHERE market_id = ?
                """, (outcome, edge, confidence, market_id))

        engine = get_core_engine()
        for m in markets:
            try:
                price_hist = []
                try:
                    from agents.shared.python.db import get_price_history
                    price_hist = await asyncio.to_thread(get_price_history, m.id, hours=24)
                except Exception:
                    pass
                
                if hasattr(engine, '_fetch_pre_orderbook'):
                    pre_orderbook = await asyncio.to_thread(engine._fetch_pre_orderbook, m)
                else:
                    logger.warning(
                        f"[PennyBg] engine._fetch_pre_orderbook not available, "
                        f"running analysis without pre_orderbook for {m.id}"
                    )
                    pre_orderbook = None
                
                def dummy_update(**kwargs):
                    pass
                
                logger.info(f"Background analysis starting for penny stock: {m.title}")
                signal, swing_signal, _ = await run_agent_evaluation(
                    m, engine.scout, engine.swing, dummy_update,
                    adapter=engine.adapter, trigger_type="scheduled",
                    price_history=price_hist, pre_orderbook=pre_orderbook,
                    scan_category="penny_stocks"
                )
                
                active_sig = signal or swing_signal
                pred_out = None
                edge_val = None
                conf_val = None
                if active_sig:
                    pred_out = active_sig.target_outcome
                    edge_val = active_sig.edge
                    conf_val = active_sig.confidence
                    logger.info(f"Background analysis completed for {m.title}: {pred_out} (edge: {edge_val})")
                else:
                    logger.info(f"Background analysis completed for {m.title}: no signal found")
                
                await asyncio.to_thread(update_predictions_in_db, m.id, pred_out, edge_val, conf_val)
                
            except Exception as exc:
                logger.error(f"Error in background analysis for penny stock {m.id}: {exc}", exc_info=True)
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"Error in background_analyze_penny_markets: {e}", exc_info=True)

async def api_discover_penny_stocks(request):
    try:
        from core.singleton import get_core_engine
        from agents.shared.python.market_selector import MarketSelector
        from agents.shared.python.db import (
            add_penny_stock_to_monitoring,
            get_active_penny_stocks
        )
        from telegram.bot import bot, AUTHORIZED_CHAT_ID
        
        engine = get_core_engine()
        selector = MarketSelector(engine.adapter)
        
        # Загружаем до 100 дешевых рынков
        markets = await asyncio.to_thread(
            selector.select, total_limit=100, category="penny_stocks"
        )
        
        if not markets:
            return web.json_response({"status": "ok", "discovered": 0})
            
        active_stocks = await asyncio.to_thread(get_active_penny_stocks)
        active_ids = {m["market_id"] for m in active_stocks}
        
        new_discovered_markets = []
        for m in markets:
            if m.id in active_ids:
                continue
                
            # Рынки с ценой 0.10–0.90 не являются penny stocks по определению.
            if not (m.price <= 0.10 or m.price >= 0.90):
                logger.debug(
                    f"Discovered market {m.id} ('{m.title}') has price {m.price} outside penny stock limits (<=0.10 or >=0.90), skipping."
                )
                continue
                
            # Добавляем в мониторинг как неанализированный
            await asyncio.to_thread(
                add_penny_stock_to_monitoring,
                market_id=m.id,
                title=m.title,
                url=m.url,
                initial_price=m.price,
                predicted_outcome=None
            )
            new_discovered_markets.append(m)
            
        new_discovered = len(new_discovered_markets)
        if new_discovered > 0:
            # Запускаем последовательный фоновый анализ рынков агентами
            asyncio.create_task(background_analyze_penny_markets(new_discovered_markets))
            
            msg = (
                f"📥 <b>Принудительный импорт Penny Stocks</b>\n\n"
                f"С дашборда запрошен принудительный поиск дешевых рынков.\n"
                f"Обнаружено и добавлено в мониторинг: <b>{new_discovered}</b> новых рынков.\n"
                f"<i>Запущен фоновый последовательный анализ агентами...</i>"
            )
            try:
                await bot.send_message(AUTHORIZED_CHAT_ID, msg, parse_mode="HTML", disable_web_page_preview=True)
            except Exception as tg_err:
                logger.error(f"Failed to send Telegram notification: {tg_err}")
                
        return web.json_response({"status": "ok", "discovered": new_discovered})
        
    except Exception as e:
        logger.exception("Error in api_discover_penny_stocks")
        return web.json_response({"error": str(e)}, status=500)

async def api_whale_stocks(request):
    active_page = get_int_query(request, "active_page", 1)
    active_limit = get_int_query(request, "active_limit", 50)
    resolved_page = get_int_query(request, "resolved_page", 1)
    resolved_limit = get_int_query(request, "resolved_limit", 50)
    history_page = get_int_query(request, "history_page", 1)
    history_limit = get_int_query(request, "history_limit", 50)
    whales_page = get_int_query(request, "whales_page", 1)
    whales_limit = get_int_query(request, "whales_limit", 10)
    
    data = await asyncio.to_thread(
        data_provider.get_whale_stocks_dashboard,
        active_page=active_page,
        active_limit=active_limit,
        resolved_page=resolved_page,
        resolved_limit=resolved_limit,
        history_page=history_page,
        history_limit=history_limit,
        whales_page=whales_page,
        whales_limit=whales_limit
    )
    with _jobs_lock:
        running_ids = [mid for mid, job in analysis_jobs.items() if job.get("status") == "running"]
    data["running_analyses"] = running_ids
    return web.json_response(data)

async def api_buy_whale_stock(request):
    try:
        try:
            body = await request.json()
        except Exception as json_err:
            return web.json_response({"error": f"Invalid JSON body: {json_err}"}, status=400)

        market_id = body.get("market_id")
        price_val = body.get("price")
        
        if not market_id or price_val is None:
            return web.json_response({"error": "market_id and price are required"}, status=400)
            
        try:
            price = float(price_val)
        except (ValueError, TypeError) as num_err:
            return web.json_response({"error": f"Invalid price format: {num_err}"}, status=400)
            
    except (KeyError, TypeError) as req_err:
        return web.json_response({"error": f"Malformed request parameters: {req_err}"}, status=400)

    try:
        from agents.shared.python.db import buy_virtual_whale_stock
        await asyncio.to_thread(buy_virtual_whale_stock, market_id, price)
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.exception("Error in api_buy_whale_stock")
        return web.json_response({"error": f"Internal server error: {e}"}, status=500)

async def api_sell_whale_stock(request):
    try:
        try:
            body = await request.json()
        except Exception as json_err:
            return web.json_response({"error": f"Invalid JSON body: {json_err}"}, status=400)

        market_id = body.get("market_id")
        sell_price = body.get("sell_price")
        if not market_id:
            return web.json_response({"error": "market_id is required"}, status=400)
            
        if sell_price is not None:
            try:
                sell_price = float(sell_price)
            except ValueError:
                return web.json_response({"error": "sell_price must be a float"}, status=400)
            
    except (KeyError, TypeError) as req_err:
        return web.json_response({"error": f"Malformed request parameters: {req_err}"}, status=400)

    try:
        from agents.shared.python.db import sell_virtual_whale_stock
        await asyncio.to_thread(sell_virtual_whale_stock, market_id, sell_price)
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.exception("Error in api_sell_whale_stock")
        return web.json_response({"error": f"Internal server error: {e}"}, status=500)

async def api_discover_whale_stocks(request):
    try:
        from services.onchain_trend_alert import scan_volume_spikes, scan_large_single_bets, scan_wallet_series
        from telegram.bot import bot, AUTHORIZED_CHAT_ID
        
        spikes = await asyncio.to_thread(scan_volume_spikes) or []
        bets = await asyncio.to_thread(scan_large_single_bets) or []
        series = await asyncio.to_thread(scan_wallet_series) or []
        
        total_discovered = len(spikes) + len(bets) + len(series)
        
        if total_discovered > 0:
            msg = (
                f"🐳 <b>Принудительный поиск Whale Following</b>\n\n"
                f"С дашборда запрошен принудительный поиск активности китов.\n"
                f"Обнаружено сигналов: <b>{total_discovered}</b> (всплески: {len(spikes)}, крупные ставки: {len(bets)}, серии сделок: {len(series)})."
            )
            try:
                await bot.send_message(AUTHORIZED_CHAT_ID, msg, parse_mode="HTML", disable_web_page_preview=True)
            except Exception as tg_err:
                logger.error(f"Failed to send Telegram notification: {tg_err}")
                
        return web.json_response({"status": "ok", "discovered": total_discovered})
        
    except Exception as e:
        logger.exception("Error in api_discover_whale_stocks")
        return web.json_response({"error": str(e)}, status=500)

# === Фоновый запуск анализа ===


analysis_jobs = {}
_background_tasks = set()
_jobs_lock = threading.Lock()
_last_cleanup = 0.0
_CLEANUP_INTERVAL = 60.0

import concurrent.futures
_analysis_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="scout-analysis"
)
_analysis_semaphore = asyncio.Semaphore(2)
_analysis_queue = None

async def analysis_worker():
    """Фоновый воркер для обработки очереди анализов."""
    global _analysis_queue
    while True:
        try:
            market_id = await _analysis_queue.get()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"analysis_worker: ошибка получения задания: {e}")
            continue  # task_done не нужен — задание не было взято
        try:
            await run_analysis_in_background(market_id)
        except Exception as e:
            logger.exception(f"Error in analysis worker for {market_id}")
        finally:
            _analysis_queue.task_done()

def _cleanup_stale_jobs():
    global _last_cleanup
    _now = time.time()
    if _now - _last_cleanup < _CLEANUP_INTERVAL:
        return
    _last_cleanup = _now
    with _jobs_lock:
        stale = [k for k, v in analysis_jobs.items() if _now - v.get("_ts", _now) > 3600]
        for k in stale:
            del analysis_jobs[k]

async def run_analysis_in_background(market_id: str):
    async with _analysis_semaphore:
        await _run_analysis_in_background_impl(market_id)

async def _run_analysis_in_background_impl(market_id: str):
    from core.singleton import get_core_engine
    from core.workflow import run_agent_evaluation, process_consensus
    from agents.shared.python.db import get_market_from_db, get_market_discussions, mark_market_analyzed
    
    engine = get_core_engine()
    
    # 1. Получаем инфо о рынке из нашей БД
    db_market = await asyncio.to_thread(get_market_from_db, market_id)
    if not db_market:
        try:
            m = await asyncio.to_thread(engine.adapter.get_market, market_id)
            if m:
                db_market = {
                    "id": m.id,
                    "title": m.title,
                    "price": m.price,
                    "url": m.url
                }
        except Exception as e:
            logger.error(f"Failed to fetch market {market_id} from adapter: {e}")
            
    if not db_market:
        with _jobs_lock:
            analysis_jobs[market_id] = {
                "status": "failed",
                "error": "Рынок не найден ни в локальной БД, ни в адаптере Polymarket.",
                "_ts": time.time()
            }
        return
        
    full_market_id = db_market["id"]
    market_title = db_market["title"]
    
    try:
        # Инициализируем статус
        with _jobs_lock:
            analysis_jobs[market_id] = {
                "status": "running",
                "progress": {
                    "scout": "⚙️ Анализирует...",
                    "swing": "⏳ Ожидает",
                    "shadow": "⏳ Ожидает"
                },
                "_ts": time.time()
            }
        
        # Подгружаем полноценный объект рынка из адаптера
        market = await asyncio.to_thread(engine.adapter.get_market, full_market_id)
        if not market:
            from core.models import Market
            from datetime import datetime
            market = Market(
                id=full_market_id,
                platform="polymarket",
                title=market_title,
                price=db_market["price"],
                url=db_market["url"],
                outcome="YES",
                close_time=datetime.now()
            )
            
        price_hist = []
        try:
            from agents.shared.python.db import get_price_history
            price_hist = await asyncio.to_thread(get_price_history, market.id, hours=24)
        except Exception:
            pass
            
        pre_orderbook = engine._fetch_pre_orderbook(market)
        
        def sync_update_state(**kwargs):
            scout_status = kwargs.get("scout_status")
            swing_status = kwargs.get("swing_status")
            shadow_status = kwargs.get("shadow_status")
            
            with _jobs_lock:
                job = analysis_jobs.get(market_id)
                if job and job.get("status") == "running":
                    progress = job.setdefault("progress", {})
                    if scout_status:
                        progress["scout"] = scout_status
                    if swing_status:
                        progress["swing"] = swing_status
                    if shadow_status:
                        progress["shadow"] = shadow_status
                    job["_ts"] = time.time()
                    
        signal, swing_signal, context = await run_agent_evaluation(
            market,
            scout=engine.scout,
            swing=engine.swing,
            update_state=sync_update_state,
            adapter=engine.adapter,
            trigger_type="manual",
            price_history=price_hist,
            pre_orderbook=pre_orderbook
        )
        
        if context is None:
            with _jobs_lock:
                analysis_jobs[market_id] = {
                    "status": "failed",
                    "error": "Анализ рынка пропущен (дедупликация или сбой).",
                    "_ts": time.time()
                }
            return
            
        sync_update_state(shadow_status="⚙️ Анализирует...")
        loop = asyncio.get_running_loop()
        opinion_shadow = await loop.run_in_executor(
            _analysis_executor,
            functools.partial(
                engine._run_shadow_analysis,
                m=market,
                active_signal=signal or swing_signal,
                signal=signal,
                swing_signal=swing_signal,
                context=context,
                price_hist=price_hist,
                _update_state=sync_update_state,
                log=logger.info
            )
        )
        
        if opinion_shadow:
            sh_status = "✅ Согласен" if opinion_shadow.agree else "❌ Против"
            sync_update_state(shadow_status=f"{sh_status} (Увер: {opinion_shadow.confidence})")
        else:
            sync_update_state(shadow_status="⚪️ Нет мнения")
            
        await asyncio.to_thread(
            process_consensus,
            context, signal, swing_signal, opinion_shadow,
            engine.state, sync_update_state, None,
            api_key=engine.api_key
        )
        
        await asyncio.to_thread(mark_market_analyzed, market.id, market.price)
        
        opinions = await asyncio.to_thread(get_market_discussions, market.id)
        
        with _jobs_lock:
            analysis_jobs[market_id] = {
                "status": "completed",
                "opinions": opinions,
                "_ts": time.time()
            }
        
    except Exception as e:
        logger.exception(f"Error in background dashboard analysis for {market_id}")
        with _jobs_lock:
            analysis_jobs[market_id] = {
                "status": "failed",
                "error": str(e),
                "_ts": time.time()
            }

async def api_analyze_market(request):
    _cleanup_stale_jobs()
    try:
        body = await request.json()
        market_id = body.get("market_id")
        force = body.get("force", False)
        if not market_id:
            return web.json_response({"error": "market_id is required"}, status=400)
    except Exception as e:
        return web.json_response({"error": f"Invalid request body: {e}"}, status=400)
        
    from agents.shared.python.db import get_market_discussions
    
    if force:
        logger.info(f"Force re-analysis requested for market {market_id}. Clearing DB opinions/cache and active jobs.")
        from agents.shared.python.db import get_connection
        def clear_db():
            with get_connection() as conn:
                conn.execute("DELETE FROM agent_opinions WHERE market_id = ?", (market_id,))
                conn.execute("DELETE FROM analyzed_markets WHERE market_id = ?", (market_id,))
        await asyncio.to_thread(clear_db)
        with _jobs_lock:
            if market_id in analysis_jobs:
                del analysis_jobs[market_id]
            
    with _jobs_lock:
        job = analysis_jobs.get(market_id)
        if job:
            if job["status"] == "completed":
                job["_ts"] = time.time()
                return web.json_response({"status": "completed", "opinions": job["opinions"]})
            elif job["status"] == "failed":
                del analysis_jobs[market_id]
            else:
                return web.json_response({"status": "running", "progress": job.get("progress")})
            
    opinions = await asyncio.to_thread(get_market_discussions, market_id)
    if opinions and len(opinions) > 0:
        with _jobs_lock:
            analysis_jobs[market_id] = {
                "status": "completed",
                "opinions": opinions,
                "_ts": time.time()
            }
        return web.json_response({"status": "completed", "opinions": opinions})
        
    if _analysis_queue is None:
        return web.json_response({"error": "Сервер ещё инициализируется, попробуйте позже."}, status=503)
    try:
        _analysis_queue.put_nowait(market_id)
        with _jobs_lock:
            analysis_jobs[market_id] = {
                "status": "running",
                "progress": {
                    "scout": "⏳ В очереди...",
                    "swing": "⏳ Ожидает",
                    "shadow": "⏳ Ожидает"
                },
                "_ts": time.time()
            }
        return web.json_response({"status": "running", "progress": {
            "scout": "⏳ В очереди...",
            "swing": "⏳ Ожидает",
            "shadow": "⏳ Ожидает"
        }})
    except asyncio.QueueFull:
        return web.json_response({"error": "Очередь анализов переполнена. Пожалуйста, подождите завершения текущих задач."}, status=429)

async def api_analyze_market_status(request):
    _cleanup_stale_jobs()
    market_id = request.query.get("market_id")
    if not market_id:
        return web.json_response({"error": "market_id is required"}, status=400)
        
    with _jobs_lock:
        job = analysis_jobs.get(market_id)
        if job:
            if job["status"] == "completed":
                job["_ts"] = time.time()
                return web.json_response({"status": "completed", "opinions": job["opinions"]})
            elif job["status"] == "failed":
                err = job.get("error")
                del analysis_jobs[market_id]
                return web.json_response({"status": "failed", "error": err})
            else:
                return web.json_response({"status": "running", "progress": job.get("progress")})
            
    from agents.shared.python.db import get_market_discussions
    opinions = await asyncio.to_thread(get_market_discussions, market_id)
    if opinions and len(opinions) > 0:
        with _jobs_lock:
            analysis_jobs[market_id] = {
                "status": "completed",
                "opinions": opinions,
                "_ts": time.time()
            }
        return web.json_response({"status": "completed", "opinions": opinions})
        
    return web.json_response({"status": "not_found"})

# === Фабрика приложения ===

def create_dashboard_app() -> web.Application:
    """Создает и настраивает инстанс aiohttp приложения."""
    app = web.Application()
    
    # HTML маршруты
    app.router.add_get("/favicon.ico", handle_favicon)
    app.router.add_get("/favicon.png", handle_favicon)
    app.router.add_get("/", handle_overview)
    app.router.add_get("/penny-stocks", handle_penny_stocks)
    app.router.add_get("/favourite-compounding", handle_favourite_compounding)
    app.router.add_get("/scout", handle_scout)
    app.router.add_get("/whale", handle_whale)
    app.router.add_get("/corridors", handle_corridors)
    
    # JSON API маршруты
    app.router.add_get("/api/overview", api_overview)
    app.router.add_get("/api/eval-status", api_eval_status)
    app.router.add_get("/api/penny-stocks", api_penny_stocks)
    app.router.add_get("/api/favourite-compounding", api_favourite_compounding)
    app.router.add_get("/api/whale-stocks", api_whale_stocks)
    app.router.add_get("/api/equity-curve", api_equity_curve)
    app.router.add_get("/api/signals", api_signals)
    app.router.add_get("/api/corridors", api_corridors)
    
    # POST API маршруты (виртуальный портфель и ручной анализ)
    app.router.add_post("/api/penny-stocks/buy", api_buy_penny_stock)
    app.router.add_post("/api/penny-stocks/sell", api_sell_penny_stock)
    app.router.add_post("/api/penny-stocks/analyze", api_analyze_market)
    app.router.add_get("/api/penny-stocks/analyze-status", api_analyze_market_status)
    app.router.add_post("/api/penny-stocks/discover", api_discover_penny_stocks)
    app.router.add_post("/api/favourite-compounding/buy", api_buy_compound_opportunity)
    app.router.add_post("/api/favourite-compounding/sell", api_sell_compound_opportunity)
    app.router.add_post("/api/whale-stocks/buy", api_buy_whale_stock)
    app.router.add_post("/api/whale-stocks/sell", api_sell_whale_stock)
    app.router.add_post("/api/whale-stocks/analyze", api_analyze_market)
    app.router.add_get("/api/whale-stocks/analyze-status", api_analyze_market_status)
    app.router.add_post("/api/whale-stocks/discover", api_discover_whale_stocks)
    app.router.add_post("/api/delete-market", api_delete_market)

    
    logger.info("Application routes successfully registered.")
    
    async def on_startup(app):
        global _analysis_queue
        _analysis_queue = asyncio.Queue(maxsize=10)
        for i in range(2):
            task = asyncio.create_task(analysis_worker(), name=f"analysis-worker-{i}")
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
        await asyncio.sleep(0)
            
    async def on_cleanup(app):
        for task in list(_background_tasks):
            task.cancel()
        if _background_tasks:
            await asyncio.gather(*_background_tasks, return_exceptions=True)
        _analysis_executor.shutdown(wait=False)
        await asyncio.sleep(0)

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    
    return app
