import json, pytest
from pathlib import Path
from datetime import datetime, timezone
from core.models import Market
from core.math_filter import math_pre_filter, FilterDecision

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "market_pairs.json").read_text(encoding='utf-8')
)

def _make(d, mid):
    return Market(
        id=mid, platform=d["platform"], title=d["title"],
        url="https://example.com", outcome="YES", price=d["price"],
        close_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )

@pytest.mark.parametrize("case", FIXTURES, ids=[c["description"] for c in FIXTURES])
def test_regression(case):
    a = _make(case["market_a"], "a")
    b = _make(case["market_b"], "b")
    result = math_pre_filter(a, b)
    expected = FilterDecision[case["expected_decision"]]
    assert result.decision == expected, (
        f"FAILED: {case['description']}\n"
        f"Ожидали {expected.value}, получили {result.decision.value}\n"
        f"Reasoning: {result.reasoning}"
    )
