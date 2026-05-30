import pytest
from core.onchain_scorer import OnchainScore
from core.whale_gate import check_whale_gate

def test_whale_gate_allows_when_whale_count_below_threshold():
    """Gate пропускает, если whale_count < 2 (по умолчанию), даже при CONTRA."""
    score = OnchainScore(
        score=-0.8,
        confidence=0.9,
        direction="CONTRA",
        annotation="test",
        whale_count=1,
        yes_dominance=0.2
    )
    result = check_whale_gate(score)
    assert result.allow is True
