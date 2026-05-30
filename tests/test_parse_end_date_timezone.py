from datetime import datetime, timezone
from services.telegram_listener import _parse_end_date

class TestParseEndDate:

    def test_naive_datetime_gets_utc(self):
        """Дата без timezone не должна вызывать TypeError при сравнении."""
        item = {"endDate": "2025-01-01T00:00:00"}  # без Z и +00:00
        result = _parse_end_date(item)
        assert result.tzinfo is not None, "Результат должен быть aware datetime"
        # Не должно быть TypeError:
        _ = result <= datetime.now(timezone.utc)

    def test_z_suffix_parsed(self):
        item = {"endDate": "2026-12-31T23:59:59Z"}
        result = _parse_end_date(item)
        assert result.tzinfo is not None
        assert result.year == 2026

    def test_fallback_on_empty(self):
        """Без даты возвращает 2099 — рынок считается вечным."""
        result = _parse_end_date({})
        assert result.year == 2099

    def test_expired_market_detected(self):
        item = {"endDate": "2020-01-01T00:00:00Z"}
        result = _parse_end_date(item)
        assert result < datetime.now(timezone.utc), "2020 год должен быть в прошлом"
