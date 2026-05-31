import pytest
import json
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from core.arb_router import route_ambiguous
from core.math_filter import MathFilterResult, FilterDecision
from core.models import Market

def _mkt(id, title, price):
    return Market(id=id, platform="polymarket", title=title,
                  description="", url=f"http://x/{id}", outcome="YES",
                  price=price, close_time=datetime.now(timezone.utc) + timedelta(days=14))

@pytest.fixture(autouse=True)
def clean_llm_cache():
    try:
        from agents.shared.python.db import get_connection
        with get_connection() as conn:
            conn.execute("DELETE FROM arb_llm_cache")
    except Exception:
        pass
    yield
    try:
        from agents.shared.python.db import get_connection
        with get_connection() as conn:
            conn.execute("DELETE FROM arb_llm_cache")
    except Exception:
        pass


def _ambiguous_mf(spread: float) -> MathFilterResult:
    return MathFilterResult(
        decision=FilterDecision.AMBIGUOUS,
        arbitrage_type="price_divergence",
        spread_pct=spread,
        reasoning="тест",
        trade_instruction="",
    )

def _confirmed_arb_mf() -> MathFilterResult:
    return MathFilterResult(
        decision=FilterDecision.CONFIRMED_ARBITRAGE,
        arbitrage_type="complementary_overpriced",
        spread_pct=15.0,
        reasoning="тест",
        trade_instruction="BUY NO",
        has_arbitrage=True,
    )

# --- Пропуск без LLM ---

def test_returns_none_if_not_ambiguous():
    """CONFIRMED_ARBITRAGE не проходит через router."""
    mf = _confirmed_arb_mf()
    result = route_ambiguous(mf, _mkt("not_amb_a","X",0.5), _mkt("not_amb_b","Y",0.4), api_key="key")
    assert result is None

def test_does_not_skip_llm_if_spread_too_low():
    """Маленький спред → LLM ВСЕ РАВНО вызывается, спред лимит отключен."""
    mf = _ambiguous_mf(spread=3.0)
    mock_payload = (MagicMock(), None)
    with patch("core.arb_router.generate_content_with_fallback", return_value=mock_payload) as mock_llm, \
         patch("core.arb_router.extract_response_text", return_value=json.dumps({"same_event": True, "confidence": 0.85, "reason": "Одно событие", "confirmed_arb": True})):
        result = route_ambiguous(mf, _mkt("low_spread_a","X",0.5), _mkt("low_spread_b","Y",0.47), api_key="key")
    mock_llm.assert_called_once()
    assert result is not None

# --- LLM вызывается для больших спредов ---

def test_llm_called_for_large_spread():
    """Спред >= порога → LLM вызывается."""
    mf = _ambiguous_mf(spread=15.0)
    fake_response = {"candidates": [{"content": {"parts": [
        {"text": json.dumps({"same_event": True, "confidence": 0.85,
                             "reason": "Одно событие", "confirmed_arb": True})}
    ]}}]}
    with patch("core.arb_router.generate_content_with_fallback",
               return_value=(fake_response, "gemini-flash")) as mock_llm, \
         patch("core.arb_router.extract_response_text",
               return_value=json.dumps({"same_event": True, "confidence": 0.85,
                                        "reason": "Одно событие", "confirmed_arb": True})):
        result = route_ambiguous(mf, _mkt("large_spread_a","Bitcoin above 100K Dec",0.60),
                                     _mkt("large_spread_b","Bitcoin above 80K Dec",0.47), api_key="key")
    mock_llm.assert_called_once()
    assert result["same_event"] is True
    assert result["confirmed_arb"] is True
    assert "reason" in result

def test_llm_response_parsed_correctly():
    """Ответ LLM корректно десериализуется."""
    mf = _ambiguous_mf(spread=15.0)
    payload_str = json.dumps({
        "same_event": False, "confidence": 0.3,
        "reason": "Разные события", "confirmed_arb": False
    })
    with patch("core.arb_router.generate_content_with_fallback",
               return_value=({"candidates":[{"content":{"parts":[{"text": payload_str}]}}]}, "m")), \
         patch("core.arb_router.extract_response_text",
               return_value=payload_str):
        result = route_ambiguous(mf, _mkt("parsed_a","X",0.5), _mkt("parsed_b","Y",0.35), api_key="key")
    assert result["same_event"] is False
    assert result["confirmed_arb"] is False

def test_returns_none_on_llm_error():
    """LLM-ошибка → None без краша."""
    mf = _ambiguous_mf(spread=20.0)
    with patch("core.arb_router.generate_content_with_fallback",
               side_effect=Exception("network error")):
        result = route_ambiguous(mf, _mkt("err_a","X",0.5), _mkt("err_b","Y",0.30), api_key="key")
    assert result is None

def test_prompt_is_compact(monkeypatch):
    """Промпт < 500 символов — убеждаемся что он маленький."""
    captured = {}
    def fake_generate(api_key, payload, **kwargs):
        captured["prompt"] = payload["contents"][0]["parts"][0]["text"]
        return None, None
    monkeypatch.setattr("core.arb_router.generate_content_with_fallback", fake_generate)
    mf = _ambiguous_mf(spread=15.0)
    route_ambiguous(mf, _mkt("compact_a", "Bitcoin above 100K December 2026", 0.6),
                        _mkt("compact_b", "Bitcoin above 80K December 2026", 0.45), api_key="key")
    assert len(captured.get("prompt", "")) < 600, "Промпт слишком большой!"

def test_llm_cache_ttl_expiry():
    """Тестирует TTL кэша LLM-ответов: записи старше 7 дней должны игнорироваться."""
    from agents.shared.python.db import get_connection
    from core.arb_router import _set_llm_cache
    
    mf = _ambiguous_mf(spread=15.0)
    market_a = _mkt("ttl_a", "Title X", 0.5)
    market_b = _mkt("ttl_b", "Title Y", 0.4)
    
    # 1. Записываем в кэш
    _set_llm_cache(market_a.id, market_b.id, {"same_event": True, "reason": "ok"})
    
    # 2. Имитируем старение записи (8 дней назад - больше TTL = 7 дней)
    pair_key = "_".join(sorted([market_a.id, market_b.id]))
    with get_connection() as conn:
        conn.execute(
            "UPDATE arb_llm_cache SET created_at = datetime('now', '-8 days') WHERE pair_key = ?",
            (pair_key,)
        )
        
    # 3. Вызываем route_ambiguous — должен быть промах кэша и вызов LLM
    mock_payload = (MagicMock(), None)
    with patch("core.arb_router.generate_content_with_fallback", return_value=mock_payload) as mock_llm, \
         patch("core.arb_router.extract_response_text", return_value='{"same_event": true, "confidence": 0.9, "reason": "ok", "confirmed_arb": true}'):
        res = route_ambiguous(mf, market_a, market_b, api_key="test")
        assert res is not None
        mock_llm.assert_called_once()
        
    # 4. Обновляем на 6 дней назад (в пределах TTL = 7 дней)
    with get_connection() as conn:
        conn.execute(
            "UPDATE arb_llm_cache SET created_at = datetime('now', '-6 days') WHERE pair_key = ?",
            (pair_key,)
        )
        
    # 5. Вызываем route_ambiguous — должно быть попадание в кэш без вызова LLM
    with patch("core.arb_router.generate_content_with_fallback") as mock_llm:
        res = route_ambiguous(mf, market_a, market_b, api_key="test")
        assert res is not None
        assert res["same_event"] is True
        mock_llm.assert_not_called()

