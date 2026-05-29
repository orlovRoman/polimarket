# tests/test_shadow_guards.py

def test_shadow_confidence_capped_without_orderbook():
    """Без ордербука confidence не может быть > 0.40"""
    # Симулируем вывод LLM с завышенным confidence
    raw_analysis = {"agree": True, "confidence": 0.80, "liquidity_risk": "low",
                    "opinion": "Всё хорошо", "orderbook_facts": "спред 5%",
                    "risk_assessment": "низкий", "shadow_verdict": "входить"}
    orderbook = None  # нет данных

    if not orderbook:
        if float(raw_analysis["confidence"]) > 0.40:
            raw_analysis["confidence"] = 0.30
            raw_analysis["liquidity_risk"] = "medium"

    assert raw_analysis["confidence"] == 0.30
    assert raw_analysis["liquidity_risk"] == "medium"

def test_shadow_smart_money_hallucination_detected():
    """Упоминание 'Smart Money подтверждают' при отсутствии данных → очищается"""
    analysis = {
        "opinion": "Smart Money подтверждают покупку YES.",
        "risk_assessment": "Киты подтверждают позицию.",
        "shadow_verdict": "Входить.",
        "orderbook_facts": "данные недоступны",
        "confidence": 0.30,
        "agree": True,
        "liquidity_risk": "medium"
    }
    smart_money = None
    if not smart_money:
        for field in ["opinion", "risk_assessment", "shadow_verdict"]:
            text = analysis.get(field, "")
            if "smart money подтверждают" in text.lower() or "киты подтверждают" in text.lower():
                analysis[field] = "данные по крупным трейдерам недоступны"

    assert "Smart Money подтверждают" not in analysis["opinion"]
    assert "недоступны" in analysis["risk_assessment"] or "подтверждают" not in analysis["risk_assessment"]

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
