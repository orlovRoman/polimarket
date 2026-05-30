import pytest
from unittest.mock import MagicMock

def _market(title, price=0.5):
    m = MagicMock()
    m.title = title
    m.price = price
    return m

class TestScoreMarket:

    def test_eu_abbreviation_scores(self):
        """'eu' (2 символа) должен учитываться в пересечении слов."""
        from services.telegram_listener import _score_market
        m = _market("Will EU ban AI apps in 2026?")
        text = "EU prepares to ban AI applications this year"
        score = _score_market(m, text)
        assert score > 0, (
            "score=0: 'eu' и 'ai' отрезаны фильтром len>=3 — "
            "снизьте порог до len>=2"
        )

    def test_ai_abbreviation_scores(self):
        from services.telegram_listener import _score_market
        m = _market("Will AI replace developers by 2027?")
        text = "AI is taking over software engineering jobs"
        score = _score_market(m, text)
        assert score >= 10, f"'ai' должен дать хотя бы 10 очков, получено {score}"

    def test_exact_title_match_gives_1000(self):
        from services.telegram_listener import _score_market
        m = _market("Will Bitcoin reach 100k?")
        text = "will bitcoin reach 100k? by end of year"
        score = _score_market(m, text)
        assert score >= 1000

    def test_inactive_market_lower_score(self):
        """Неактивный рынок (price=0.99) не получает +5 бонус."""
        from services.telegram_listener import _score_market
        m_active   = _market("Bitcoin 100k", price=0.5)
        m_inactive = _market("Bitcoin 100k", price=0.99)
        text = "bitcoin 100k prediction"
        assert _score_market(m_active, text) > _score_market(m_inactive, text)
