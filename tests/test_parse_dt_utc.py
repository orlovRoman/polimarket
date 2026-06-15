import pytest
from datetime import timezone
from agents.shared.python.utils import _parse_dt_utc

# --- Корректное поведение ---

@pytest.mark.parametrize("s, expected_iso", [
    # Без timezone → UTC
    ("2026-06-15 10:30:00",         "2026-06-15T10:30:00+00:00"),
    ("2026-06-15T10:30:00",         "2026-06-15T10:30:00+00:00"),
    # Z-суффикс → UTC (БАГИ на Python < 3.11 без fix)
    ("2026-06-15T10:30:00Z",        "2026-06-15T10:30:00+00:00"),
    # +00:00 → UTC без сдвига
    ("2026-06-15 10:30:00+00:00",   "2026-06-15T10:30:00+00:00"),
    # +07:00 → конвертируем в UTC (10:30 - 7h = 03:30)
    ("2026-06-15 10:30:00+07:00",   "2026-06-15T03:30:00+00:00"),
    # -05:00 → UTC (10:30 + 5h = 15:30)
    ("2026-06-15T10:30:00-05:00",   "2026-06-15T15:30:00+00:00"),
])
def test_parse_dt_utc_known_values(s, expected_iso):
    result = _parse_dt_utc(s)
    assert result is not None
    assert result.tzinfo == timezone.utc
    assert result.isoformat() == expected_iso, (
        f"Вход: {s!r}\n"
        f"Ожидали: {expected_iso}\n"
        f"Получили: {result.isoformat()}"
    )

# --- Edge cases ---

def test_parse_dt_utc_none():
    assert _parse_dt_utc(None) is None

def test_parse_dt_utc_empty_string():
    assert _parse_dt_utc("") is None

def test_parse_dt_utc_whitespace():
    assert _parse_dt_utc("   ") is None

def test_parse_dt_utc_garbage():
    assert _parse_dt_utc("not-a-date") is None

def test_parse_dt_utc_partial():
    # Только дата без времени — валидный ISO
    result = _parse_dt_utc("2026-06-15")
    assert result is not None
    assert result.tzinfo == timezone.utc

def test_parse_dt_utc_returns_utc_always():
    """Результат ВСЕГДА должен быть в UTC, не в исходном offset."""
    result = _parse_dt_utc("2026-06-15T10:30:00+07:00")
    assert result.utcoffset().total_seconds() == 0, (
        "Функция должна возвращать UTC, а не сохранять исходный offset"
    )
