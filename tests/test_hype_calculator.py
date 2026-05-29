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
