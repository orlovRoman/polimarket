import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import pytest
import os
import sqlite3
from unittest.mock import patch
from core.onchain_gate import GateResult
import config
import agents.shared.python.db as db_module

from tests.helpers import SQLiteConnectionProxy, SQLiteCursorProxy


@pytest.fixture(autouse=True)
def disable_foreign_keys(monkeypatch):
    """Отключает FK-проверки для всех тестов через monkeypatch."""
    original_connect = sqlite3.connect
    
    def patched_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        return SQLiteConnectionProxy(conn)
    
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
    try:
        with db_module.get_connection() as conn:
            db_module.cleanup_test_data(conn)
    except Exception:
        pass


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Изолированная база данных для тестирования настроек."""
    db_path = tmp_path / "test_penny_settings.db"
    
    # Сбрасываем синглтон-провайдер кошелька, чтобы тесты не влияли друг на друга
    from agents.shared.python.wallet.factory import reset_wallet_provider
    reset_wallet_provider()
    
    # Патчим DB_PATH в config и db_module
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_db_initialized", False)
    
    db_module.init_db()
    
    yield db_path
    db_module._db_initialized = False
    reset_wallet_provider()


