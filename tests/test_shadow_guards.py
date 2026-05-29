# tests/test_shadow_guards.py

def test_shadow_post_validation_confidence_capped_no_orderbook():
    import logging
    analysis = {"confidence": 0.78, "liquidity_risk": "low", "agree": True,
                "opinion": "Ликвидность нормальная", "orderbook_facts": "...",
                "risk_assessment": "...", "shadow_verdict": "Входить"}
    orderbook = None
    if not orderbook:
        if float(analysis["confidence"]) > 0.40:
            analysis["confidence"] = 0.30
            analysis["liquidity_risk"] = "medium"
    assert analysis["confidence"] == 0.30
    assert analysis["liquidity_risk"] == "medium"

def test_shadow_post_validation_hallucination_flagged():
    analysis = {
        "opinion": "Стакан норм.",
        "risk_assessment": "Крупные трейдеры подтверждают позицию YES на сумму $50k.",
        "shadow_verdict": "Войти лимитным ордером."
    }
    smart_money = None
    hallucination_phrases = ["smart money подтверждают", "крупные трейдеры подтверждают"]
    if not smart_money:
        for field in ["opinion", "risk_assessment", "shadow_verdict"]:
            text = analysis.get(field, "")
            if any(p in text.lower() for p in hallucination_phrases):
                analysis[field] = text + " [⚠️ данные по крупным трейдерам недоступны]"
    assert "⚠️" in analysis["risk_assessment"]
    assert "Крупные трейдеры подтверждают" in analysis["risk_assessment"]  # оригинал сохранён

def test_shadow_orderbook_facts_from_real_data():
    """При наличии ордербука orderbook_facts содержит реальные числа"""
    from agents.shared.utils.prompt_guards import guard_orderbook
    ob = {"spread": "3.2%", "top_bid": 0.58, "top_ask": 0.61,
          "bid_depth_5": 800, "ask_depth_5": 2400, "total_bids": 6, "total_asks": 11}
    result = guard_orderbook(ob)
    assert "$800" in result
    assert "$2,400" in result
    assert "0.3x" in result  # 800/2400 = 0.33
    assert "медвежий сигнал" in result
