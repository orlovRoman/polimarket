# web/dashboard.py
import asyncio
import logging
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

async def handle_overview(request):
    html = await asyncio.to_thread(render_template, "overview.html")
    return web.Response(text=html, content_type="text/html")

async def handle_penny_stocks(request):
    html = await asyncio.to_thread(render_template, "penny_stocks.html")
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

async def api_penny_stocks(request):
    data = await asyncio.to_thread(data_provider.get_penny_stocks_dashboard)
    return web.json_response(data)

async def api_equity_curve(request):
    strategy = request.query.get("strategy", "all")
    days_str = request.query.get("days", "30")
    try:
        days = int(days_str)
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
    try:
        days = int(days_str)
    except ValueError:
        days = 30
    try:
        limit = int(limit_str)
    except ValueError:
        limit = 50
    data = await asyncio.to_thread(data_provider.get_strategy_signals, strategy, days, limit)
    return web.json_response(data)

async def api_corridors(request):
    data = await asyncio.to_thread(data_provider.get_corridors_dashboard)
    return web.json_response(data)

async def api_buy_penny_stock(request):
    try:
        body = await request.json()
        market_id = body.get("market_id")
        price_str = body.get("price")
        if not market_id or price_str is None:
            return web.json_response({"error": "market_id and price are required"}, status=400)
        price = float(price_str)
    except Exception as e:
        return web.json_response({"error": f"Invalid request body: {e}"}, status=400)
        
    from agents.shared.python.db import buy_virtual_penny_stock
    await asyncio.to_thread(buy_virtual_penny_stock, market_id, price)
    return web.json_response({"status": "ok"})

async def api_sell_penny_stock(request):
    try:
        body = await request.json()
        market_id = body.get("market_id")
        if not market_id:
            return web.json_response({"error": "market_id is required"}, status=400)
    except Exception as e:
        return web.json_response({"error": f"Invalid request body: {e}"}, status=400)
        
    from agents.shared.python.db import sell_virtual_penny_stock
    await asyncio.to_thread(sell_virtual_penny_stock, market_id)
    return web.json_response({"status": "ok"})

# === Фоновый запуск анализа ===

analysis_jobs = {}

async def run_analysis_in_background(market_id: str):
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
        analysis_jobs[market_id] = {
            "status": "failed",
            "error": "Рынок не найден ни в локальной БД, ни в адаптере Polymarket."
        }
        return
        
    full_market_id = db_market["id"]
    market_title = db_market["title"]
    
    try:
        # Инициализируем статус
        analysis_jobs[market_id] = {
            "status": "running",
            "progress": {
                "scout": "⚙️ Анализирует...",
                "swing": "⏳ Ожидает",
                "shadow": "⏳ Ожидает"
            }
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
            
            job = analysis_jobs.get(market_id)
            if job and job.get("status") == "running":
                progress = job.setdefault("progress", {})
                if scout_status:
                    progress["scout"] = scout_status
                if swing_status:
                    progress["swing"] = swing_status
                if shadow_status:
                    progress["shadow"] = shadow_status
                    
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
            analysis_jobs[market_id] = {
                "status": "failed",
                "error": "Анализ рынка пропущен (дедупликация или сбой)."
            }
            return
            
        sync_update_state(shadow_status="⚙️ Анализирует...")
        opinion_shadow = engine._run_shadow_analysis(
            m=market,
            active_signal=signal or swing_signal,
            signal=signal,
            swing_signal=swing_signal,
            context=context,
            price_hist=price_hist,
            _update_state=sync_update_state,
            log=logger.info
        )
        
        if opinion_shadow:
            sh_status = "✅ Согласен" if opinion_shadow.agree else "❌ Против"
            sync_update_state(shadow_status=f"{sh_status} (Увер: {opinion_shadow.confidence})")
        else:
            sync_update_state(shadow_status="⚪️ Нет мнения")
            
        process_consensus(
            context, signal, swing_signal, opinion_shadow,
            engine.state, sync_update_state, None,
            api_key=engine.api_key
        )
        
        await asyncio.to_thread(mark_market_analyzed, market.id, market.price)
        
        opinions = await asyncio.to_thread(get_market_discussions, market.id)
        
        analysis_jobs[market_id] = {
            "status": "completed",
            "opinions": opinions
        }
        
    except Exception as e:
        logger.error(f"Error in background dashboard analysis for {market_id}: {e}", exc_info=True)
        analysis_jobs[market_id] = {
            "status": "failed",
            "error": str(e)
        }

async def api_analyze_penny_stock(request):
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
        from agents.shared.python.db import get_connection
        def clear_db():
            with get_connection() as conn:
                conn.execute("DELETE FROM agent_opinions WHERE market_id = ?", (market_id,))
                conn.execute("DELETE FROM analyzed_markets WHERE market_id = ?", (market_id,))
        await asyncio.to_thread(clear_db)
        if market_id in analysis_jobs:
            del analysis_jobs[market_id]
            
    job = analysis_jobs.get(market_id)
    if job:
        if job["status"] == "completed":
            return web.json_response({"status": "completed", "opinions": job["opinions"]})
        elif job["status"] == "failed":
            del analysis_jobs[market_id]
        else:
            return web.json_response({"status": "running", "progress": job.get("progress")})
            
    opinions = await asyncio.to_thread(get_market_discussions, market_id)
    if opinions and len(opinions) > 0:
        analysis_jobs[market_id] = {
            "status": "completed",
            "opinions": opinions
        }
        return web.json_response({"status": "completed", "opinions": opinions})
        
    asyncio.create_task(run_analysis_in_background(market_id))
    return web.json_response({"status": "running", "progress": {
        "scout": "⏳ Инициализация...",
        "swing": "⏳ Ожидает",
        "shadow": "⏳ Ожидает"
    }})

async def api_analyze_status(request):
    market_id = request.query.get("market_id")
    if not market_id:
        return web.json_response({"error": "market_id is required"}, status=400)
        
    job = analysis_jobs.get(market_id)
    if job:
        if job["status"] == "completed":
            return web.json_response({"status": "completed", "opinions": job["opinions"]})
        elif job["status"] == "failed":
            return web.json_response({"status": "failed", "error": job.get("error")})
        else:
            return web.json_response({"status": "running", "progress": job.get("progress")})
            
    from agents.shared.python.db import get_market_discussions
    opinions = await asyncio.to_thread(get_market_discussions, market_id)
    if opinions and len(opinions) > 0:
        analysis_jobs[market_id] = {
            "status": "completed",
            "opinions": opinions
        }
        return web.json_response({"status": "completed", "opinions": opinions})
        
    return web.json_response({"status": "not_found"})

# === Фабрика приложения ===

def create_dashboard_app() -> web.Application:
    """Создает и настраивает инстанс aiohttp приложения."""
    app = web.Application()
    
    # HTML маршруты
    app.router.add_get("/", handle_overview)
    app.router.add_get("/penny-stocks", handle_penny_stocks)
    app.router.add_get("/scout", handle_scout)
    app.router.add_get("/whale", handle_whale)
    app.router.add_get("/corridors", handle_corridors)
    
    # JSON API маршруты
    app.router.add_get("/api/overview", api_overview)
    app.router.add_get("/api/penny-stocks", api_penny_stocks)
    app.router.add_get("/api/equity-curve", api_equity_curve)
    app.router.add_get("/api/signals", api_signals)
    app.router.add_get("/api/corridors", api_corridors)
    
    # POST API маршруты (виртуальный портфель и ручной анализ)
    app.router.add_post("/api/penny-stocks/buy", api_buy_penny_stock)
    app.router.add_post("/api/penny-stocks/sell", api_sell_penny_stock)
    app.router.add_post("/api/penny-stocks/analyze", api_analyze_penny_stock)
    app.router.add_get("/api/penny-stocks/analyze-status", api_analyze_status)
    
    logger.info("Application routes successfully registered.")
    return app
