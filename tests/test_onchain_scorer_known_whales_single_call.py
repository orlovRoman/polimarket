import pytest
from core.context import SmartMoneySummary
from core.onchain_scorer import compute_onchain_score

def test_get_known_whales_called_once(monkeypatch):
    """get_known_whales() должен вызываться ровно 1 раз, не N раз."""
    call_count = 0
    def mock_get_known_whales():
        nonlocal call_count
        call_count += 1
        return {}

    monkeypatch.setattr("core.onchain_scorer.get_known_whales", mock_get_known_whales)
    sm = SmartMoneySummary(
        available=True,
        total_yes_usd=1000,
        total_no_usd=500,
        yes_dominance=0.67,
        top_wallets=[
            "whale1 | WR: 80% → YES $500",
            "whale2 | WR: 70% → YES $300"
        ],
        summary=""
    )
    compute_onchain_score(sm)
    assert call_count == 1, f"Ожидался 1 вызов, получено {call_count}"
