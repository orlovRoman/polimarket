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
    
    # POST API маршруты (виртуальный портфель)
    app.router.add_post("/api/penny-stocks/buy", api_buy_penny_stock)
    app.router.add_post("/api/penny-stocks/sell", api_sell_penny_stock)
    
    logger.info("Application routes successfully registered.")
    return app
