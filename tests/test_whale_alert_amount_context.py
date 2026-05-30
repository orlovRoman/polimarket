from services.telegram_listener import parse_whale_alert

class TestWhaleAlertAmountContext:

    def test_pnl_before_trade_amount(self):
        """P&L строка в начале не должна перехватывать сумму сделки."""
        text = "P&L last 30d: +$3,200. Trader bought YES $15,000 at 72¢"
        result = parse_whale_alert(text)
        assert result["amount_usd"] == 15000.0, (
            f"amount_usd={result['amount_usd']} — P&L $3,200 перехватил сумму сделки $15,000"
        )

    def test_buy_keyword_amount_priority(self):
        """Сумма после buy-слова имеет максимальный приоритет."""
        text = "Market cap $500,000. Trader bought YES $25,000"
        result = parse_whale_alert(text)
        assert result["amount_usd"] == 25000.0, (
            f"amount_usd={result['amount_usd']} — рыночная капитализация перехватила сумму"
        )

    def test_standard_amount_without_context(self):
        """Стандартный случай без P&L — первый $N."""
        text = "Trader bought YES $10,500 at 65¢"
        result = parse_whale_alert(text)
        assert result["amount_usd"] == 10500.0

    def test_no_amount_returns_zero(self):
        text = "Market movement detected, trend is bullish"
        result = parse_whale_alert(text)
        assert result["amount_usd"] == 0.0
