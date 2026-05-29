import json
import sqlite3
import sys
import types
import importlib
from pathlib import Path
from datetime import datetime, timezone
import pytest

@pytest.fixture()
def db_module(tmp_path):
    """
    Загружает db.py в изолированном окружении с временной БД.
    Сбрасывает _db_initialized и _db_initializing перед каждым тестом.
    """
    db_file = tmp_path / "test_lists.db"

    original_config = sys.modules.get("config")
    original_core = sys.modules.get("core")
    original_core_models = sys.modules.get("core.models")

    fake_config = types.ModuleType("config")
    fake_config.DB_PATH = db_file
    sys.modules["config"] = fake_config

    core_pkg = types.ModuleType("core")
    core_models = types.ModuleType("core.models")
    for cls_name in ("Market", "Signal", "MarketCorrelation"):
        setattr(core_models, cls_name, object)
    core_pkg.models = core_models
    sys.modules["core"] = core_pkg
    sys.modules["core.models"] = core_models

    db_mod_path = str(
        Path(__file__).parent.parent / "agents" / "shared" / "python" / "db.py"
    )
    spec = importlib.util.spec_from_file_location("db_fresh", db_mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    mod._db_initialized = False
    mod._db_initializing = False

    yield mod

    sys.modules.pop("db_fresh", None)
    if original_config:
        sys.modules["config"] = original_config
    else:
        sys.modules.pop("config", None)
        
    if original_core:
        sys.modules["core"] = original_core
    else:
        sys.modules.pop("core", None)
        
    if original_core_models:
        sys.modules["core.models"] = original_core_models
    else:
        sys.modules.pop("core.models", None)


def test_add_and_check_ignored(db_module):
    """Тестирует добавление и проверку в списке игнорируемых."""
    market_id = "mkt_ignored_123"
    market_title = "Ignored Market Title"
    
    # Сначала рынок не в списке
    assert not db_module.is_in_market_list(market_id, "ignored")
    
    # Добавляем в список
    db_module.add_to_market_list(market_id, market_title, "ignored", None)
    
    # Теперь рынок должен быть в списке
    assert db_module.is_in_market_list(market_id, "ignored")
    assert not db_module.is_in_market_list(market_id, "watching")
    
    # Проверяем get_market_list
    lst = db_module.get_market_list("ignored")
    assert len(lst) == 1
    assert lst[0]["market_id"] == market_id
    assert lst[0]["market_title"] == market_title


def test_add_and_check_watching(db_module):
    """Тестирует добавление и проверку в списке наблюдения (watchlist)."""
    market_id = "mkt_watching_123"
    market_title = "Watching Market Title"
    base_price = 0.55
    
    # Сначала рынок не в списке
    assert not db_module.is_in_market_list(market_id, "watching")
    
    # Добавляем в список
    db_module.add_to_market_list(market_id, market_title, "watching", base_price)
    
    # Теперь рынок должен быть в списке
    assert db_module.is_in_market_list(market_id, "watching")
    assert not db_module.is_in_market_list(market_id, "ignored")
    
    # Проверяем get_market_list
    lst = db_module.get_market_list("watching")
    assert len(lst) == 1
    assert lst[0]["market_id"] == market_id
    assert lst[0]["market_title"] == market_title
    assert lst[0]["base_price"] == base_price
    assert lst[0]["last_price"] == base_price


def test_duplicate_add_is_idempotent(db_module):
    """Проверяет, что повторное добавление рынка обновляет запись и не вызывает ошибок уникальности."""
    market_id = "mkt_dup_123"
    db_module.add_to_market_list(market_id, "Title 1", "ignored", None)
    db_module.add_to_market_list(market_id, "Title 2", "ignored", None)
    
    lst = db_module.get_market_list("ignored")
    assert len(lst) == 1
    assert lst[0]["market_title"] == "Title 2"


def test_remove_from_list(db_module):
    """Проверяет удаление рынка из списков."""
    market_id = "mkt_remove_123"
    db_module.add_to_market_list(market_id, "Title", "ignored", None)
    assert db_module.is_in_market_list(market_id, "ignored")
    
    removed = db_module.remove_from_market_list(market_id, "ignored")
    assert removed == 1
    assert not db_module.is_in_market_list(market_id, "ignored")
    
    # Удаление несуществующего рынка возвращает 0
    removed_nonexistent = db_module.remove_from_market_list("non_existent", "ignored")
    assert removed_nonexistent == 0


def test_watchlist_price_update(db_module):
    """Проверяет обновление последней цены в watchlist."""
    market_id = "mkt_price_123"
    db_module.add_to_market_list(market_id, "Title", "watching", 0.50)
    
    db_module.update_watchlist_price(market_id, 0.75)
    
    lst = db_module.get_market_list("watching")
    assert len(lst) == 1
    assert lst[0]["last_price"] == 0.75
    # Базовая цена не должна измениться
    assert lst[0]["base_price"] == 0.50


def test_market_selector_filters_lists(db_module):
    """Проверяет, что MarketSelector исключает рынки из списков 'ignored' и 'watching'."""
    # Создаем фиктивный адаптер
    class FakeMarket:
        def __init__(self, id, title, price, close_time):
            self.id = id
            self.title = title
            self.price = price
            self.close_time = close_time
            self.url = f"https://polymarket.com/market/{id}"

    now = datetime.now(timezone.utc)
    future_time = now + __import__('datetime').timedelta(days=10)
    
    m_normal = FakeMarket("normal_1", "Normal Market", 0.5, future_time)
    m_ignored = FakeMarket("ignored_1", "Ignored Market", 0.5, future_time)
    m_watching = FakeMarket("watching_1", "Watching Market", 0.5, future_time)
    
    # Добавляем в списки во временной БД
    db_module.add_to_market_list(m_ignored.id, m_ignored.title, "ignored", None)
    db_module.add_to_market_list(m_watching.id, m_watching.title, "watching", 0.5)
    
    # Патчим импорт db в market_selector, чтобы он читал из нашей тестовой db_module
    sys.modules["agents.shared.python.db"] = db_module
    
    # Временно восстанавливаем оригинальные модули
    original_config = sys.modules.get("config")
    original_core = sys.modules.get("core")
    original_core_models = sys.modules.get("core.models")
    
    sys.modules.pop("config", None)
    sys.modules.pop("core", None)
    sys.modules.pop("core.models", None)
    sys.modules.pop("core.guards", None)

    db_mod_path = str(
        Path(__file__).parent.parent / "agents" / "shared" / "python" / "market_selector.py"
    )
    spec = importlib.util.spec_from_file_location("market_selector_fresh", db_mod_path)
    selector_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selector_mod)
    
    # Возвращаем все обратно
    if original_config:
        sys.modules["config"] = original_config
    else:
        sys.modules.pop("config", None)
        
    if original_core:
        sys.modules["core"] = original_core
    else:
        sys.modules.pop("core", None)
        
    if original_core_models:
        sys.modules["core.models"] = original_core_models
    else:
        sys.modules.pop("core.models", None)

    class FakeAdapter:
        pass
        
    selector = selector_mod.MarketSelector(FakeAdapter())
    
    # Прогоняем метод _filter напрямую
    filtered = selector._filter([m_normal, m_ignored, m_watching])
    
    assert len(filtered) == 1
    assert filtered[0].id == "normal_1"
    
    # Очищаем sys.modules
    sys.modules.pop("agents.shared.python.db", None)
