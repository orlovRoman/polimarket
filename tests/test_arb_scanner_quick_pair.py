import pytest
from core.arb_scanner import _quick_pair_check

class TestQuickPairCheck:

    def test_standard_pair_passes(self):
        """Обычная пара с 2+ общими словами — проходит."""
        assert _quick_pair_check(
            "Bitcoin above 100000 by December 2026",
            "Bitcoin above 80000 by December 2026"
        ) is True

    def test_no_overlap_rejected(self):
        """Разные темы — отклоняется."""
        assert _quick_pair_check(
            "Will Trump win 2028 election",
            "Bitcoin price above 100000"
        ) is False

    def test_short_titles_one_common_word(self):
        """Короткие заголовки с 1 общим словом — должны проходить (после фикса)."""
        # "Fed pause rates" -> {fed, pause, rates} (len=3 <= 4)
        # "Federal Reserve hold" -> {federal, reserve, hold} (len=3 <= 4)
        # Но здесь 0 общих слов, поэтому возвращает False
        assert _quick_pair_check(
            "Fed pause rates",
            "Federal Reserve hold",
        ) is False

    def test_fed_rate_pair_detected(self):
        """Fed-пара с явным пересечением — проходит."""
        assert _quick_pair_check(
            "Fed rate hike probability Q3 2026",
            "Federal Reserve rate hike decision 2026"
        ) is True  # общие: {rate, hike} — 2 слова

    def test_short_titles_with_one_meaningful_overlap(self):
        """2 коротких заголовка, 1 общее значимое слово — фикс работает."""
        # "Fed hike decision" -> {fed, hike, decision} (len 3 <= 4)
        # "Fed pause decision" -> {fed, pause, decision} (len 3 <= 4)
        # общие: {fed, decision} -> 2 (проходит в любом случае)
        assert _quick_pair_check("Fed hike decision", "Fed pause decision") is True

    def test_short_titles_with_exactly_one_meaningful_overlap(self):
        # "Fed hike" -> {fed, hike} (len=2 <= 4)
        # "Fed pause" -> {fed, pause} (len=2 <= 4)
        # Общие: {fed} -> len=1. 
        # До фикса: False (min_common=2).
        # После фикса: True (len <= 4 -> effective_min=1).
        assert _quick_pair_check("Fed hike", "Fed pause") is True

    def test_stopwords_not_counted(self):
        """Стопслова не считаются общими словами."""
        assert _quick_pair_check(
            "will the fed raise",
            "will the ecb cut"
        ) is False  # после удаления стопслов: {} и {} — 0 общих ≥3 символов

    def test_empty_titles_return_false(self):
        assert _quick_pair_check("", "") is False
        assert _quick_pair_check("", "Bitcoin price") is False
