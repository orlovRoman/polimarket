# web/dashboard.py
import asyncio
import logging
import functools
import time
import threading
from pathlib import Path
from aiohttp import web
from web import data_provider
import re
from web.whale_config import WHALE_DASHBOARD_CONFIG

logger = logging.getLogger("NexusPolyBot.WebDashboard")
TEMPLATES_DIR = Path(__file__).parent / "templates"

_scheduler = None
_calibration_running = False

def set_scheduler(scheduler):
    global _scheduler
    _scheduler = scheduler

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
        
    html = base_html.replace("<!-- PAGE_CONTENT -->", page_html)
    from web.ui_config import UI_CONFIG
    html = html.replace("{{WHALE_CHART_HEIGHT}}", str(UI_CONFIG.whale_chart_height_px))
    return html


def require_system_active(handler):
    @functools.wraps(handler)
    async def wrapper(request):
        from agents.shared.python.db import is_system_paused
        if await asyncio.to_thread(is_system_paused):
            return web.json_response(
                {"error": "Система на паузе. Действие заблокировано."}, 
                status=403
            )
        return await handler(request)
    return wrapper

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
    raise web.HTTPFound("/overview")

async def handle_calibration(request):
    html = await asyncio.to_thread(render_template, "calibration.html")
    return web.Response(text=html, content_type="text/html")

async def handle_learning(request):
    html = await asyncio.to_thread(render_template, "learning.html")
    return web.Response(text=html, content_type="text/html")

# === JSON API хэндлеры ===

async def api_overview(request):
    data = await asyncio.to_thread(data_provider.get_overview_stats)
    return web.json_response(data)

async def api_system_status_get(request):
    from agents.shared.python.db import get_memory
    keys = [
        "strategy_scout_enabled",
        "strategy_synthetic_corridor_enabled",
        "strategy_temporal_corridor_enabled",
        "strategy_cross_platform_enabled",
        "strategy_whale_enabled",
        "strategy_penny_stocks_enabled",
        "strategy_favourite_compounding_enabled",
        "process_memory_archive_enabled",
        "process_evaluation_enabled",
        "process_wallet_recalculation_enabled",
        "process_clusters_insiders_enabled",
        "process_audit_resolutions_enabled"
    ]
    data = {}
    for key in keys:
        default_val = False if key in [
            "strategy_synthetic_corridor_enabled",
            "strategy_temporal_corridor_enabled",
            "strategy_cross_platform_enabled"
        ] else True
        data[key] = get_memory(key, default_val)
    return web.json_response(data)

ALLOWED_TOGGLE_KEYS = {
    "strategy_scout_enabled",
    "strategy_synthetic_corridor_enabled",
    "strategy_temporal_corridor_enabled",
    "strategy_cross_platform_enabled",
    "strategy_whale_enabled",
    "strategy_penny_stocks_enabled",
    "strategy_favourite_compounding_enabled",
    "strategy_compound_parlays_enabled",
    "process_memory_archive_enabled",
    "process_evaluation_enabled",
    "process_wallet_recalculation_enabled",
    "process_clusters_insiders_enabled",
    "process_audit_resolutions_enabled",
}

async def api_system_status_post(request):
    data = await request.json()
    from agents.shared.python.db import save_memory
    
    for key, value in data.items():
        if key in ALLOWED_TOGGLE_KEYS and isinstance(value, bool):
            save_memory(key, value)
            
    return web.json_response({"status": "ok"})

async def api_memory_stats(request):
    data = await asyncio.to_thread(data_provider.get_memory_stats)
    return web.json_response(data)

async def api_eval_status(request):
    data = await asyncio.to_thread(data_provider.get_eval_status)
    return web.json_response(data)

async def api_learning_impact(request):
    data = await asyncio.to_thread(data_provider.get_learning_impact_data)
    return web.json_response(data)

async def api_agent_performance(request):
    data = await asyncio.to_thread(data_provider.get_agent_performance_data)
    return web.json_response(data)

def get_int_query(request, name, default):
    val_str = request.query.get(name)
    val = default
    if val_str:
        try:
            val = int(val_str)
        except ValueError:
            pass
            
    if name.endswith("limit"):
        from web.ui_config import UI_CONFIG
        val = min(val, UI_CONFIG.max_limit)
        
    return val

async def api_penny_stocks(request):
    active_page = get_int_query(request, "active_page", 1)
    active_limit = get_int_query(request, "active_limit", 50)
    resolved_page = get_int_query(request, "resolved_page", 1)
    resolved_limit = get_int_query(request, "resolved_limit", 50)
    history_page = get_int_query(request, "history_page", 1)
    history_limit = get_int_query(request, "history_limit", 50)
    wins_page = get_int_query(request, "wins_page", resolved_page)
    wins_limit = get_int_query(request, "wins_limit", resolved_limit)
    losses_page = get_int_query(request, "losses_page", resolved_page)
    losses_limit = get_int_query(request, "losses_limit", resolved_limit)
    
    data = await asyncio.to_thread(
        data_provider.get_penny_stocks_dashboard,
        active_page=active_page,
        active_limit=active_limit,
        resolved_page=resolved_page,
        resolved_limit=resolved_limit,
        history_page=history_page,
        history_limit=history_limit,
        wins_page=wins_page,
        wins_limit=wins_limit,
        losses_page=losses_page,
        losses_limit=losses_limit
    )
    with _jobs_lock:
        running_ids = [mid for mid, job in analysis_jobs.items() if job.get("status") == "running"]
    data["running_analyses"] = running_ids
    return web.json_response(data)

async def api_favourite_compounding(request):
    active_page = get_int_query(request, "active_page", 1)
    active_limit = get_int_query(request, "active_limit", 50)
    wins_page = get_int_query(request, "wins_page", 1)
    wins_limit = get_int_query(request, "wins_limit", 50)
    losses_page = get_int_query(request, "losses_page", 1)
    losses_limit = get_int_query(request, "losses_limit", 50)
    history_page = get_int_query(request, "history_page", 1)
    history_limit = get_int_query(request, "history_limit", 50)
    
    data = await asyncio.to_thread(
        data_provider.get_compounding_dashboard,
        active_page=active_page,
        active_limit=active_limit,
        wins_page=wins_page,
        wins_limit=wins_limit,
        losses_page=losses_page,
        losses_limit=losses_limit,
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
    data = {
        'synthetic': [],
        'temporal': [],
        'cross': [],
        'kpis': {},
        'synthetic_total': 0,
        'temporal_total': 0,
        'cross_total': 0
    }
    return web.json_response(data)

async def api_calibration_runs(request):
    from web.calibration_provider import CalibrationProvider
    data = await asyncio.to_thread(CalibrationProvider.get_recent_calibration_runs, 10)
    return web.json_response(data)

async def api_calibration_pending(request):
    from web.calibration_provider import CalibrationProvider
    data = await asyncio.to_thread(CalibrationProvider.get_pending_calibration_params)
    return web.json_response(data)

async def api_calibration_overlays(request):
    from web.calibration_provider import CalibrationProvider
    data = await asyncio.to_thread(CalibrationProvider.get_current_overlays)
    return web.json_response(data)
async def api_calibration_history(request):
    from web.calibration_provider import CalibrationProvider
    try:
        limit = int(request.query.get("limit", 50))
    except ValueError:
        limit = 50
    status_param = request.query.get("status")
    statuses = [s.strip() for s in status_param.split(",") if s.strip()] if status_param else None
    data = await asyncio.to_thread(CalibrationProvider.get_calibration_history, limit, statuses)
    return web.json_response(data)

async def api_calibration_approve(request):
    try:
        body = await request.json()
        param_id = int(body.get("id"))
    except Exception:
        return web.json_response({"error": "Invalid request body"}, status=400)
    from web.calibration_provider import CalibrationProvider
    success = await asyncio.to_thread(CalibrationProvider.approve_calibration_param, param_id, "dashboard")
    return web.json_response({"status": "ok" if success else "failed"})

async def api_calibration_reject(request):
    try:
        body = await request.json()
        param_id = int(body.get("id"))
    except Exception:
        return web.json_response({"error": "Invalid request body"}, status=400)
    from web.calibration_provider import CalibrationProvider
    success = await asyncio.to_thread(CalibrationProvider.reject_calibration_param, param_id)
    return web.json_response({"status": "ok" if success else "failed"})
async def api_calibration_force_run(request):
    global _calibration_running
    if _calibration_running:
        return web.json_response({"error": "Calibration is already running. Please wait."}, status=409)
    _calibration_running = True
    try:
        from agents.orchestrator.scripts.calibrate import run_calibration
        report, has_updates = await run_calibration(window_days=7, trigger_type="manual")
        return web.json_response({"status": "ok", "has_updates": has_updates, "report": report})
    except Exception as e:
        logger.error(f"Force run failed: {e}")
        return web.json_response({"error": str(e)}, status=500)
    finally:
        _calibration_running = False

# === Whale Radar API ===
async def api_whale_radar(request):
    """
    Возвращает агрегированные позиции китов по рынкам.
    Query params:
      - min_whales (int, default=1): минимум китов для включения рынка
    """
    try:
        from agents.shared.python.whale_portfolio_service import get_whale_radar_summary
        min_whales = int(request.query.get("min_whales", 1))
        # Делаем вызов блокирующей функции в отдельном треде
        data = await asyncio.to_thread(get_whale_radar_summary, min_whales)
        return web.json_response({"ok": True, "data": data, "count": len(data)})
    except Exception as e:
        logger.error(f"[API /whale-radar] Ошибка: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def api_whale_radar_sync(request):
    """Ручной тригер синхронизации."""
    try:
        from agents.shared.python.whale_portfolio_service import run_portfolio_sync_job
        # запускаем джобу асинхронно в бэкграунде чтобы не блокировать ответ
        asyncio.create_task(run_portfolio_sync_job())
        return web.json_response({"ok": True, "message": "Синхронизация запущена"})
    except Exception as e:
        logger.error(f"[API /whale-radar/sync] Ошибка: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def api_calibration_schedule_status(request):
    if not _scheduler:
        return web.json_response({"error": "Scheduler not available"}, status=500)
    try:
        job = _scheduler.get_job("nexus_calibration_job")
        if not job:
            return web.json_response({"status": "ok", "paused": True})
        is_paused = job.next_run_time is None
        return web.json_response({"status": "ok", "paused": is_paused})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_calibration_toggle_schedule(request):
    if not _scheduler:
        return web.json_response({"error": "Scheduler not available"}, status=500)
    try:
        body = await request.json()
        action = body.get("action") # "pause" or "resume"
        if action == "pause":
            _scheduler.pause_job("nexus_calibration_job")
            return web.json_response({"status": "paused"})
        elif action == "resume":
            _scheduler.resume_job("nexus_calibration_job")
            return web.json_response({"status": "resumed"})
        else:
            return web.json_response({"error": "Invalid action"}, status=400)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

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
        from web.data_provider import save_global_virtual_stake
        if "global_virtual_stake" in body:
            from agents.shared.python.db import get_connection
            with get_connection() as conn:
                save_global_virtual_stake(conn, float(body["global_virtual_stake"]))
        await asyncio.to_thread(buy_virtual_penny_stock, market_id, price)
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.exception("Error in api_buy_penny_stock")
        return web.json_response({"error": f"Internal server error: {e}"}, status=500)

async def api_get_notification_settings(request):
    from agents.shared.python.db import get_notification_settings
    settings = await asyncio.to_thread(get_notification_settings)
    return web.json_response(settings)

async def api_save_notification_settings(request):
    try:
        body = await request.json()
        from agents.shared.python.db import save_notification_setting
        if "notify_trend_hunter" in body:
            await asyncio.to_thread(save_notification_setting, "notify_trend_hunter", bool(body["notify_trend_hunter"]))
        if "notify_penny_stocks" in body:
            await asyncio.to_thread(save_notification_setting, "notify_penny_stocks", bool(body["notify_penny_stocks"]))
        if "notify_favourite_compounding" in body:
            await asyncio.to_thread(save_notification_setting, "notify_favourite_compounding", bool(body["notify_favourite_compounding"]))
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.error(f"Error saving notification settings: {e}")
        return web.json_response({"error": str(e)}, status=500)

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
                    """Empty callback."""
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
                
            close_time_str = None
            if m.close_time:
                close_time_str = m.close_time.strftime("%Y-%m-%d %H:%M:%S")

            # Добавляем в мониторинг как неанализированный
            await asyncio.to_thread(
                add_penny_stock_to_monitoring,
                market_id=m.id,
                title=m.title,
                url=m.url,
                initial_price=m.price,
                predicted_outcome=None,
                close_time=close_time_str
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

async def api_get_whale_settings(request):
    try:
        from agents.shared.python.db import get_whale_settings
        settings = await asyncio.to_thread(get_whale_settings)
        return web.json_response(settings)
    except Exception as e:
        logger.exception("Error in api_get_whale_settings")
        return web.json_response({"error": str(e)}, status=500)

async def api_set_whale_settings(request):
    try:
        data = await request.json()
        from agents.shared.python.db import update_whale_settings
        await asyncio.to_thread(update_whale_settings, data)
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.exception("Error in api_set_whale_settings")
        return web.json_response({"error": str(e)}, status=500)

async def api_edit_whale_stake(request):
    try:
        data = await request.json()
        market_id = data.get('market_id')
        virtual_bought_price = data.get('virtual_bought_price')
        bet_size_usdc = data.get('bet_size_usdc')
        
        if not all(x is not None for x in (market_id, virtual_bought_price, bet_size_usdc)):
            return web.json_response({"error": "Missing required fields"}, status=400)
            
        from agents.shared.python.db import update_whale_stake
        rows_updated = await asyncio.to_thread(update_whale_stake, market_id, virtual_bought_price, bet_size_usdc)
        if rows_updated == 0:
            return web.json_response({"error": "Market not found or already closed"}, status=404)
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.exception("Error in api_edit_whale_stake")
        return web.json_response({"error": str(e)}, status=500)

async def api_whale_stocks(request):
    active_page = get_int_query(request, "active_page", 1)
    active_limit = get_int_query(request, "active_limit", WHALE_DASHBOARD_CONFIG.default_active_limit)
    wins_page = get_int_query(request, "wins_page", 1)
    wins_limit = get_int_query(request, "wins_limit", WHALE_DASHBOARD_CONFIG.default_wins_limit)
    losses_page = get_int_query(request, "losses_page", 1)
    losses_limit = get_int_query(request, "losses_limit", WHALE_DASHBOARD_CONFIG.default_losses_limit)
    whales_page = get_int_query(request, "whales_page", 1)
    whales_limit = get_int_query(request, "whales_limit", WHALE_DASHBOARD_CONFIG.default_whales_limit)
    whales_sort_by = request.query.get("whales_sort_by", "total_vol")
    whales_sort_dir = request.query.get("whales_sort_dir", "desc")
    
    data = await asyncio.to_thread(
        data_provider.get_whale_stocks_dashboard,
        active_page=active_page,
        active_limit=active_limit,
        wins_page=wins_page,
        wins_limit=wins_limit,
        losses_page=losses_page,
        losses_limit=losses_limit,
        whales_page=whales_page,
        whales_limit=whales_limit,
        whales_sort_by=whales_sort_by,
        whales_sort_dir=whales_sort_dir
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

_whale_scan_in_progress = False

async def background_whale_scan():
    global _whale_scan_in_progress
    try:
        from services.onchain_trend_alert import scan_volume_spikes, scan_large_single_bets, scan_wallet_series
        from telegram.bot import bot, AUTHORIZED_CHAT_ID
        
        logger.info("[Dashboard] Starting background whale scan...")
        spikes = await asyncio.to_thread(scan_volume_spikes) or []
        bets = await asyncio.to_thread(scan_large_single_bets) or []
        series = await asyncio.to_thread(scan_wallet_series) or []
        
        total_discovered = len(spikes) + len(bets) + len(series)
        logger.info(f"[Dashboard] Background whale scan finished. Discovered: {total_discovered}")
        
        msg = (
            f"🐳 <b>Принудительный поиск Whale Following завершен</b>\n\n"
            f"С дашборда был запрошен ручной поиск активности китов.\n"
            f"Обнаружено сигналов: <b>{total_discovered}</b>\n"
            f"• Всплески объема: {len(spikes)}\n"
            f"• Крупные одиночные ставки: {len(bets)}\n"
            f"• Серии сделок: {len(series)}"
        )
        try:
            await bot.send_message(AUTHORIZED_CHAT_ID, msg, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as tg_err:
            logger.error(f"Failed to send Telegram notification: {tg_err}")
            
    except Exception as e:
        logger.exception("Error in background_whale_scan")
    finally:
        _whale_scan_in_progress = False

async def api_discover_whale_stocks(request):
    global _whale_scan_in_progress
    try:
        if _whale_scan_in_progress:
            return web.json_response({"status": "processing", "message": "Сканирование уже выполняется в фоновом режиме."})
            
        _whale_scan_in_progress = True
        asyncio.create_task(background_whale_scan())
        return web.json_response({"status": "started"})
        
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
            
        if hasattr(engine, '_fetch_pre_orderbook'):
            pre_orderbook = engine._fetch_pre_orderbook(market)
        else:
            logger.warning(
                f"[AnalysisBg] engine._fetch_pre_orderbook not available, "
                f"running analysis without pre_orderbook for {market.id}"
            )
            pre_orderbook = None
        
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
                conn.execute("DELETE FROM signals WHERE market_id = ? AND status = 'PENDING'", (market_id,))
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
        
    from agents.shared.python.db import get_last_analyzed_price
    last_price = await asyncio.to_thread(get_last_analyzed_price, market_id)
    if last_price is not None and not force:
        from datetime import datetime, timezone
        dummy_opinion = [{
            "agent_name": "Система",
            "message": "Рынок был проанализирован, но пропущен (не прошел первичные фильтры: ROI, ликвидность или вероятность). Детальный анализ агентами не проводился.",
            "created_at": datetime.now(timezone.utc).isoformat()
        }]
        return web.json_response({"status": "completed", "opinions": dummy_opinion})
        
    if _analysis_queue is None:
        return web.json_response({"error": "Сервер ещё инициализируется, попробуйте позже."}, status=503)
    try:
        from agents.shared.python.db import get_connection
        def clear_zombies():
            with get_connection() as conn:
                conn.execute("DELETE FROM signals WHERE market_id = ? AND status = 'PENDING'", (market_id,))
        await asyncio.to_thread(clear_zombies)
        
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

async def api_get_settings(request):
    from agents.shared.python.db import get_connection
    from web.data_provider import get_global_virtual_stake
    with get_connection() as conn:
        stake = get_global_virtual_stake(conn)
    return web.json_response({
        "global_virtual_stake": stake,
        "polymarket_fee_pct": 2.0
    })

async def api_save_settings(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
        
    stake_val = body.get("global_virtual_stake")
    if stake_val is None:
        return web.json_response({"error": "Missing global_virtual_stake"}, status=400)
    try:
        stake_float = float(stake_val)
        if not (0 < stake_float <= 100_000):
            return web.json_response({"error": "Stake must be between 0 and 100000"}, status=400)
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid stake value"}, status=400)
        
    from agents.shared.python.db import get_connection
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO memory (key, value) VALUES ('global_virtual_stake', ?)",
            (str(stake_float),)
        )
    return web.json_response({"status": "ok"})

async def api_get_compound_settings(request):
    from agents.shared.python.db import get_compound_settings
    settings = get_compound_settings()
    return web.json_response(settings)

async def api_save_compound_settings(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
        
    from agents.shared.python.db import save_compound_setting
    
    # Validation logic
    for k, v in body.items():
        if v is None:
            continue
            
        if k == "min_price":
            try:
                val = float(v)
                if 0.01 <= val <= 0.99:
                    save_compound_setting(k, str(val))
            except ValueError: pass
        elif k == "min_volume":
            try:
                val = float(v)
                if val >= 0:
                    save_compound_setting(k, str(val))
            except ValueError: pass
        elif k == "max_hours":
            try:
                val = int(v)
                if val >= 0:
                    save_compound_setting(k, str(val))
            except ValueError: pass
        elif k == "virtual_stake":
            try:
                val = int(v)
                if val >= 0:
                    save_compound_setting(k, str(val))
            except ValueError: pass
        elif k == "min_confidence":
            try:
                val = float(v)
                if 0.0 <= val <= 1.0:
                    save_compound_setting(k, str(val))
            except ValueError: pass
        elif k == "max_concurrent_chains":
            try:
                val = int(v)
                if val >= 1:
                    save_compound_setting(k, str(val))
            except ValueError: pass
        elif k == "chain_length":
            try:
                val = int(v)
                if val >= 2:
                    save_compound_setting(k, str(val))
            except ValueError: pass
        elif k == "enabled":
            save_compound_setting(k, "1" if v else "0")
            
    return web.json_response({"status": "ok"})

async def api_get_scout_settings(request):
    from agents.shared.python.db import get_scout_settings
    settings = get_scout_settings()
    return web.json_response(settings)

async def api_save_scout_settings(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
        
    from agents.shared.python.db import save_scout_setting
    for k, v in body.items():
        if v is None:
            continue
        try:
            val = float(v)
            save_scout_setting(k, str(val))
        except ValueError:
            pass
            
    return web.json_response({"status": "ok"})

# === Penny Stocks Settings хэндлеры ===

async def handle_penny_stocks_settings(request):
    html = await asyncio.to_thread(render_template, "penny_stocks_settings.html")
    return web.Response(text=html, content_type="text/html")

async def api_penny_stocks_config_get(request):
    from agents.shared.python.penny_settings_service import load_penny_config
    data = await asyncio.to_thread(load_penny_config)
    return web.json_response(data)

async def api_penny_stocks_config_reset(request):
    from agents.shared.python.penny_settings_service import reset_penny_config_to_defaults, load_penny_config
    await asyncio.to_thread(reset_penny_config_to_defaults)
    data = await asyncio.to_thread(load_penny_config)
    return web.json_response(data)

async def api_penny_stocks_config_update(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    
    from agents.shared.python.penny_settings_service import save_penny_config
    try:
        data = await asyncio.to_thread(save_penny_config, body)
        return web.json_response(data)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:
        return web.json_response({"error": f"Internal error: {e}"}, status=500)

async def api_penny_stocks_preflight(request):
    from agents.shared.python.penny_settings_service import run_penny_preflight
    data = await asyncio.to_thread(run_penny_preflight)
    return web.json_response(data)

async def api_penny_stocks_rederive_creds(request):
    from agents.shared.python.penny_settings_service import rederive_penny_credentials
    data = await asyncio.to_thread(rederive_penny_credentials)
    return web.json_response(data)

async def api_system_status(request):
    from agents.shared.python.db import is_system_paused
    paused = await asyncio.to_thread(is_system_paused)
    return web.json_response({"paused": paused})

async def api_system_pause(request):
    from agents.shared.python.db import set_system_paused
    await asyncio.to_thread(set_system_paused, True)
    if _scheduler:
        _scheduler.pause()
    return web.json_response({"status": "paused"})

async def api_system_resume(request):
    from agents.shared.python.db import set_system_paused
    await asyncio.to_thread(set_system_paused, False)
    if _scheduler:
        _scheduler.resume()
    return web.json_response({"status": "active"})

# === Фабрика приложения ===

def create_dashboard_app() -> web.Application:
    """Создает и настраивает инстанс aiohttp приложения."""
    app = web.Application()
    
    # HTML маршруты
    app.router.add_get("/favicon.ico", handle_favicon)
    app.router.add_get("/favicon.png", handle_favicon)
    app.router.add_get("/", handle_overview)
    app.router.add_get("/penny-stocks", handle_penny_stocks)
    app.router.add_get("/penny-settings", handle_penny_stocks_settings)
    app.router.add_get("/favourite-compounding", handle_favourite_compounding)
    app.router.add_get("/scout", handle_scout)
    app.router.add_get("/whale", handle_whale)
    app.router.add_get("/corridors", handle_corridors)
    app.router.add_get("/calibration", handle_calibration)
    app.router.add_get("/learning", handle_learning)
    
    # JSON API маршруты
    app.router.add_get("/api/system/status", api_system_status)
    app.router.add_post("/api/system/pause", api_system_pause)
    app.router.add_post("/api/system/resume", api_system_resume)
    app.router.add_get("/api/overview", api_overview)
    app.router.add_get("/api/system-status", api_system_status_get)
    app.router.add_post("/api/system-status", api_system_status_post)
    app.router.add_get("/api/memory-stats", api_memory_stats)
    app.router.add_get("/api/eval-status", api_eval_status)
    app.router.add_get("/api/learning-impact", api_learning_impact)
    app.router.add_get("/api/agent-performance", api_agent_performance)
    app.router.add_get("/api/penny-stocks", api_penny_stocks)
    app.router.add_get("/api/favourite-compounding", api_favourite_compounding)
    app.router.add_get("/api/whale-stocks", api_whale_stocks)
    app.router.add_get("/api/whale-radar", api_whale_radar)
    app.router.add_post("/api/whale-radar/sync", api_whale_radar_sync)
    app.router.add_get("/api/whale-settings", api_get_whale_settings)
    app.router.add_post("/api/whale-settings", api_set_whale_settings)
    app.router.add_post("/api/whale-settings/edit-stake", api_edit_whale_stake)
    app.router.add_get("/api/equity-curve", api_equity_curve)
    app.router.add_get("/api/signals", api_signals)
    app.router.add_get("/api/corridors", api_corridors)
    
    # POST API маршруты (виртуальный портфель и ручной анализ)
    app.router.add_post("/api/penny-stocks/buy", require_system_active(api_buy_penny_stock))
    app.router.add_post("/api/penny-stocks/sell", require_system_active(api_sell_penny_stock))
    app.router.add_post("/api/penny-stocks/analyze", require_system_active(api_analyze_market))
    app.router.add_get("/api/penny-stocks/analyze-status", api_analyze_market_status)
    app.router.add_post("/api/penny-stocks/discover", require_system_active(api_discover_penny_stocks))
    app.router.add_post("/api/favourite-compounding/buy", require_system_active(api_buy_compound_opportunity))
    app.router.add_post("/api/favourite-compounding/sell", require_system_active(api_sell_compound_opportunity))
    app.router.add_post("/api/whale-stocks/buy", require_system_active(api_buy_whale_stock))
    app.router.add_post("/api/whale-stocks/sell", require_system_active(api_sell_whale_stock))
    app.router.add_post("/api/whale-stocks/analyze", require_system_active(api_analyze_market))
    app.router.add_get("/api/whale-stocks/analyze-status", api_analyze_market_status)
    app.router.add_post("/api/whale-stocks/discover", require_system_active(api_discover_whale_stocks))
    app.router.add_post("/api/delete-market", require_system_active(api_delete_market))
    app.router.add_get("/api/settings", api_get_settings)
    app.router.add_post("/api/settings", api_save_settings)
    
    app.router.add_get("/api/notification-settings", api_get_notification_settings)
    app.router.add_post("/api/notification-settings", api_save_notification_settings)
    app.router.add_get("/api/calibration/runs", api_calibration_runs)
    app.router.add_get("/api/calibration/pending", api_calibration_pending)
    app.router.add_get("/api/calibration/history", api_calibration_history)
    app.router.add_get("/api/calibration/overlays", api_calibration_overlays)
    app.router.add_get("/api/calibration/schedule_status", api_calibration_schedule_status)
    app.router.add_post("/api/calibration/approve", api_calibration_approve)
    app.router.add_post("/api/calibration/reject", api_calibration_reject)
    app.router.add_post("/api/calibration/force_run", api_calibration_force_run)
    app.router.add_post("/api/calibration/toggle_schedule", api_calibration_toggle_schedule)
    
    app.router.add_get("/api/compound-settings", api_get_compound_settings)
    app.router.add_post("/api/compound-settings", api_save_compound_settings)

    app.router.add_get("/api/scout-settings", api_get_scout_settings)
    app.router.add_post("/api/scout-settings", api_save_scout_settings)

    # Penny Stocks Settings API
    app.router.add_get("/api/penny-stocks/config", api_penny_stocks_config_get)
    app.router.add_post("/api/penny-stocks/config", api_penny_stocks_config_update)
    app.router.add_post("/api/penny-stocks/config/reset", api_penny_stocks_config_reset)
    app.router.add_get("/api/penny-stocks/preflight", api_penny_stocks_preflight)
    app.router.add_post("/api/penny-stocks/rederive-creds", api_penny_stocks_rederive_creds)

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
