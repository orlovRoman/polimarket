import re
import json
from difflib import SequenceMatcher
from pathlib import Path
from core.models import Market

STOPWORDS = {
    "will", "the", "a", "an", "in", "of", "to", "be", "is", "are",
    "who", "what", "when", "does", "can", "win", "get", "have",
    "by", "at", "or", "if", "for", "and", "not", "on", "with",
}

MANUAL_PAIRS_PATH = Path("config/manual_market_pairs.json")


def normalize(title: str) -> str:
    """Убирает ценовые тэги, стоп-слова и знаки препинания."""
    title = re.sub(r'\([^)]*¢[^)]*\)', '', title)   # "(YES: 65¢ | NO: 35¢)"
    title = re.sub(r'[^\w\s]', '', title.lower())
    return " ".join(w for w in title.split() if w not in STOPWORDS)


def keyword_match_score(a: str, b: str) -> float:
    """Схожесть двух нормализованных строк через SequenceMatcher."""
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
    Keyword-матчинг: возвращает список (market_a, market_b, score),
    отсортированный по убыванию score.

    :param markets_a: Рынки первой платформы (обычно Polymarket)
    :param markets_b: Рынки второй платформы (обычно Kalshi)
    :param min_score: Минимальный порог схожести названий (0–1)
    :param max_days_diff: Максимальная разница в датах закрытия (дни)
    """
    pairs = []
    index_a = [(m, normalize(m.title)) for m in markets_a]
    index_b = [(m, normalize(m.title)) for m in markets_b]

    for ma, norm_a in index_a:
        for mb, norm_b in index_b:
            # Фильтр по дате закрытия
            days_diff = abs((ma.close_time - mb.close_time).days)
            if days_diff > max_days_diff:
                continue

            score = keyword_match_score(norm_a, norm_b)
            if score >= min_score:
                pairs.append((ma, mb, score))

    return sorted(pairs, key=lambda x: -x[2])


def verify_pair_with_llm(
    market_a: Market,
    market_b: Market,
    api_key: str,
) -> dict:
    """
    Gemini проверяет: это один и тот же вопрос?
    Вызывается только для пар в «серой зоне» (score 0.50–0.72).

    :return: {"is_same_event": bool, "confidence": float, "reason": str}
    """
    from agents.shared.utils.gemini_client import generate_content_with_fallback, extract_response_text
    import json as _json

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
        return _json.loads(text.strip())
    except Exception as e:
        print(f"[MarketMatcher] Ошибка парсинга LLM-ответа: {e}")
        return {"is_same_event": False, "confidence": 0.0, "reason": str(e)}
