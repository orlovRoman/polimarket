import inspect
import pytest

def test_gemini_client_not_imported_locally():
    """gemini_client должен импортироваться на уровне модуля, не внутри функции."""
    from core import arb_router
    source = inspect.getsource(arb_router.route_ambiguous)
    assert "from agents.shared.utils.gemini_client import" not in source, (
        "route_ambiguous содержит локальный импорт gemini_client — "
        "перенесите на уровень модуля с try/except ImportError"
    )

def test_gemini_client_module_level_available():
    """Модуль arb_router должен экспортировать _GEMINI_AVAILABLE."""
    from core import arb_router
    assert hasattr(arb_router, '_GEMINI_AVAILABLE'), (
        "arb_router должен иметь _GEMINI_AVAILABLE = True/False "
        "в зависимости от доступности gemini_client"
    )
