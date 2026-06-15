import pytest
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch
from core.onchain_gate import GateResult

# Глобально оборачиваем sqlite3.connect для отключения проверок внешних ключей в тестах
class SQLiteCursorProxy:
    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, *params):
        if isinstance(sql, str) and "PRAGMA foreign_keys" in sql:
            return self._cur.execute("PRAGMA foreign_keys = OFF")
        return self._cur.execute(sql, *params)

    def __getattr__(self, name):
        return getattr(self._cur, name)

    def __setattr__(self, name, value):
        if name == "_cur":
            super().__setattr__(name, value)
        else:
            setattr(self._cur, name, value)

class SQLiteConnectionProxy:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *params):
        if isinstance(sql, str) and "PRAGMA foreign_keys" in sql:
            return self._conn.execute("PRAGMA foreign_keys = OFF")
        return self._conn.execute(sql, *params)

    def cursor(self, *args, **kwargs):
        cur = self._conn.cursor(*args, **kwargs)
        return SQLiteCursorProxy(cur)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name == "_conn":
            super().__setattr__(name, value)
        else:
            setattr(self._conn, name, value)

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)

original_connect = sqlite3.connect
def mock_connect(*args, **kwargs):
    conn = original_connect(*args, **kwargs)
    return SQLiteConnectionProxy(conn)
sqlite3.connect = mock_connect

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

