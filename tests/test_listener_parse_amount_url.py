import pytest
from services.telegram_listener import parse_whale_alert

class TestParseAmountFromUrl:

    def test_dollar_in_url_not_parsed_as_amount(self):
        """$ref или $utm в URL не должен парситься как сумма сделки."""
        text = (
            "Check this market "
            "(https://polymarket.com/event/btc?ref=$affiliate) "
            "— big trade incoming"
        )
        result = parse_whale_alert(text)
        # Сумма должна быть 0.0 (не найдена), а не падение ValueError
        assert result["amount_usd"] == 0.0, (
            f"amount_usd={result['amount_usd']} — $ в URL был ошибочно распознан как сумма"
        )

    def test_real_amount_parsed_correctly(self):
        """Реальная сумма $15,000 парсится корректно."""
        text = "Trader bought YES $15,000 at 72¢"
        result = parse_whale_alert(text)
        assert result["amount_usd"] == 15000.0

    def test_amount_with_cents(self):
        """Сумма с центами $1,250.50 парсится корректно."""
        text = "Trade: $1,250.50 on YES"
        result = parse_whale_alert(text)
        assert result["amount_usd"] == 1250.50

    def test_no_amount_returns_zero(self):
        """Без суммы в тексте возвращается 0.0."""
        text = "Interesting trade on the market, no price mentioned"
        result = parse_whale_alert(text)
        assert result["amount_usd"] == 0.0
