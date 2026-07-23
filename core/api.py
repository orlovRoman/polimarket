from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Dict, Any

from core.engine import CoreEngine

app = FastAPI(title="Polymarket Nexus API")
engine = CoreEngine()

class AnalyzeRequest(BaseModel):
    post_id: int
    chat_id: str
    source_chat_id: str = ""
    source_username: str | None = None
    source_message_id: int | None = None
    source_url: str | None = None
    source_text: str | None = None
@app.get("/")
def root_index():
    return {
        "status": "online",
        "service": "Polymarket Nexus API",
        "dashboard_url": ":8050",
        "docs_url": "/docs",
        "endpoints": {
            "status": "/api/status",
            "markets": "/api/markets",
            "docs": "/docs"
        }
    }
@app.get("/api/status")
def get_status() -> Dict[str, Any]:
    return engine.get_status()

@app.get("/api/markets")
def get_markets() -> Dict[str, Any]:
    return engine.get_active_markets()

@app.post("/api/analyze/{post_id}")
def analyze_post(post_id: int, request: AnalyzeRequest, background_tasks: BackgroundTasks):
    if str(post_id) != str(request.post_id):
        raise HTTPException(status_code=400, detail="Path post_id and body post_id mismatch")
    
    background_tasks.add_task(
        engine.analyze_post_async, 
        request.post_id, 
        request.chat_id,
        source_username=request.source_username,
        source_message_id=request.source_message_id,
        source_url=request.source_url,
        source_text=request.source_text
    )
    return {"status": "Analysis started in background", "post_id": post_id}
