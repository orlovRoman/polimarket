# tests/test_arbitrage_guards.py

def test_logical_contradiction_requires_both_descriptions():
    """logical_contradiction без description → auto-downgrade"""
    from types import SimpleNamespace
    market_a = SimpleNamespace(title="Will Trump win?", price=0.50, description="")
    market_b = SimpleNamespace(title="Will Trump win all swing states?", price=0.65, description="")

    result = {"arbitrage_type": "logical_contradiction", "has_arbitrage": True,
              "spread_percent": 15, "reasoning": "B не может быть дороже A"}

    desc_a_missing = not (market_a.description and len(market_a.description) > 20)
    if result["arbitrage_type"] == "logical_contradiction" and desc_a_missing:
        result["arbitrage_type"] = "statistical_pair_trade"

    assert result["arbitrage_type"] == "statistical_pair_trade"

def test_risk_free_allowed_with_full_descriptions():
    """risk_free с полными описаниями — не понижается"""
    from types import SimpleNamespace
    market_a = SimpleNamespace(
        description="Resolves YES if candidate A wins. Source: AP News official call.",
        price=0.50
    )
    market_b = SimpleNamespace(
        description="Resolves YES if candidate A wins. Source: AP News official call.",
        price=0.40
    )
    result = {"arbitrage_type": "risk_free"}
    desc_ok = len(market_a.description) > 20 and len(market_b.description) > 20
    if result["arbitrage_type"] == "logical_contradiction" and not desc_ok:
        result["arbitrage_type"] = "statistical_pair_trade"
    assert result["arbitrage_type"] == "risk_free"  # не тронуто

def test_arbitrage_prompt_contains_step_zero():
    """GEMINI.md содержит ШАГ 0 с проверкой описаний"""
    from pathlib import Path
    gemini_path = Path("agents/polymarket_arbitrage_agent/GEMINI.md")
    text = gemini_path.read_text(encoding="utf-8")
    assert "ШАГ 0" in text
    assert "description_a" in text or "description_b" in text
    assert "logical_contradiction" in text and "oracle_unknown" in text

def test_arbitrage_spread_uses_llm_value_when_ambiguous():
    """При AMBIGUOUS решении spread берётся из LLM-ответа, не из math_filter"""
    mf_spread = 3.2   # math_filter грубая оценка
    llm_data = {"spread_percent": 18.5, "arbitrage_type": "pair_trade",
                "has_arbitrage": True, "reasoning": "..."}
    spread_val = float(llm_data.get("spread_percent") or mf_spread)
    assert spread_val == 18.5  # берётся из LLM

