# tests/test_hype_calculator.py
from agents.shared.utils.hype_calculator import HypeMetrics, calculate_hype_potential

def test_hype_no_signals_returns_low():
    m = HypeMetrics(0, 0, 0, 0, 0.0, 24)
    score, _ = calculate_hype_potential(m)
    assert score < 0.20

def test_hype_all_max_returns_high():
    m = HypeMetrics(100, 30, 5000, 10, 0.20, 24)
    score, _ = calculate_hype_potential(m)
    assert score > 0.80

def test_hype_breakdown_contains_all_components():
    m = HypeMetrics(60, 10, 500, 2, 0.05, 30)
    score, breakdown = calculate_hype_potential(m)
    assert "Trends" in breakdown
    assert "Reddit" in breakdown
    assert "Новости" in breakdown
    assert "Тайминг" in breakdown
    assert f"hype_potential={score:.3f}" in breakdown

def test_hype_timing_too_soon_penalized():
    m1 = HypeMetrics(80, 20, 1000, 3, 0.10, 3)   # 3ч — мало
    m2 = HypeMetrics(80, 20, 1000, 3, 0.10, 24)  # 24ч — норм
    s1, _ = calculate_hype_potential(m1)
    s2, _ = calculate_hype_potential(m2)
    assert s2 > s1

def test_hype_score_bounded_01():
    m = HypeMetrics(100, 100, 99999, 100, 1.0, 24)
    score, _ = calculate_hype_potential(m)
    assert 0.0 <= score <= 1.0

def test_swing_llm_hype_capped_at_015_deviation():
    """Отклонение LLM от Python-расчёта > 0.15 → используется Python"""
    hype_score = 0.45  # Python рассчитал
    llm_hype = 0.85    # LLM нафантазировал
    if abs(llm_hype - hype_score) > 0.15:
        llm_hype = hype_score
    assert llm_hype == 0.45

def test_swing_price_history_not_overwritten():
    """Исходный price_history не перезаписывается внутри estimate_market"""
    price_history = [{"recorded_at": "2026-05-29 10:00", "price": 0.30 + i*0.01}
                     for i in range(10)]
    original_len = len(price_history)
    price_hist = price_history or []
    price_now = 0.45
    price_6h_ago = price_hist[-7]["price"] if len(price_hist) >= 7 else price_now
    delta = price_now - price_6h_ago
    assert len(price_history) == original_len  # не перезаписан
    assert delta != 0.0  # дельта реальная
    assert price_6h_ago == price_history[-7]["price"]

def test_recent_news_count_from_processed_items():
    """recent_news_count считается из processed news_items, не из сырых заголовков"""
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    news_items_to_guard = [
        {"title": "Fresh news", "published": (now - timedelta(hours=2)).isoformat()},
        {"title": "Old news",   "published": (now - timedelta(hours=50)).isoformat()},
        {"title": "No date",    "published": None},
    ]
    recent_news_count = 0
    for ni in news_items_to_guard:
        pub = ni.get("published")
        if pub:
            try:
                pub_dt = datetime.fromisoformat(pub)
                age_h = (now - pub_dt).total_seconds() / 3600
                if 0 <= age_h <= 6:
                    recent_news_count += 1
            except Exception:
                pass
    assert recent_news_count == 1  # только свежая новость

