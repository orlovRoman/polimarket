import re
import json
from difflib import SequenceMatcher
from pathlib import Path
from core.models import Market
from agents.shared.utils.gemini_client import generate_content_with_fallback, extract_response_text
from core.math_filter import math_pre_filter, FilterDecision

# SMELL-15: Expanded STOPWORDS
STOPWORDS = {
    "will", "the", "a", "an", "in", "of", "to", "be", "is", "are",
    "who", "what", "when", "does", "can", "win", "get", "have",
    "by", "at", "or", "if", "for", "and", "not", "on", "with",
    "going", "fed", "happen", "occur", "year", "month", "end",
}

MANUAL_PAIRS_PATH = Path("config/manual_market_pairs.json")

def normalize(title: str) -> set:
    """Убирает ценовые тэги, знаки препинания и возвращает множество слов для быстрого пересечения."""
    title = re.sub(r'\([^)]*¢[^)]*\)', '', title)   # "(YES: 65¢ | NO: 35¢)"
    title = re.sub(r'[^\w\s]', '', title.lower())
    return set(w for w in title.split() if w not in STOPWORDS)

def keyword_match_score(a: str, b: str) -> float:
    """Схожесть двух строк через SequenceMatcher."""
    if not a or not b:
        return 0.0
    return round(SequenceMatcher(None, a, b).ratio(), 3)

def load_manual_pairs() -> list[dict]:
    """
    Загружает вручную заданные пары рынков из конфига.
    Формат: [{"poly_id": "...", "kalshi_id": "...", "note": "..."}]
    """
    if not MANUAL_PAIRS_PATH.exists():
        return []
    try:
        return json.loads(MANUAL_PAIRS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[MarketMatcher] Ошибка чтения manual_market_pairs.json: {e}")
        return []

def find_candidate_pairs(
    markets_a: list[Market],
    markets_b: list[Market],
    min_score: float = 0.50,
    max_days_diff: int = 21,
) -> list[tuple[Market, Market, float]]:
    """
    Keyword-матчинг: возвращает список (market_a, market_b, score).
    Использует быстрое вычисление Jaccard similarity перед дорогим SequenceMatcher (RISK-05).
    """
    pairs = []
    
    # Pre-compute token sets for all markets
    index_a = [(m, normalize(m.title), m.title.lower()) for m in markets_a]
    index_b = [(m, normalize(m.title), m.title.lower()) for m in markets_b]

    for ma, set_a, str_a in index_a:
        if not set_a: continue
        for mb, set_b, str_b in index_b:
            if not set_b: continue
            
            # Фильтр по дате закрытия
            days_diff = abs((ma.close_time - mb.close_time).days)
            if days_diff > max_days_diff:
                continue

            # Jaccard similarity for rapid pre-filtering
            intersection = len(set_a & set_b)
            union = len(set_a | set_b)
            jaccard = intersection / union if union > 0 else 0
            
            # If jaccard is very low, they have almost no words in common. Skip SequenceMatcher.
            if jaccard < 0.15:
                continue

            score = keyword_match_score(str_a, str_b)
            if score >= min_score:
                # Math pre-filter для same-platform пар с threshold-логикой
                # Кросс-платформенные пары не фильтруем здесь — у агента больше контекста
                if ma.platform == mb.platform:
                    mf = math_pre_filter(ma, mb)
                    if mf.decision == FilterDecision.CONFIRMED_NO_ARBI:
                        continue  # Отсекаем до LLM-верификации
                pairs.append((ma, mb, score))

    return sorted(pairs, key=lambda x: -x[2])

def verify_pair_with_llm(
    market_a: Market,
    market_b: Market,
    api_key: str,
) -> dict:
    """
    Gemini проверяет: это один и тот же вопрос?
    Вызывается только для пар в «серой зоне».
    """
    prompt = f"""Два рынка предсказаний с разных платформ. Они об одном и том же событии?

Рынок A ({market_a.platform.upper()}):
  Название: {market_a.title}
  Описание: {(market_a.description or '')[:300]}
  Закрытие: {market_a.close_time.strftime('%Y-%m-%d')}

Рынок B ({market_b.platform.upper()}):
  Название: {market_b.title}
  Описание: {(market_b.description or '')[:300]}
  Закрытие: {market_b.close_time.strftime('%Y-%m-%d')}

Критерий: оба рынка резолвируются ОДИНАКОВО при одном исходе события.
Отвечай строго JSON."""

    schema = {
        "type": "OBJECT",
        "properties": {
            "is_same_event": {"type": "BOOLEAN"},
            "confidence": {"type": "NUMBER"},
            "reason": {"type": "STRING"},
        },
        "required": ["is_same_event", "confidence", "reason"],
    }

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "temperature": 0.1,
        },
    }

    result, _ = generate_content_with_fallback(
        api_key=api_key, payload=payload,
        default_model="gemini-2.5-flash", agent_name="MATCHER"
    )

    if not result:
        return {"is_same_event": False, "confidence": 0.0, "reason": "LLM error"}

    try:
        text = extract_response_text(result)
        return json.loads(text.strip())
    except Exception as e:
        print(f"[MarketMatcher] Ошибка парсинга LLM-ответа: {e}")
        return {"is_same_event": False, "confidence": 0.0, "reason": str(e)}
