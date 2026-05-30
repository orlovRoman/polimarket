import pytest

class TestCurrencyRegex:

    def test_3000_before_not_parsed_as_billions(self):
        """'$3000 before May' НЕ должно парситься как 3 трлн."""
        from core.math_filter import _parse_threshold
        result = _parse_threshold("Will gold hit $3000 before May?")
        assert result is not None
        value, unit = result
        assert value == 3000.0, (
            f"Ожидался порог $3000, получено {value:.2e} — "
            f"возможно regex матчит '3000 b' как billion"
        )
        assert unit == 'pts', f"Ожидался unit='pts', получен '{unit}'"

    def test_5k_parsed_correctly(self):
        """$5k = 5000"""
        from core.math_filter import _parse_threshold
        result = _parse_threshold("Will ETH reach $5k?")
        assert result == (5000.0, 'usd'), f"Получен {result}"

    def test_2b_parsed_correctly(self):
        """$2B = 2_000_000_000"""
        from core.math_filter import _parse_threshold
        result = _parse_threshold("Will company valuation hit $2B?")
        assert result == (2e9, 'usd'), f"Получен {result}"

    def test_2_billion_full_word(self):
        """'$2 billion' (с пробелом, полное слово) должен парситься."""
        from core.math_filter import _parse_threshold
        result = _parse_threshold("Will market cap exceed $2 billion?")
        assert result is not None
        value, unit = result
        assert value == 2e9, f"Ожидалось 2e9, получено {value}"

    def test_no_false_match_on_word_boundary(self):
        """'500 bps' не должен матчить 500b = 500 billion."""
        from core.math_filter import _parse_threshold
        result = _parse_threshold("Will Fed cut 50 bps?")
        if result:
            value, unit = result
            assert value < 1e6, f"False match: 50 bps → {value:.2e}"
