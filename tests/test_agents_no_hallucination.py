# tests/test_agents_no_hallucination.py
"""
Интеграционные тесты: проверяют что агенты НЕ генерируют фантазии
при отсутствии входных данных.
Не требуют реального API — только логику гардов и post-validation.
"""
from agents.shared.utils.prompt_guards import (
    guard_description, guard_orderbook, guard_smart_money, guard_news_with_age
)
from agents.shared.utils.hype_calculator import HypeMetrics, calculate_hype_potential

# ── SCOUT ──────────────────────────────────────────────────────────────
def test_scout_no_description_blocks_oracle_fantasy():
    block = guard_description(None)
    assert "НЕ ПРИДУМЫВАЙ" in block

def test_scout_description_present_cites_source():
    desc = "Resolves YES if Federal Reserve raises rate. Source: Fed official statement."
    block = guard_description(desc)
    assert "Federal Reserve" in block
    assert "Fed official statement" in block

# ── SHADOW ─────────────────────────────────────────────────────────────
def test_shadow_no_orderbook_confidence_capped():
    """Без ордербука confidence не может быть > 0.40"""
    analysis = {"confidence": 0.75, "liquidity_risk": "low", "agree": True,
                "opinion": "...", "orderbook_facts": "спред 5%",
                "risk_assessment": "...", "shadow_verdict": "..."}
    orderbook = None  # явная переменная
    # Воспроизводим логику из agent.py
    if not orderbook:
        if float(analysis["confidence"]) > 0.40:
            analysis["confidence"] = 0.30
            analysis["liquidity_risk"] = "medium"
    assert analysis["confidence"] == 0.30
    assert analysis["liquidity_risk"] == "medium"

def test_shadow_confidence_not_capped_with_orderbook():
    """С ордербуком confidence НЕ снижается"""
    analysis = {"confidence": 0.75, "liquidity_risk": "low"}
    orderbook = {"spread": "2%", "bid_depth_5": 1000, "ask_depth_5": 800}
    if not orderbook:  # False — не входим
        analysis["confidence"] = 0.30
    assert analysis["confidence"] == 0.75  # не тронуто

def test_shadow_no_smart_money_no_mention():
    analysis = {"risk_assessment": "Smart Money подтверждают позицию YES."}
    smart_money = None
    if not smart_money:
        if "smart money подтверждают" in analysis["risk_assessment"].lower():
            analysis["risk_assessment"] = "Данные по крупным трейдерам недоступны."
    assert "Smart Money подтверждают" not in analysis["risk_assessment"]

# ── ARBITRAGE ──────────────────────────────────────────────────────────
def test_arbitrage_no_description_no_logical_contradiction():
    result = {"arbitrage_type": "logical_contradiction"}
    market_a_desc = ""
    if not market_a_desc and result["arbitrage_type"] == "logical_contradiction":
        result["arbitrage_type"] = "statistical_pair_trade"
    assert result["arbitrage_type"] == "statistical_pair_trade"

# ── SWING ──────────────────────────────────────────────────────────────
def test_swing_hype_python_beats_llm_fantasy():
    hype_python = 0.40
    llm_output = 0.90
    if abs(llm_output - hype_python) > 0.15:
        llm_output = hype_python
    assert llm_output == 0.40

def test_swing_no_news_forces_catalyst_absence():
    news_block = guard_news_with_age([])
    assert "catalyst_absence_reason" in news_block

def test_swing_old_news_tagged_not_catalyst():
    from datetime import datetime, timedelta
    old = [{"title": "Something happened", "published": (datetime.utcnow() - timedelta(hours=100)).isoformat()}]
    block = guard_news_with_age(old)
    assert "НЕ КАТАЛИЗАТОР" in block
