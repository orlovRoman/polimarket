import pytest
from core.arb_scanner import _quick_pair_check

class TestPriceTagFalsePositive:

    def test_unrelated_markets_with_price_tag_not_matched(self):
        """
        Два несвязанных рынка с ценовым тегом НЕ должны считаться похожими
        только из-за общих слов YES/NO в тегах.
        """
        title_a = "Will Bitcoin reach $200k? (YES: 72¢ | NO: 28¢)"
        title_b = "Will Taylor Swift win Grammy? (YES: 65¢ | NO: 35¢)"
        result = _quick_pair_check(title_a, title_b)
        assert result is False, (
            "Несвязанные рынки не должны матчиться через YES/NO в ценовом теге"
        )

    def test_related_markets_with_price_tag_still_matched(self):
        """Связанные рынки с ценовым тегом должны находиться несмотря на фильтрацию тега."""
        title_a = "Will Fed raise rates in Q3 2026? (YES: 72¢ | NO: 28¢)"
        title_b = "Will Fed raise rates before October? (YES: 68¢ | NO: 32¢)"
        result = _quick_pair_check(title_a, title_b)
        assert result is True, "Связанные рынки должны находиться даже с ценовым тегом"

    def test_no_price_tag_unrelated_still_false(self):
        """Контрольный тест: без ценового тега несвязанные рынки — False."""
        assert _quick_pair_check(
            "Will Bitcoin reach 200k dollars",
            "Will Taylor Swift win Grammy award"
        ) is False

    def test_yes_no_alone_not_sufficient_for_match(self):
        """'yes' и 'no' сами по себе не должны быть единственными совпадениями."""
        # Заголовки без ценового тега, но с yes в тексте обоих
        title_a = "Yes market will resolve"
        title_b = "Yes unrelated event is on"
        # Без стопслов: words_a = {'yes', 'market', 'resolve'}, words_b = {'yes', 'unrelated', 'event'}
        # Пересечение = {'yes'} -> True (поскольку размер <= 4, effective_min = 1)
        # После добавления yes в стопслова: пересечение = 0 -> False
        result = _quick_pair_check(title_a, title_b)
        assert result is False
