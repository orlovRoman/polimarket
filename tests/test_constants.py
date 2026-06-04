from core.constants import Outcome

def test_outcome_values():
    assert Outcome.YES == "YES"
    assert Outcome.NO == "NO"

def test_outcome_is_valid():
    assert Outcome.is_valid("YES")
    assert Outcome.is_valid("no")  # case-insensitive
    assert not Outcome.is_valid("MAYBE")

def test_outcome_import_in_engine():
    """Проверяет что импорт из engine.py не падает."""
    from core.constants import Outcome as O  # noqa
    assert hasattr(O, "YES") and hasattr(O, "NO")

def test_shadow_analysis_no_outcome_comparison():
    """target_outcome.upper() == Outcome.NO — паттерн из engine.py."""
    target = "NO"
    assert target.upper() == Outcome.NO

    target = "YES"
    assert target.upper() != Outcome.NO
    assert target.upper() == Outcome.YES
