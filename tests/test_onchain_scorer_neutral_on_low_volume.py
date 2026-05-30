import pytest
from core.context import SmartMoneySummary
from core.onchain_scorer import compute_onchain_score

def test_compute_onchain_score_neutral_below_200():
    """При total < $200 возвращается нейтральный скор."""
    sm = SmartMoneySummary(
        available=True,
        total_yes_usd=50,
        total_no_usd=100,
        yes_dominance=0.33,
        top_wallets=[],
        summary=""
    )
    result = compute_onchain_score(sm)
    assert result.direction == "NEUTRAL"
    assert result.score == 0.0
