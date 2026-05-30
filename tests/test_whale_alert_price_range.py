import pytest
from services.telegram_listener import parse_whale_alert

class TestWhaleAlertPriceRange:

    def test_large_value_not_parsed_as_price(self):
        """$15,000 после 'at' — это не цена контракта (>1.0), игнорируем."""
        text = "Trader bought YES at $15,000"
        result = parse_whale_alert(text)
        assert result["price"] is None or result["price"] <= 1.0, (
            f"price={result['price']} — $15,000 ошибочно распознан как цена контракта"
        )

    def test_valid_price_parsed(self):
        """$0.72 — корректная цена контракта."""
        text = "Buy YES at $0.72"
        result = parse_whale_alert(text)
        assert result["price"] == pytest.approx(0.72)

    def test_cents_price_priority_over_usd(self):
        """Цена в центах имеет приоритет над USD-форматом."""
        text = "Buy YES at 72¢ (market cap $500,000)"
        result = parse_whale_alert(text)
        assert result["price"] == pytest.approx(0.72)
        assert result["amount_usd"] == pytest.approx(500000.0)
