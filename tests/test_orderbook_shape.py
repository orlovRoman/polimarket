from core.orderbook_shape import analyze_orderbook_shape

def test_analyze_orderbook_shape_thin_ask():
    # Тест на тонкий ask wall (< $200)
    ob = {
        "ask_depth_5": 150.0,
        "bid_depth_5": 500.0,
        "top_ask": 0.15
    }
    res = analyze_orderbook_shape(ob, 0.10)
    assert res.thin_ask_wall is True
    assert res.pumpability_score > 0.6
    assert "тонкий ask ✅" in res.annotation

def test_analyze_orderbook_shape_deep_ask():
    # Тест на глубокий ask wall (>= $200)
    ob = {
        "ask_depth_5": 800.0,
        "bid_depth_5": 100.0,
        "top_ask": 0.55
    }
    res = analyze_orderbook_shape(ob, 0.50)
    assert res.thin_ask_wall is False
    assert res.pumpability_score < 0.3
    assert "глубокий ask ❌" in res.annotation

def test_analyze_orderbook_shape_empty():
    # Тест при пустом стакане
    res = analyze_orderbook_shape({}, 0.20)
    assert res.thin_ask_wall is False
    assert res.pumpability_score == 0.0
    assert "Ордербук недоступен" in res.annotation
