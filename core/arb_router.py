"""
Маршрутизатор AMBIGUOUS-результатов math_pre_filter.
Использует минимальный LLM-вызов (~300 токенов) вместо
полного SCOUT-промпта (~8000 токенов).
"""
from __future__ import annotations
import json
import logging
from typing import Optional, TYPE_CHECKING
from core.math_filter import MathFilterResult, FilterDecision

if TYPE_CHECKING:
    from core.models import Market

logger = logging.getLogger("arb_router")

try:
    from agents.shared.utils.gemini_client import (
        generate_content_with_fallback,
        extract_response_text,
    )
    _GEMINI_AVAILABLE = True
except ImportError as e:
    logger.warning(f"[arb_router] gemini_client недоступен: {e}")
    _GEMINI_AVAILABLE = False

_SCHEMA = {
    "type": "object",
    "properties": {
        "same_event":    {"type": "boolean"},
        "confidence":    {"type": "number"},
        "reason":        {"type": "string"},
        "confirmed_arb": {"type": "boolean"},
    },
    "required": ["same_event", "confidence", "reason", "confirmed_arb"]
}

def _init_llm_cache_db():
    from agents.shared.python.db import get_connection
    try:
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS arb_llm_cache (
                    pair_key TEXT PRIMARY KEY,
                    same_event INTEGER NOT NULL,
                    reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_arb_llm_cache_created ON arb_llm_cache(created_at)")
    except Exception as e:
        logger.warning(f"[arb_router] Ошибка инициализации SQLite кэша: {e}")

def _get_llm_cache(market_id_a: str, market_id_b: str) -> Optional[dict]:
    pair_key = "_".join(sorted([market_id_a, market_id_b]))
    try:
        _init_llm_cache_db()
        from agents.shared.python.db import get_connection
        with get_connection() as conn:
            row = conn.execute(
                "SELECT same_event, reason FROM arb_llm_cache WHERE pair_key = ?",
                (pair_key,)
            ).fetchone()
            if row:
                return {
                    "same_event": bool(row[0]),
                    "reason": row[1],
                    "confidence": 1.0
                }
    except Exception as e:
        logger.warning(f"[arb_router] Ошибка чтения LLM-кэша: {e}")
    return None

def _set_llm_cache(market_id_a: str, market_id_b: str, result: dict) -> None:
    pair_key = "_".join(sorted([market_id_a, market_id_b]))
    if "same_event" not in result:
        return
    try:
        _init_llm_cache_db()
        from agents.shared.python.db import get_connection
        with get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO arb_llm_cache (pair_key, same_event, reason, created_at) VALUES (?, ?, ?, datetime('now'))",
                (pair_key, int(result["same_event"]), result.get("reason", ""))
            )
    except Exception as e:
        logger.warning(f"[arb_router] Ошибка записи в LLM-кэш: {e}")

def _calculate_confirmed_arb(mf: MathFilterResult, market_a: Market, market_b: Market, same_event: bool, default_val: bool = False) -> bool:
    """Динамический пересчет подтвержденного арбитража на основе текущих цен."""
    if not same_event:
        return False
        
    if mf.arbitrage_type == "price_divergence":
        current_spread = abs(market_a.price - market_b.price) * 100
        return current_spread >= 5.0
        
    if mf.arbitrage_type == "monotonicity_violation":
        return True
        
    if mf.arbitrage_type == "complementary_underpriced":
        price_sum = market_a.price + market_b.price
        return (1.0 - price_sum) * 100 >= 5.0

    if mf.arbitrage_type == "complementary_overpriced":
        price_sum = market_a.price + market_b.price
        return (price_sum - 1.0) * 100 >= 5.0
        
    if mf.arbitrage_type == "logical_implication":
        return default_val
        
    return default_val

def route_ambiguous(
    mf: MathFilterResult,
    market_a: Market,
    market_b: Market,
    api_key: str,
    model: str = "gemini-2.5-flash",
) -> Optional[dict]:
    """
    Для всех AMBIGUOUS пар:
    проверяем кэш, при промахе задаём LLM один узкий вопрос — одно ли это событие.
    
    Returns dict с ключами: same_event, confidence, reason, confirmed_arb
    или None при ошибке.
    """
    if mf.decision != FilterDecision.AMBIGUOUS:
        return None

    # Пытаемся получить из SQLite кэша
    cached = _get_llm_cache(market_a.id, market_b.id)
    if cached is not None:
        cached["confirmed_arb"] = _calculate_confirmed_arb(
            mf, market_a, market_b, cached["same_event"], default_val=cached["same_event"]
        )
        return cached

    prompt = (
        f"Два рынка Polymarket:\n"
        f"A: \"{market_a.title}\" — цена {market_a.price:.2f}\n"
        f"B: \"{market_b.title}\" — цена {market_b.price:.2f}\n\n"
        f"Math-анализ: тип={mf.arbitrage_type}, спред={mf.spread_pct:.1f}%\n"
        f"Инструкция: {mf.trade_instruction or 'нет'}\n\n"
        f"Вопросы:\n"
        f"1. Описывают ли рынки A и B одно и то же реальное событие "
        f"(same_event=true) или разные (same_event=false)?\n"
        f"2. Если same_event=true — подтверждается ли арбитражная возможность "
        f"(confirmed_arb=true)?\n"
        f"Отвечай строго по схеме JSON. reason — на русском языке, кратко (≤30 слов)."
    )

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _SCHEMA,
            "maxOutputTokens": 200,
        },
    }

    try:
        if not _GEMINI_AVAILABLE:
            logger.warning("[arb_router] gemini_client не импортирован — route_ambiguous отключён")
            return None
        result, _ = generate_content_with_fallback(
            api_key=api_key, payload=payload,
            default_model=model, agent_name="ARB_ROUTER",
            market_id=market_a.id
        )
        if not result:
            return None
        text = extract_response_text(result)
        try:
            res_dict = json.loads(text)
            same_event = res_dict.get("same_event", False)
            confirmed_arb = _calculate_confirmed_arb(
                mf, market_a, market_b, same_event, default_val=res_dict.get("confirmed_arb", False)
            )
            res_dict["confirmed_arb"] = confirmed_arb
            
            # Сохраняем в кэш
            _set_llm_cache(market_a.id, market_b.id, res_dict)
            return res_dict
        except json.JSONDecodeError as jde:
            logger.warning(
                f"[arb_router] JSONDecodeError: {jde}. "
                f"Raw text ({len(text)} chars): {text[:300]!r}"
            )
            return None
    except Exception as e:
        logger.warning(f"[arb_router] Ошибка LLM: {e}")
        return None
