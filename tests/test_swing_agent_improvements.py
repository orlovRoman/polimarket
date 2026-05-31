import pytest
from unittest.mock import MagicMock

# ── Итерация 1: Двухфазный confidence (LLM + blend) ──────────────────────────

def test_llm_confidence_overrides_low_hype_score():
    """LLM с высоким confidence должен дать buy даже при hype_score=0.40."""
    from core.swing_rules import swing_decision
    rec, conf = swing_decision(
        hype_score=0.40,
        price=0.10,
        llm_confidence=0.80,
        llm_direction="YES"
    )
    assert rec == "buy", f"Ожидали buy при llm_confidence=0.80, получили {rec}"
    assert conf > 0.60


def test_formula_overrides_high_llm_if_price_expensive():
    """Если цена дорогая (>0.20), buy не должен выходить даже при LLM confidence=0.9."""
    from core.swing_rules import swing_decision
    rec, _ = swing_decision(
        hype_score=0.80,
        price=0.35,  # дорого для swing
        llm_confidence=0.90,
        llm_direction="YES"
    )
    assert rec == "ignore", "Дорогая цена должна блокировать buy независимо от LLM"


def test_blend_weights():
    """Финальный confidence = 0.35*hype + 0.65*llm_confidence."""
    from core.swing_rules import swing_decision
    _, conf = swing_decision(
        hype_score=0.60,
        price=0.08,
        llm_confidence=0.70,
        llm_direction="YES"
    )
    expected = round(0.35 * 0.60 + 0.65 * 0.70, 3)
    assert abs(conf - expected) < 0.01, f"Blend неверный: {conf} != {expected}"


# ── Итерация 2: Динамический промпт по горизонту ─────────────────────────────

from agents.shared.utils.horizon_strategy import get_horizon_strategy

def test_critical_horizon_requires_high_confidence():
    h = get_horizon_strategy(3.0)
    assert h.label == "CRITICAL"
    assert h.min_confidence >= 0.70
    assert h.require_immediate_catalyst is True


def test_medium_horizon_is_optimal():
    h = get_horizon_strategy(48.0)
    assert h.label == "MEDIUM"
    assert h.require_immediate_catalyst is False
    assert h.min_confidence < 0.60


def test_long_horizon_has_higher_bar():
    h = get_horizon_strategy(200.0)
    assert h.label == "LONG"
    # Длинный горизонт требует более высокого confidence чем оптимальный
    h_medium = get_horizon_strategy(48.0)
    assert h.min_confidence > h_medium.min_confidence


def test_horizon_blocks_buy_on_critical_without_catalyst():
    """
    При CRITICAL горизонте buy с отсутствующим катализатором → ignore.
    """
    h = get_horizon_strategy(2.0)
    analysis = {
        "recommendation": "buy",
        "confidence": 0.80,
        "catalyst": "нет катализатора",
        "swing_verdict": "памп вероятен"
    }
    if h.require_immediate_catalyst:
        catalyst = analysis["catalyst"].lower()
        if any(ph in catalyst for ph in ["нет катализатора", "отсутствует"]):
            analysis["recommendation"] = "ignore"
    assert analysis["recommendation"] == "ignore"


# ── Итерация 3: ROI-фильтр ───────────────────────────────────────────────────

from agents.shared.utils.roi_filter import apply_roi_filter

def test_roi_filter_passes_good_trade():
    """0.05 → 0.12 = ROI 140%, edge 0.07 → должен пройти."""
    r = apply_roi_filter(current_price=0.05, target_price=0.12, direction="YES")
    assert r.passes is True
    assert r.roi_percent > 100


def test_roi_filter_blocks_low_roi():
    """0.10 → 0.13 = ROI 30% < 40% → блок."""
    r = apply_roi_filter(current_price=0.10, target_price=0.13)
    assert r.passes is False
    assert "ROI" in r.rejection_reason


def test_roi_filter_blocks_expensive_entry():
    """Цена входа 0.30 > MAX_ENTRY_PRICE → блок."""
    r = apply_roi_filter(current_price=0.30, target_price=0.70)
    assert r.passes is False
    assert "Цена входа" in r.rejection_reason


def test_roi_filter_no_direction():
    """Шорт: current_price=0.88, direction=NO → entry=0.12, target=0.25."""
    r = apply_roi_filter(current_price=0.88, target_price=0.25, direction="NO")
    assert r.passes is True


def test_roi_filter_tiny_edge_blocked():
    """Edge 0.02 < 0.04 → блок даже при высоком ROI%."""
    r = apply_roi_filter(current_price=0.02, target_price=0.04)
    assert r.passes is False


# ── Итерация 4: Контрарианский анализ ────────────────────────────────────────

def test_asymmetry_blocks_balanced_market():
    """asymmetry_score=0.50 должен заблокировать buy."""
    analysis = {
        "recommendation": "buy",
        "confidence": 0.75,
        "asymmetry_score": 0.50,
        "swing_verdict": "памп вероятен"
    }
    asymmetry = float(analysis.get("asymmetry_score", 0.5))
    if asymmetry < 0.55:
        analysis["recommendation"] = "ignore"

    assert analysis["recommendation"] == "ignore"


def test_asymmetry_passes_strong_case():
    """asymmetry_score=0.80 не должен блокировать buy."""
    analysis = {
        "recommendation": "buy",
        "confidence": 0.75,
        "asymmetry_score": 0.80,
        "swing_verdict": "памп вероятен"
    }
    asymmetry = float(analysis.get("asymmetry_score", 0.5))
    if asymmetry < 0.55:
        analysis["recommendation"] = "ignore"

    assert analysis["recommendation"] == "buy"


# ── Итерация 5: Cross-check catalyst vs news ─────────────────────────────────

from agents.shared.utils.catalyst_verifier import verify_catalyst


def test_catalyst_confirmed_by_news():
    """Слова катализатора есть в новостях → confirmed."""
    result = verify_catalyst(
        catalyst="Трамп подписал указ о тарифах на сталь",
        news_block="[2026-05-31] Трамп объявил новые тарифы на сталь и алюминий",
    )
    assert result.confirmed is True
    assert result.confidence_penalty == 0.0


def test_catalyst_not_in_news_penalized():
    """Слова катализатора не встречаются в новостях → штраф."""
    result = verify_catalyst(
        catalyst="SpaceX запустит Starship через 2 дня",
        news_block="Биткоин достиг нового максимума. Apple представила iPhone 18.",
    )
    assert result.confirmed is False
    assert result.confidence_penalty > 0.0
    assert "не подтверждён" in result.warning


def test_no_catalyst_phrase_not_penalized():
    """'Нет катализатора' — честный ответ, не штрафуем."""
    result = verify_catalyst(
        catalyst="нет катализатора",
        news_block="любые новости",
    )
    assert result.confirmed is True
    assert result.confidence_penalty == 0.0


def test_empty_catalyst_penalized():
    result = verify_catalyst(catalyst="", news_block="любые новости")
    assert result.confirmed is False
    assert result.confidence_penalty > 0.0


def test_partial_overlap_lower_penalty():
    """1 совпавшее слово → меньший штраф чем 0 совпадений."""
    result_one = verify_catalyst(
        catalyst="Трамп выступит на саммите",
        news_block="Трамп встретился с Меркель в Берлине",
    )
    result_zero = verify_catalyst(
        catalyst="квантовый компьютер IBM",
        news_block="Биткоин вырос на 5%",
    )
    assert result_one.confidence_penalty <= result_zero.confidence_penalty
