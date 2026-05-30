import inspect
import pytest

def test_no_requests_import():
    """import requests должен быть удалён после рефакторинга на httpx."""
    import services.telegram_listener as tl
    source = inspect.getsource(tl)
    # Ищем именно строку импорта, не вхождение в строках
    import_lines = [l.strip() for l in source.splitlines()
                    if l.strip().startswith('import ') or l.strip().startswith('from ')]
    assert not any('import requests' in l for l in import_lines), (
        "Мёртвый 'import requests' найден — удалите его, "
        "в среде без requests это вызовет ModuleNotFoundError"
    )

def test_no_datetime_import():
    """from datetime import datetime не используется и должен быть удалён."""
    import services.telegram_listener as tl
    source = inspect.getsource(tl)
    if 'from datetime import datetime' in source:
        # Проверяем, что datetime реально используется в коде (не только в импорте)
        uses = [l for l in source.splitlines()
                if 'datetime' in l and not l.strip().startswith('from') and not l.strip().startswith('#')]
        assert uses, (
            "'from datetime import datetime' импортирован но нигде не используется — удалите"
        )
