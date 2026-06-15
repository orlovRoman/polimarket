import pytest
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch
from core.onchain_gate import GateResult

class TestCursor(sqlite3.Cursor):
    def execute(self, sql, *params):
        if isinstance(sql, str) and "PRAGMA foreign_keys" in sql:
            return super().execute("PRAGMA foreign_keys = OFF")
        return super().execute(sql, *params)

class TestConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cursor_factory = TestCursor

    def execute(self, sql, *params):
        if isinstance(sql, str) and "PRAGMA foreign_keys" in sql:
            return super().execute("PRAGMA foreign_keys = OFF")
        return super().execute(sql, *params)

@pytest.fixture(autouse=True)
def disable_foreign_keys(monkeypatch):
    """Отключает FK-проверки для всех тестов через monkeypatch."""
    original_connect = sqlite3.connect
    
    def patched_connect(*args, **kwargs):
        kwargs['factory'] = TestConnection
        return original_connect(*args, **kwargs)
    
    monkeypatch.setattr(sqlite3, "connect", patched_connect)

# Настраиваем фиктивные переменные окружения для тестов до импорта модулей
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:test_token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "12345")
os.environ.setdefault("GOOGLE_API_KEY", "test_google_key")


@pytest.fixture(autouse=True)
def mock_onchain_gate_for_tests(request):
    module_name = request.module.__name__
    if "test_onchain_gate" in module_name or "test_workflow_gate_integration" in module_name:
        yield
        return
        
    with patch("core.onchain_gate.check_onchain_gate", return_value=GateResult(allow=True, reason="Mocked gate pass", blocked_by="pass")):
        yield


@pytest.fixture(autouse=True)
def restore_db_paths_each_test(request):
    """Восстанавливает оригинальный путь к БД перед каждым тестом, если его перезаписали."""
    import config
    import agents.shared.python.db as db_module
    
    temp_path = getattr(request.module, "temp_db_path", None)
    if temp_path:
        default_path = Path(temp_path)
    else:
        default_path = Path("vault/database.sqlite")
    
    # Если путь был изменен предыдущим тестом без monkeypatch, возвращаем дефолтный
    if getattr(config, "DB_PATH", None) != default_path:
        config.DB_PATH = default_path
    if getattr(db_module, "DB_PATH", None) != default_path:
        db_module.DB_PATH = default_path
        db_module._db_initialized = False  # Сбрасываем флаг инициализации для переподключения
        
    yield


@pytest.fixture(autouse=True)
def clean_database_garbage():
    """Очищает тестовый мусор из базы данных после каждого теста."""
    yield
    import agents.shared.python.db as db_module
    try:
        with db_module.get_connection() as conn:
            db_module.cleanup_test_data(conn)
    except Exception:
        pass

