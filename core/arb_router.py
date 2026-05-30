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

_MIN_SPREAD_FOR_LLM = 8.0   # ниже — не тратим токены
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

def route_ambiguous(
    mf: MathFilterResult,
    market_a: "Market",
    market_b: "Market",
    api_key: str,
    model: str = "gemini-2.5-flash",
) -> Optional[dict]:
    """
    Для AMBIGUOUS с spread >= _MIN_SPREAD_FOR_LLM:
    задаём LLM один узкий вопрос — одно ли это событие.
    
    Returns dict с ключами: same_event, confidence, reason, confirmed_arb
    или None при ошибке / spread < порога.
    """
    if mf.decision != FilterDecision.AMBIGUOUS:
        return None
    if mf.spread_pct < _MIN_SPREAD_FOR_LLM:
        logger.debug(f"[arb_router] Spread {mf.spread_pct:.1f}% < {_MIN_SPREAD_FOR_LLM}% — пропуск")
        return None

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
            return json.loads(text)
        except json.JSONDecodeError as jde:
            logger.warning(
                f"[arb_router] JSONDecodeError: {jde}. "
                f"Raw text ({len(text)} chars): {text[:300]!r}"
            )
            return None
    except Exception as e:
        logger.warning(f"[arb_router] Ошибка LLM: {e}")
        return None
