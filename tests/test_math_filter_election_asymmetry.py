from core.math_filter import _check_same_event

def test_specific_vs_generic_election():
    """Рынок с конкретным типом выборов vs общий рынок — должен решаться через overlap."""
    result = _check_same_event(
        "Will Democrats win the 2026 midterm elections?",
        "Will Democrats win in 2026?"
    )
    assert isinstance(result, bool)

def test_different_election_types_returns_false():
    """Senate vs Gubernatorial — разные типы → False."""
    result = _check_same_event(
        "Will Democrats win the 2026 Senate election?",
        "Will Democrats win the 2026 governor election?"
    )
    assert result is False, "Senate и Governor — разные события"

def test_same_election_type_returns_true():
    """Одинаковые типы выборов — одно событие."""
    result = _check_same_event(
        "Will Democrats win the 2026 Senate election?",
        "Will Democratic Party take Senate majority in 2026?"
    )
    assert result is True, "Оба про Senate 2026 → одно событие"
