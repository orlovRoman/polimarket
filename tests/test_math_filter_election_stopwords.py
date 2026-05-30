import pytest
from core.math_filter import _check_same_event

class TestElectionStopwords:

    def test_different_election_types_not_same_event(self):
        """Senate и midterm выборы с одним кандидатом — разные события."""
        result = _check_same_event(
            "Will Trump win the 2026 midterm election?",
            "Will Trump win the 2026 Senate election?"
        )
        assert result is False, (
            "Midterm и Senate elections — разные события, "
            "не должны считаться одним event"
        )

    def test_same_election_is_same_event(self):
        """Один и тот же рынок с разной формулировкой — одно событие."""
        result = _check_same_event(
            "Will Democrats win the 2026 midterm elections?",
            "Will Democratic Party take majority in 2026 midterms?"
        )
        assert result is True, "Одни и те же выборы должны считаться одним событием"

    def test_presidential_different_years_not_same(self):
        """Выборы разных годов — разные события (year-фильтр)."""
        result = _check_same_event(
            "Will Biden win the 2024 presidential election?",
            "Will Trump win the 2028 presidential election?"
        )
        assert result is False

    def test_election_word_preserved_after_fix(self):
        """'election' НЕ должен быть в стопвордах после фикса."""
        import inspect, re
        from core import math_filter as mf
        source = inspect.getsource(mf._check_same_event)
        stopwords_match = re.search(r"stopwords\s*=\s*\{([^}]+)\}", source, re.DOTALL)
        if stopwords_match:
            stopwords_block = stopwords_match.group(1)
            assert "'election'" not in stopwords_block, (
                "'election' не должен быть в stopwords — это ключевой маркер события"
            )

    def test_president_alias_removed(self):
        """'president' НЕ должен маппиться в 'election' через alias."""
        import inspect, re
        from core import math_filter as mf
        source = inspect.getsource(mf._check_same_event)
        aliases_match = re.search(r"aliases\s*=\s*\{([^}]+)\}", source, re.DOTALL)
        if aliases_match:
            aliases_block = aliases_match.group(1)
            assert "'president': 'election'" not in aliases_block, (
                "'president' → 'election' alias удаляет семантику из сравнения"
            )
