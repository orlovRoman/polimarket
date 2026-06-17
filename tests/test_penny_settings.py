# tests/test_penny_settings.py
import pytest
import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import config
import agents.shared.python.db as db_module
from agents.shared.python.penny_settings_db import (
    get_penny_settings_raw,
    get_penny_stocks_config,
    update_penny_stocks_config,
    PennyStocksConfig
)
from agents.shared.python.penny_settings_service import (
    load_penny_config,
    save_penny_config,
    run_penny_preflight,
    rederive_penny_credentials
)
from agents.shared.python.penny_execution_service import (
    should_skip_penny_scan,
    passes_penny_filters,
    passes_signal_filters,
    compute_penny_bet_size,
    can_execute_penny_trade,
    execute_penny_trade,
    get_active_positions_count,
    get_today_spent_budget
)
from agents.shared.python.wallet.factory import get_wallet_provider
from agents.shared.python.wallet.models import BalanceInfo, CredentialsStatus

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

# --- Тесты settings_db ---

def test_init_penny_settings_table_creates_defaults(isolated_db):
    """Проверка, что таблица инициализируется с дефолтными настройками."""
    raw = get_penny_settings_raw()
    assert raw["trading_mode"] == "paper"
    assert raw["bet_size_usdc"] == "1.0"
    assert raw["kill_switch"] == "0"
    assert raw["auto_buy_enabled"] == "0"
    assert raw["wallet_address"] == ""

def test_get_penny_stocks_config_casts_types(isolated_db):
    """Проверка правильного приведения типов в config dataclass."""
    cfg = get_penny_stocks_config()
    assert isinstance(cfg, PennyStocksConfig)
    assert cfg.trading_mode == "paper"
    assert cfg.bet_size_usdc == 1.0
    assert cfg.max_bet_size_usdc == 5.0
    assert cfg.max_open_positions == 10
    assert cfg.daily_budget_usdc == 20.0
    assert cfg.min_probability == 0.01
    assert cfg.max_probability == 0.09
    assert cfg.auto_buy_enabled is False
    assert cfg.kill_switch is False

def test_get_penny_stocks_config_fallback_on_invalid_data(isolated_db):
    """Проверка безопасного fallback при неконсистентных лимитах (bet_size > budget)."""
    # Сохраняем неконсистентные настройки вручную
    with db_module.get_connection() as conn:
        conn.execute("UPDATE penny_settings SET value = '50.0' WHERE key = 'bet_size_usdc'")
        conn.execute("UPDATE penny_settings SET value = '10.0' WHERE key = 'daily_budget_usdc'")
        
    cfg = get_penny_stocks_config()
    # Так как bet_size > daily_budget, должен сработать fallback с включенным kill_switch=True
    assert cfg.kill_switch is True
    assert cfg.bet_size_usdc == 1.0
    assert cfg.daily_budget_usdc == 20.0

def test_update_penny_stocks_config_whitelist_and_audit(isolated_db):
    """Проверка частичного обновления, whitelist полей и ведения аудита."""
    updates = {
        "bet_size_usdc": "2.5",
        "invalid_secret_key": "some_value"  # Должен быть проигнорирован
    }
    
    res = update_penny_stocks_config(updates, changed_by="test_user", source="test_runner")
    assert "bet_size_usdc" in res["updated_keys"]
    assert "invalid_secret_key" not in res["updated_keys"]
    assert res["config"].bet_size_usdc == 2.5
    
    # Проверяем аудит в БД
    with db_module.get_connection() as conn:
        audit = conn.execute("SELECT * FROM penny_settings_audit WHERE key = 'bet_size_usdc'").fetchone()
        assert audit is not None
        assert audit["changed_by"] == "test_user"
        assert audit["old_value"] == "1.0"
        assert audit["new_value"] == "2.5"
        assert audit["source"] == "test_runner"

# --- Тесты Wallet Provider ---

def test_paper_wallet_provider_returns_mocks():
    """Проверка, что PaperWalletProvider возвращает верные моки."""
    provider = get_wallet_provider()
    # При сброшенном APP_MODE это всегда PaperWalletProvider
    assert not provider.is_live()
    
    balance = provider.preflight_check()
    assert isinstance(balance, BalanceInfo)
    assert balance.is_mock is True
    assert balance.provider_mode == "paper"
    assert balance.usdc_balance == 1000.0
    assert balance.allowance_ok is True
    
    creds = provider.get_credentials()
    assert isinstance(creds, CredentialsStatus)
    assert creds.is_mock is True
    assert creds.api_key == "paper-api-key"

# --- Тесты penny_settings_service ---

def test_load_penny_config(isolated_db):
    """Проверка сборки конфигурации и метаданных."""
    data = load_penny_config()
    assert data["ok"] is True
    assert data["meta"]["app_mode"] == "paper"
    assert data["meta"]["is_mock"] is True
    assert data["meta"]["live_capable"] is False
    assert data["config"]["trading_mode"] == "paper"

def test_save_penny_config_validation_blocks_live_in_paper_app(isolated_db, monkeypatch):
    """Проверка запрета перевода trading_mode=live при APP_MODE=paper."""
    monkeypatch.setattr(config, "APP_MODE", "paper")
    
    with pytest.raises(ValueError, match="Нельзя включить trading_mode=live"):
        save_penny_config({"trading_mode": "live"})
        
    with pytest.raises(ValueError, match="Нельзя включить live_trading_enabled"):
        save_penny_config({"live_trading_enabled": "1"})

def test_save_penny_config_validation_blocks_autobuy_with_empty_wallet(isolated_db):
    """Проверка запрета включения автопокупки при пустом кошельке."""
    with pytest.raises(ValueError, match="требует заполненного wallet_address"):
        save_penny_config({"auto_buy_enabled": "1"})

def test_run_penny_preflight_in_paper(isolated_db):
    """Проверка запуска префлайта в paper режиме."""
    # Записываем кошелек, чтобы убрать ошибку кошелька
    save_penny_config({"wallet_address": "0x123456789"})
    
    res = run_penny_preflight()
    assert res["ok"] is True  # в paper режиме preflight успешно проходит
    assert res["is_mock"] is True
    assert res["provider_mode"] == "paper"
    assert len(res["warnings"]) == 1
    assert "Paper Provider" in res["warnings"][0]
    
    # Проверяем запись в runtime_state
    with db_module.get_connection() as conn:
        ok_row = conn.execute("SELECT value FROM penny_runtime_state WHERE key = 'last_preflight_ok'").fetchone()
        assert ok_row["value"] == "1"

def test_rederive_penny_credentials(isolated_db):
    """Проверка вызова rederive-creds."""
    res = rederive_penny_credentials()
    assert res["ok"] is True
    assert res["is_mock"] is True
    assert "Simulated" in res["message"]

# --- Тесты penny_execution_service ---

def test_should_skip_penny_scan(isolated_db):
    """Проверка флага пропуска сканирования."""
    cfg = get_penny_stocks_config()
    assert should_skip_penny_scan(cfg) is False
    
    save_penny_config({"kill_switch": "1"})
    cfg_kill = get_penny_stocks_config()
    assert should_skip_penny_scan(cfg_kill) is True

class MockMarket:
    def __init__(self, price, volume_24h, close_time_hours):
        self.price = price
        self.volume_24h = volume_24h
        self.close_time = datetime.now(timezone.utc) + timedelta(hours=close_time_hours)

def test_passes_penny_filters(isolated_db):
    """Проверка фильтрации дешевых рынков (цена, объем, время закрытия)."""
    cfg = get_penny_stocks_config()
    # defaults: max_prob=0.09, min_volume=50, min_hours=2, max_hours=168
    
    # 1. Корректный рынок (YES = 5¢, объем 100$, закроется через 10 часов)
    m_ok = MockMarket(price=0.05, volume_24h=100.0, close_time_hours=10)
    assert passes_penny_filters(m_ok, cfg) is True
    
    # 2. Слишком дорогой рынок (YES = 15¢)
    m_expensive = MockMarket(price=0.15, volume_24h=100.0, close_time_hours=10)
    assert passes_penny_filters(m_expensive, cfg) is False
    
    # 3. Рынок NO прошел фильтр (YES = 95¢, значит NO = 5¢)
    m_no_cheap = MockMarket(price=0.95, volume_24h=100.0, close_time_hours=10)
    assert passes_penny_filters(m_no_cheap, cfg) is True
    
    # 4. Недостаточный объем торгов (10$)
    m_low_vol = MockMarket(price=0.05, volume_24h=10.0, close_time_hours=10)
    assert passes_penny_filters(m_low_vol, cfg) is False
    
    # 5. Закрывается слишком рано (через 1 час)
    m_early = MockMarket(price=0.05, volume_24h=100.0, close_time_hours=1.0)
    assert passes_penny_filters(m_early, cfg) is False

def test_passes_signal_filters(isolated_db):
    """Проверка фильтрации сигналов агентов (confidence, target_outcome)."""
    cfg = get_penny_stocks_config()
    
    # Сигнал с высоким confidence и низкой ценой
    sig_ok = {"target_outcome": "YES", "probability": 0.05, "confidence": 0.7}
    assert passes_signal_filters(sig_ok, cfg) is True
    
    # Слишком низкий confidence (0.3 < 0.5)
    sig_low_conf = {"target_outcome": "YES", "probability": 0.05, "confidence": 0.3}
    assert passes_signal_filters(sig_low_conf, cfg) is False
    
    # Вероятность YES не проходит фильтр (YES=95¢ при target YES)
    sig_expensive = {"target_outcome": "YES", "probability": 0.95, "confidence": 0.7}
    assert passes_signal_filters(sig_expensive, cfg) is False
    
    # Вероятность NO проходит фильтр (YES=95¢ при target NO, эффективная цена NO = 5¢)
    sig_no_cheap = {"target_outcome": "NO", "probability": 0.95, "confidence": 0.7}
    assert passes_signal_filters(sig_no_cheap, cfg) is True

def test_compute_penny_bet_size(isolated_db):
    """Проверка расчета ставки и ограничений по лимитам/бюджету."""
    cfg = get_penny_stocks_config()
    # defaults: bet_size=1.0, max_bet=5.0, min_conf=0.5
    
    # 1. Базовый расчет (confidence = 0.5 == min_conf) -> ставка = base = 1.0
    sig_base = {"confidence": 0.5}
    assert compute_penny_bet_size(sig_base, cfg) == pytest.approx(1.0)
    
    # 2. Масштабированный расчет (confidence = 1.0) -> scaled = base * 2 = 2.0
    sig_scaled = {"confidence": 1.0}
    assert compute_penny_bet_size(sig_scaled, cfg) == pytest.approx(2.0)
    
    # 3. Ниже min_confidence -> ставка = 0.0
    sig_low = {"confidence": 0.4}
    assert compute_penny_bet_size(sig_low, cfg) == pytest.approx(0.0)

def test_can_execute_penny_trade(isolated_db):
    """Проверка логики блокировки выполнения сделки."""
    cfg = get_penny_stocks_config()
    sig = {"target_outcome": "YES", "probability": 0.05, "confidence": 0.7}
    
    # По умолчанию автопокупка выключена
    assert can_execute_penny_trade(sig, cfg) is False
    
    # Включаем автопокупку и задаем кошелек
    save_penny_config({
        "wallet_address": "0x12345",
        "auto_buy_enabled": "1"
    })
    cfg_active = get_penny_stocks_config()
    
    # Должен пройти
    assert can_execute_penny_trade(sig, cfg_active) is True
    
    # При kill_switch=True блокируется
    save_penny_config({"kill_switch": "1"})
    cfg_kill = get_penny_stocks_config()
    assert can_execute_penny_trade(sig, cfg_kill) is False

# --- Новые тесты для баг-фиксов ---

def test_update_does_not_raise_on_nested_transaction(isolated_db):
    """Обновление настроек не должно падать с ошибкой транзакции при повторных вызовах."""
    for _ in range(3):
        result = update_penny_stocks_config({"bet_size_usdc": "2.0"})
        assert result["config"].bet_size_usdc == 2.0
    # откатываем обратно
    result = update_penny_stocks_config({"bet_size_usdc": "1.0"})
    assert result["config"].bet_size_usdc == 1.0

def test_today_budget_independent_of_current_bet_size(isolated_db):
    """Потраченный бюджет считается по фактическим ставкам, а не по текущей базовой ставке."""
    cfg = get_penny_stocks_config()
    update_penny_stocks_config({"wallet_address": "0x123", "auto_buy_enabled": "1"})
    cfg_active = get_penny_stocks_config()
    
    # Сначала добавляем рынки в мониторинг, чтобы UPDATE сработал
    db_module.add_penny_stock_to_monitoring("mkt_test_budget_1", "Test Budget 1", "http://test1", 0.05, predicted_outcome="YES", confidence=0.5)
    db_module.add_penny_stock_to_monitoring("mkt_test_budget_2", "Test Budget 2", "http://test2", 0.04, predicted_outcome="YES", confidence=0.5)
    
    # Делаем 2 виртуальные сделки при ставке 1.0 USDC
    sig1 = {"target_outcome": "YES", "probability": 0.05, "confidence": 0.5, "price": 0.05}
    sig2 = {"target_outcome": "YES", "probability": 0.04, "confidence": 0.5, "price": 0.04}
    
    assert execute_penny_trade("mkt_test_budget_1", sig1, cfg_active) is True
    assert execute_penny_trade("mkt_test_budget_2", sig2, cfg_active) is True
    
    # Проверяем бюджет: должно быть 2 * 1.0 = 2.0
    assert get_today_spent_budget() == pytest.approx(2.0)
    
    # Меняем ставку на 5.0
    update_penny_stocks_config({"bet_size_usdc": "5.0"})
    
    # Потраченный бюджет должен остаться 2.0, а не увеличиться до 10.0
    assert get_today_spent_budget() == pytest.approx(2.0)

def test_wallet_provider_singleton_resets_on_mode_change(isolated_db, monkeypatch):
    """При изменении APP_MODE синглтон пересоздаётся."""
    import agents.shared.python.wallet.factory as fct
    from agents.shared.python.wallet.factory import reset_wallet_provider
    reset_wallet_provider()
    
    monkeypatch.setattr(config, "APP_MODE", "paper")
    p1 = fct.get_wallet_provider()
    assert not p1.is_live()
    
    # Включаем live_trading_enabled в БД, чтобы сработал переход на live провайдер при APP_MODE=live
    update_penny_stocks_config({"live_trading_enabled": "1"})
    
    # Сбрасываем и меняем режим — live должен упасть без ключей
    reset_wallet_provider()
    monkeypatch.setattr(config, "APP_MODE", "live")
    monkeypatch.delenv("PRIVATE_KEY", raising=False)
    monkeypatch.delenv("DEPOSIT_WALLET_ADDRESS", raising=False)
    with pytest.raises(ValueError, match="секретов"):
        fct.get_wallet_provider()


def test_execute_penny_trade_no_outcome_price(isolated_db):
    """При target_outcome=NO цена должна быть 1.0 - m.price, а не m.price."""
    cfg = get_penny_stocks_config()
    update_penny_stocks_config({"wallet_address": "0x123", "auto_buy_enabled": "1"})
    cfg_active = get_penny_stocks_config()
    
    # Сначала добавляем рынок в мониторинг
    db_module.add_penny_stock_to_monitoring("test-market-no", "Test No Price", "http://testno", 0.95, predicted_outcome="NO", confidence=0.5)
    
    # Сигнал указывает на покупку NO, а YES-цена (m.price) = 0.95.
    # Значит, эффективная цена NO = 0.05
    sig_no = {"target_outcome": "NO", "probability": 0.95, "confidence": 0.5, "price": 0.05}
    result = execute_penny_trade("test-market-no", sig_no, cfg_active)
    assert result is True
    
    # Проверяем, что в БД цена покупки 0.05
    with db_module.get_connection() as conn:
        row = conn.execute(
            "SELECT virtual_bought_price FROM penny_stocks_monitoring WHERE market_id = 'test-market-no'"
        ).fetchone()
        assert row is not None
        assert row["virtual_bought_price"] == pytest.approx(0.05)

def test_no_double_count_on_sell(isolated_db):
    """При продаже позиции бюджет не должен считаться дважды."""
    update_penny_stocks_config({"auto_buy_enabled": "1", "bet_size_usdc": "2.0", "wallet_address": "0x123"})
    cfg = get_penny_stocks_config()
    db_module.add_penny_stock_to_monitoring(
        "mkt_double", "Double Count Test", "http://x", 0.05,
        predicted_outcome="YES", confidence=0.6
    )
    sig = {"target_outcome": "YES", "probability": 0.05, "confidence": 0.6, "price": 0.05}
    execute_penny_trade("mkt_double", sig, cfg)

    # До продажи: 2.4 (открытая позиция, масштабированная по confidence=0.6)
    assert get_today_spent_budget() == pytest.approx(2.4)

    # Продаем
    db_module.sell_virtual_penny_stock("mkt_double")

    # После продажи: все равно 2.4, но теперь из истории
    assert get_today_spent_budget() == pytest.approx(2.4)

@pytest.mark.asyncio
async def test_get_config_without_reset_does_not_modify(isolated_db):
    """GET /api/penny-stocks/config без ?reset не сбрасывает настройки."""
    from aiohttp.test_utils import TestClient, TestServer
    from web.dashboard import create_dashboard_app
    update_penny_stocks_config({"bet_size_usdc": "3.5"})
    
    app = create_dashboard_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/penny-stocks/config")
        assert resp.status == 200
        data = await resp.json()
        assert data["config"]["bet_size_usdc"] == pytest.approx(3.5)

@pytest.mark.asyncio
async def test_post_reset_endpoint_applies_defaults(isolated_db):
    """POST /api/penny-stocks/config/reset применяет PENNY_DEFAULTS."""
    from aiohttp.test_utils import TestClient, TestServer
    from web.dashboard import create_dashboard_app
    update_penny_stocks_config({"bet_size_usdc": "3.5", "max_open_positions": "25"})
    
    app = create_dashboard_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/penny-stocks/config/reset")
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert data["config"]["bet_size_usdc"] == pytest.approx(1.0)
        assert data["config"]["max_open_positions"] == 10

def test_migration_fills_bet_size_for_open_positions(isolated_db):
    """После миграции открытые позиции получают актуальный bet_size из конфига."""
    update_penny_stocks_config({"bet_size_usdc": "3.0"})
    
    # Эмулируем старую открытую позицию без bet_size
    with db_module.get_connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO penny_stocks_monitoring (market_id, title, url, initial_price, current_price, max_price_seen, min_price_seen, status, virtual_bought_price, virtual_bought_at, bet_size_usdc)
            VALUES ('legacy_market', 'Legacy Market', 'http://legacy', 0.05, 0.05, 0.05, 0.05, 'ACTIVE', 0.05, CURRENT_TIMESTAMP, NULL)
        """)
        conn.execute("UPDATE penny_stocks_monitoring SET bet_size_usdc = NULL WHERE market_id = 'legacy_market'")
        
    # Перезапускаем инициализацию
    db_module._db_initialized = False
    db_module.init_db()
    
    with db_module.get_connection() as conn:
        row = conn.execute(
            "SELECT bet_size_usdc FROM penny_stocks_monitoring WHERE market_id = 'legacy_market'"
        ).fetchone()
        
    assert row is not None
    assert row["bet_size_usdc"] == pytest.approx(3.0)
