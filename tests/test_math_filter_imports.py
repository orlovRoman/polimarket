import inspect
import pytest

def test_no_local_datetime_import_in_parse_threshold():
    """datetime должен быть импортирован на уровне модуля."""
    from core import math_filter as mf
    source = inspect.getsource(mf._parse_threshold)
    assert "from datetime" not in source, (
        "_parse_threshold содержит локальный 'from datetime' — "
        "перенесите импорт на уровень модуля"
    )

def test_datetime_imported_at_module_level():
    """datetime должен быть доступен на уровне модуля math_filter."""
    import core.math_filter as mf
    assert hasattr(mf, '_dt') or 'datetime' in dir(mf), (
        "datetime не импортирован на уровне модуля core.math_filter"
    )

def test_parse_threshold_sp500_correct():
    """S&P 500 above 5500 — должен найти 5500, не 500."""
    from core.math_filter import _parse_threshold
    result = _parse_threshold("Will S&P 500 reach 5500 by end of 2026?")
    assert result is not None
    value, unit = result
    assert value == 5500.0, f"Ожидался порог 5500, получен {value}"
