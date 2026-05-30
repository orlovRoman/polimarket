from core.math_filter import _check_same_event

def test_presidential_race_detected_as_election_type():
    """'presidential' → election_type должен определяться корректно."""
    result = _check_same_event(
        "Will Trump win the 2028 presidential election?",
        "Will Trump win the 2028 presidential race?"
    )
    assert result is True, "'presidential election' и 'presidential race' — одно событие"

def test_presidential_vs_senate_not_same():
    """Presidential vs Senate — разные типы выборов."""
    result = _check_same_event(
        "Will Trump win the 2028 presidential election?",
        "Will Republicans win the 2028 Senate election?"
    )
    assert result is False
