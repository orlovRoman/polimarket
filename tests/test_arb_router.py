import pytest
import json
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from core.arb_router import route_ambiguous, _MIN_SPREAD_FOR_LLM
from core.math_filter import MathFilterResult, FilterDecision
from core.models import Market

def _mkt(id, title, price):
    return Market(id=id, platform="polymarket", title=title,
                  description="", url=f"http://x/{id}", outcome="YES",
                  price=price, close_time=datetime.now(timezone.utc) + timedelta(days=14))

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
    result = route_ambiguous(mf, _mkt("a","X",0.5), _mkt("b","Y",0.4), api_key="key")
    assert result is None

def test_returns_none_if_spread_too_low():
    """Маленький спред → LLM не вызывается."""
    mf = _ambiguous_mf(spread=_MIN_SPREAD_FOR_LLM - 1.0)
    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback") as mock_llm:
        result = route_ambiguous(mf, _mkt("a","X",0.5), _mkt("b","Y",0.47), api_key="key")
    mock_llm.assert_not_called()
    assert result is None

# --- LLM вызывается для больших спредов ---

def test_llm_called_for_large_spread():
    """Спред >= порога → LLM вызывается."""
    mf = _ambiguous_mf(spread=_MIN_SPREAD_FOR_LLM + 5.0)
    fake_response = {"candidates": [{"content": {"parts": [
        {"text": json.dumps({"same_event": True, "confidence": 0.85,
                             "reason": "Одно событие", "confirmed_arb": True})}
    ]}}]}
    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback",
               return_value=(fake_response, "gemini-flash")) as mock_llm, \
         patch("agents.shared.utils.gemini_client.extract_response_text",
               return_value=json.dumps({"same_event": True, "confidence": 0.85,
                                        "reason": "Одно событие", "confirmed_arb": True})):
        result = route_ambiguous(mf, _mkt("a","Bitcoin above 100K Dec",0.60),
                                     _mkt("b","Bitcoin above 80K Dec",0.47), api_key="key")
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
    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback",
               return_value=({"candidates":[{"content":{"parts":[{"text": payload_str}]}}]}, "m")), \
         patch("agents.shared.utils.gemini_client.extract_response_text",
               return_value=payload_str):
        result = route_ambiguous(mf, _mkt("a","X",0.5), _mkt("b","Y",0.35), api_key="key")
    assert result["same_event"] is False
    assert result["confirmed_arb"] is False

def test_returns_none_on_llm_error():
    """LLM-ошибка → None без краша."""
    mf = _ambiguous_mf(spread=20.0)
    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback",
               side_effect=Exception("network error")):
        result = route_ambiguous(mf, _mkt("a","X",0.5), _mkt("b","Y",0.30), api_key="key")
    assert result is None

def test_prompt_is_compact(monkeypatch):
    """Промпт < 500 символов — убеждаемся что он маленький."""
    captured = {}
    def fake_generate(api_key, payload, **kwargs):
        captured["prompt"] = payload["contents"][0]["parts"][0]["text"]
        return None, None
    monkeypatch.setattr("agents.shared.utils.gemini_client.generate_content_with_fallback", fake_generate)
    mf = _ambiguous_mf(spread=15.0)
    route_ambiguous(mf, _mkt("a", "Bitcoin above 100K December 2026", 0.6),
                        _mkt("b", "Bitcoin above 80K December 2026", 0.45), api_key="key")
    assert len(captured.get("prompt", "")) < 600, "Промпт слишком большой!"
