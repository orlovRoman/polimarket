import pytest
from datetime import datetime, timezone
from core.models import Signal, SwingSignal, AgentOpinion, CrossArbitrageSignal


def _base_signal(**kwargs):
    defaults = dict(
        id="sig-1", type="MISPRICING", market_id="mkt-1",
        platform="polymarket", priority="medium",
        summary="test", details="test",
        confidence=0.8,
    )
    defaults.update(kwargs)
    return Signal(**defaults)


def _base_swing(**kwargs):
    defaults = dict(
        id="sw-1", market_id="mkt-1", platform="polymarket",
        hype_potential=0.7, recommendation="buy",
        target_outcome="YES", target_exit_price=0.75,
        confidence=0.8, reasoning="hype",
    )
    defaults.update(kwargs)
    return SwingSignal(**defaults)


def _base_arb(**kwargs):
    defaults = dict(
        market_a_id="a", market_a_platform="polymarket",
        market_a_title="T A", market_a_price=0.6, market_a_url="https://a",
        market_b_id="b", market_b_platform="kalshi",
        market_b_title="T B", market_b_price=0.5, market_b_url="https://b",
        has_arbitrage=True, arbitrage_type="price_divergence",
        spread_percent=10.0, reasoning="r", trade_instruction="t",
        match_score=0.9,
    )
    defaults.update(kwargs)
    return CrossArbitrageSignal(**defaults)


# ── Баг #1: SwingSignal.recommendation нормализация ──────────

@pytest.mark.parametrize("raw,expected", [
    ("buy",      "buy"),
    ("BUY",      "buy"),
    ("Buy",      "buy"),
    ("buy_yes",  "buy"),
    ("yes",      "buy"),
    ("long",     "buy"),
    ("ignore",   "ignore"),
    ("IGNORE",   "ignore"),
    ("hold",     "ignore"),
    ("HOLD",     "ignore"),
    ("no",       "ignore"),
    ("skip",     "ignore"),
    ("sell",     "ignore"),
    ("neutral",  "ignore"),
    ("no_signal","ignore"),
])
def test_swing_signal_recommendation_normalizes(raw, expected):
    sw = _base_swing(recommendation=raw)
    assert sw.recommendation == expected, \
        f"'{raw}' → ожидали '{expected}', получили '{sw.recommendation}'"


def test_swing_signal_unknown_recommendation_preserved():
    """Неизвестное значение сохраняется как есть (не падает)"""
    sw = _base_swing(recommendation="UNKNOWN_VALUE")
    assert sw.recommendation == "unknown_value"


def test_make_consensus_buy_yes_treated_as_buy():
    """'BUY' от LLM нормализуется в 'buy' и консенсус корректно строится"""
    from core.workflow import make_consensus
    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.market.id = "mkt-1"

    shadow = AgentOpinion(
        agent_name="SHADOW", market_id="mkt-1",
        opinion="approve", confidence=0.9, agree=True,
    )

    swing = _base_swing(recommendation="BUY")  # LLM вернул upper case
    assert swing.recommendation == "buy"        # нормализовано

    decision = make_consensus(ctx, signal=None, swing_signal=swing, opinion_shadow=shadow)
    assert decision.status == "saved"


# ── Баг #2: CrossArbitrageSignal Python 3.9 совместимость ────

def test_cross_arbitrage_signal_optional_floats_none():
    """Optional[float] поля принимают None"""
    arb = _base_arb(
        entry_price_a_cents=None,
        entry_price_b_cents=None,
        expected_pnl_pct=None,
    )
    assert arb.entry_price_a_cents is None
    assert arb.entry_price_b_cents is None
    assert arb.expected_pnl_pct is None


def test_cross_arbitrage_signal_optional_floats_values():
    """Optional[float] поля принимают числа"""
    arb = _base_arb(
        entry_price_a_cents=60.0,
        entry_price_b_cents=45.5,
        expected_pnl_pct=8.3,
    )
    assert arb.entry_price_a_cents == 60.0
    assert arb.entry_price_b_cents == 45.5
    assert arb.expected_pnl_pct == 8.3


def test_models_importable_without_type_union_error():
    """Импорт models.py не должен бросать TypeError на Python 3.9"""
    try:
        import importlib
        import core.models as m
        importlib.reload(m)
    except TypeError as e:
        pytest.fail(f"models.py упал с TypeError (проблема совместимости типов): {e}")


# ── Баг #3: Signal.edge допускает отрицательные значения ─────

def test_signal_negative_edge_preserved():
    """edge = -0.05 не должен быть обрезан до 0.0"""
    sig = _base_signal(edge=-0.05)
    assert sig.edge == pytest.approx(-0.05), \
        f"Отрицательный edge должен сохраняться, получили {sig.edge}"


def test_signal_edge_clamped_below_minus_one():
    """edge < -1.0 обрезается до -1.0"""
    sig = _base_signal(edge=-5.0)
    assert sig.edge == pytest.approx(-1.0)


def test_signal_edge_clamped_above_one():
    """edge > 1.0 обрезается до 1.0"""
    sig = _base_signal(edge=3.14)
    assert sig.edge == pytest.approx(1.0)


def test_signal_edge_none_stays_none():
    """edge = None остаётся None"""
    sig = _base_signal(edge=None)
    assert sig.edge is None


def test_signal_confidence_still_clamped_0_1():
    """confidence по-прежнему зажат в [0, 1]"""
    sig = _base_signal(confidence=1.5)
    assert sig.confidence == pytest.approx(1.0)

    sig2 = _base_signal(confidence=-0.1)
    assert sig2.confidence == pytest.approx(0.0)


def test_signal_negative_edge_does_not_affect_confidence():
    """Разделение валидаторов: отрицательный edge не трогает confidence"""
    sig = _base_signal(edge=-0.3, confidence=0.75)
    assert sig.edge == pytest.approx(-0.3)
    assert sig.confidence == pytest.approx(0.75)


# ── Баг #4: AgentOpinion.liquidity_risk нормализация ─────────

@pytest.mark.parametrize("raw,expected", [
    ("low",    "LOW"),
    ("LOW",    "LOW"),
    ("medium", "MEDIUM"),
    ("MEDIUM", "MEDIUM"),
    ("high",   "HIGH"),
    ("HIGH",   "HIGH"),
    ("Low",    "LOW"),
])
def test_agent_opinion_liquidity_risk_normalized_to_upper(raw, expected):
    op = AgentOpinion(
        agent_name="SHADOW", market_id="mkt-1",
        opinion="ok", confidence=0.8, agree=True,
        liquidity_risk=raw,
    )
    assert op.liquidity_risk == expected


def test_agent_opinion_default_liquidity_risk_is_upper():
    """Дефолтное значение уже в UPPER"""
    op = AgentOpinion(
        agent_name="SHADOW", market_id="mkt-1",
        opinion="ok", confidence=0.8, agree=True,
    )
    assert op.liquidity_risk == "MEDIUM"


def test_agent_opinion_liquidity_risk_consistent_in_workflow():
    """liquidity_risk в workflow.py можно сравнивать без .upper()"""
    op = AgentOpinion(
        agent_name="SHADOW", market_id="mkt-1",
        opinion="ok", confidence=0.8, agree=True,
        liquidity_risk="high",
    )
    # Сравнение без .upper() теперь работает
    assert op.liquidity_risk == "HIGH"
    assert op.liquidity_risk != "high"


# ── Интеграционный: полная цепочка LLM output → модель → consensus ──

def test_full_chain_llm_buy_upper_to_consensus_saved():
    """LLM вернул 'BUY' → SwingSignal нормализует → consensus = 'saved'"""
    from core.workflow import make_consensus
    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.market.id = "mkt-1"

    shadow = AgentOpinion(
        agent_name="SHADOW", market_id="mkt-1",
        opinion="approve", confidence=0.9, agree=True,
        liquidity_risk="LOW",
    )
    swing = _base_swing(recommendation="BUY")

    decision = make_consensus(ctx, signal=None, swing_signal=swing, opinion_shadow=shadow)
    assert decision.status == "saved"
    assert shadow.liquidity_risk == "LOW"  # нормализовано, не "low"


def test_full_chain_llm_hold_to_no_signal_swing_hold():
    """LLM вернул 'HOLD' → SwingSignal нормализует → consensus = 'no_signal_swing_hold'"""
    from core.workflow import make_consensus
    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.market.id = "mkt-1"

    swing = _base_swing(recommendation="HOLD")
    assert swing.recommendation == "ignore"

    decision = make_consensus(ctx, signal=None, swing_signal=swing, opinion_shadow=None)
    assert decision.status == "no_signal_swing_hold"
