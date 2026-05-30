import pytest
from services.telegram_listener import parse_whale_alert, parse_radar_signal

class TestOutcomeBoundary:

    @pytest.mark.parametrize("text,expected", [
        # Реальные кейсы YES
        ("Trader bought YES $15,000 at 72¢", "YES"),
        ("BUY YES @ $0.72", "YES"),
        # Реальные кейсы NO
        ("Trader bought NO $8,000 at 28¢", "NO"),
        ("BUY NO @ $0.28", "NO"),
        # False positive на подстроки — ключевые тесты
        ("Large trade KNOWN for $50,000. Entry 15¢", None),     # KNOWN содержит NO
        ("ANNOUNCEMENT: $25,000 trade. No details.", None),      # ANNOUNCEMENT + "No details"
        ("INNOVATION market trade $10,000", None),               # INNOVATION содержит NO
        ("Whale alert NOW at $30,000", None),                    # NOW содержит NO
        # None при отсутствии маркера
        ("Interesting whale trade for $25,000 on some market", None),
    ])
    def test_parse_whale_alert_outcome(self, text, expected):
        result = parse_whale_alert(text)
        assert result["outcome"] == expected, (
            f"text={text!r}: expected outcome={expected!r}, got {result['outcome']!r}"
        )

    @pytest.mark.parametrize("text,expected", [
        ("⚡️ Buy Yes\n├ Amount: $11,136", "YES"),
        ("⚡️ Buy No\n├ Amount: $5,000", "NO"),
        # Нет маркера Buy Yes/No и нет YES/NO как слова
        ("INNOVATION trade $10,000\nWin Rate: 67%", None),
        ("ANNOUNCEMENT $25,000", None),
    ])
    def test_parse_radar_signal_outcome(self, text, expected):
        result = parse_radar_signal(text)
        assert result["outcome"] == expected, (
            f"text={text!r}: expected={expected!r}, got {result['outcome']!r}"
        )
