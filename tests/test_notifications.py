"""
Тесты для services/notifications.py — Слой #11

Покрывают 4 исправленных бага:
  Баг #1 — model_validate с фильтром вместо **row
  Баг #2 — p_in_corridor=0.0 не показывается в алерте
  Баг #3 — mark_correlations_notified в finally
  Баг #4 — pnl=0.0 показывается как "+0.0%", не "N/A"
"""
import sys
import types
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


# ──────────────────────────────────────────────────────────────────────────────
# Фикстуры
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def patch_config():
    """Подставляем фейковый конфиг для всех тестов."""
    original_config = sys.modules.get("config")
    import logging
    fake_config = types.ModuleType("config")
    fake_config.TELEGRAM_BOT_TOKEN = "fake_token"
    fake_config.TELEGRAM_CHAT_ID = "12345"
    fake_config.logger = logging.getLogger("TEST_NOTIFIER")
    sys.modules["config"] = fake_config
    yield
    if original_config:
        sys.modules["config"] = original_config
    else:
        sys.modules.pop("config", None)


@pytest.fixture()
def fake_db():
    original_modules = {key: sys.modules.get(key) for key in [
        "config",
        "agents.shared.python.db",
        "agents.shared.adapters.polymarket",
        "agents.polymarket_arbitrage_agent",
        "agents.polymarket_arbitrage_agent.src",
        "agents.polymarket_arbitrage_agent.src.agent",
        "agents.shared.adapters",
        "agents.shared.python",
        "agents.shared",
        "agents",
    ]}

    # Базовые пакеты-предки
    sys.modules["agents"] = types.ModuleType("agents")
    sys.modules["agents.shared"] = types.ModuleType("agents.shared")
    sys.modules["agents.shared.python"] = types.ModuleType("agents.shared.python")

    # Фейковый db-модуль
    db_mod = types.ModuleType("agents.shared.python.db")
    db_mod.get_new_cross_arbitrage_signals = MagicMock(return_value=[])
    db_mod.mark_cross_arbitrage_alerted = MagicMock()
    db_mod.get_new_correlations = MagicMock(return_value=[])
    db_mod.mark_correlations_notified = MagicMock()
    sys.modules["agents.shared.python.db"] = db_mod

    # Фейковый PolymarketAdapter
    mock_adapter_instance = MagicMock()
    mock_adapter_instance.get_market.return_value = None
    adapter_mod = types.ModuleType("agents.shared.adapters.polymarket")
    adapter_mod.PolymarketAdapter = MagicMock(return_value=mock_adapter_instance)
    sys.modules["agents.shared.adapters"] = types.ModuleType("agents.shared.adapters")
    sys.modules["agents.shared.adapters.polymarket"] = adapter_mod

    # Фейковый ArbitrageAgent
    arb_agent_mod = types.ModuleType("agents.polymarket_arbitrage_agent")
    arb_src_mod = types.ModuleType("agents.polymarket_arbitrage_agent.src")
    arb_agent_src_mod = types.ModuleType("agents.polymarket_arbitrage_agent.src.agent")
    arb_agent_src_mod.ArbitrageAgent = MagicMock()
    sys.modules["agents.polymarket_arbitrage_agent"] = arb_agent_mod
    sys.modules["agents.polymarket_arbitrage_agent.src"] = arb_src_mod
    sys.modules["agents.polymarket_arbitrage_agent.src.agent"] = arb_agent_src_mod

    # Фейковый os.getenv — возвращает API-ключ
    import os
    original_getenv = os.getenv
    os.getenv = lambda key, default=None: "fake-key" if key == "GOOGLE_API_KEY" else original_getenv(key, default)

    yield db_mod

    # Очистка
    os.getenv = original_getenv
    for key, val in original_modules.items():
        if val is not None:
            sys.modules[key] = val
        else:
            sys.modules.pop(key, None)


# ──────────────────────────────────────────────────────────────────────────────
# Вспомогательные объекты
# ──────────────────────────────────────────────────────────────────────────────

def _make_cross_signal_row(**overrides):
    """Минимальный dict-ряд из SQLite для CrossArbitrageSignal."""
    base = {
        "id": "poly-1__kalshi-1",
        "market_a_id": "poly-1", "market_a_platform": "polymarket",
        "market_a_title": "Market A", "market_a_price": 0.65,
        "market_a_url": "https://poly.market/a",
        "market_b_id": "kalshi-1", "market_b_platform": "kalshi",
        "market_b_title": "Market B", "market_b_price": 0.40,
        "market_b_url": "https://kalshi.com/b",
        "has_arbitrage": True, "arbitrage_type": "price_divergence",
        "spread_percent": 12.5, "reasoning": "Gap", "trade_instruction": "Buy A",
        "match_score": 0.88, "status": "new",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "action_a": "BUY_YES", "action_b": "BUY_NO",
        "entry_price_a_cents": "65", "entry_price_b_cents": "40",
        "expected_pnl_pct": None, "risk_level": "LOW",
    }
    base.update(overrides)
    return base


class FakeLeg:
    def __init__(self, expiry=None, cost=0.45):
        self.expiry = expiry or datetime(2026, 8, 1, tzinfo=timezone.utc)
        self.entry_cost = cost


class FakeTemporalSignal:
    event_title = "Fed Rate Decision"
    event_url = "https://poly.market/event/fed"
    early_leg = FakeLeg()
    late_leg = FakeLeg()
    p_in_corridor = 0.0
    date_gap_days = 30
    real_spread_pct = 7.8
    quality_score = 0.82
    ev_usd = 15.0
    early_stake_usd = 50.0
    late_stake_usd = 50.0
    exit_rule = "Exit when early expires"
    pnl_s1_before_early = -5.0
    pnl_s2_in_corridor = 22.0
    pnl_s3_never = -8.0


# ──────────────────────────────────────────────────────────────────────────────
# Баг #1 — model_validate игнорирует лишние поля из SQLite
# ──────────────────────────────────────────────────────────────────────────────

class TestCrossArbitrageDeserialization:

    def test_extra_db_columns_do_not_raise_validation_error(self, fake_db):
        """Лишние колонки из БД (rowid, future migrations) не вызывают ValidationError."""
        import services.notifications as n

        row = _make_cross_signal_row(unknown_future_column="oops", rowid=42)
        fake_db.get_new_cross_arbitrage_signals.return_value = [row]

        with patch.object(n, "send_telegram", return_value=True):
            n.send_cross_arbitrage_alerts(min_spread=5.0)
        # Главное что выше не бросило ValidationError

    def test_signal_id_not_passed_as_field(self, fake_db):
        """Поле id (PK из БД) не передаётся в модель — mark вызывается с правильным id."""
        import services.notifications as n

        row = _make_cross_signal_row()
        fake_db.get_new_cross_arbitrage_signals.return_value = [row]

        with patch.object(n, "send_telegram", return_value=True):
            n.send_cross_arbitrage_alerts(min_spread=5.0)

        fake_db.mark_cross_arbitrage_alerted.assert_called_once_with("poly-1__kalshi-1")

    def test_empty_signals_returns_early(self, fake_db):
        """Если новых сигналов нет — функция выходит без вызова send_telegram."""
        import services.notifications as n

        fake_db.get_new_cross_arbitrage_signals.return_value = []

        with patch.object(n, "send_telegram") as mock_send:
            n.send_cross_arbitrage_alerts()

        mock_send.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# Баг #2 — p_in_corridor=0.0 скрывается из алерта
# ──────────────────────────────────────────────────────────────────────────────

class TestTemporalCorridorFormat:

    def test_p_in_corridor_zero_hidden(self):
        """p_in_corridor=0.0 → строка 'P(коридор)=0%' отсутствует."""
        import services.notifications as n

        sig = FakeTemporalSignal()
        sig.p_in_corridor = 0.0
        result = n.format_temporal_corridor_alert(sig)

        assert "P(" not in result or "0%" not in result
        assert "gap=" in result

    def test_p_in_corridor_nonzero_shown(self):
        """p_in_corridor=0.65 → строка 'P(коридор)=65%' присутствует."""
        import services.notifications as n

        sig = FakeTemporalSignal()
        sig.p_in_corridor = 0.65
        result = n.format_temporal_corridor_alert(sig)

        assert "65%" in result

    def test_p_in_corridor_small_nonzero_shown(self):
        """p_in_corridor=0.01 (1%) — всё равно показывается."""
        import services.notifications as n

        sig = FakeTemporalSignal()
        sig.p_in_corridor = 0.01
        result = n.format_temporal_corridor_alert(sig)

        assert "1%" in result


# ──────────────────────────────────────────────────────────────────────────────
# Баг #3 — mark_correlations_notified вызывается в finally
# ──────────────────────────────────────────────────────────────────────────────

class TestCorrelationAlerts:

    def test_mark_called_even_when_agent_raises(self, fake_db):
        """При исключении — mark_correlations_notified всё равно вызывается."""
        import services.notifications as n

        fake_corrs = [
            {"id": 10, "market_id_a": "mkt-a", "market_id_b": "mkt-b",
             "correlation_type": "causal", "confidence": 0.9},
        ]
        fake_db.get_new_correlations.return_value = fake_corrs
        # ArbitrageAgent уже заглушен через sys.modules в фикстуре fake_db
        sys.modules["agents.polymarket_arbitrage_agent.src.agent"].ArbitrageAgent.side_effect = RuntimeError("LLM down")

        n.send_correlation_alerts()

        fake_db.mark_correlations_notified.assert_called_once_with([10])

    def test_mark_not_called_when_no_correlations(self, fake_db):
        """Если корреляций нет — mark не вызывается."""
        import services.notifications as n

        fake_db.get_new_correlations.return_value = []
        n.send_correlation_alerts()

        fake_db.mark_correlations_notified.assert_not_called()

    def test_mark_called_on_success_too(self, fake_db):
        """При успешном выполнении mark тоже вызывается."""
        import services.notifications as n

        fake_corrs = [
            {"id": 20, "market_id_a": "mkt-x", "market_id_b": "mkt-y",
             "correlation_type": "thematic", "confidence": 0.7},
        ]
        fake_db.get_new_correlations.return_value = fake_corrs
        # get_market возвращает None → continue (уже настроено в fake_db)
        sys.modules["agents.polymarket_arbitrage_agent.src.agent"].ArbitrageAgent.side_effect = None

        n.send_correlation_alerts()

        fake_db.mark_correlations_notified.assert_called_once_with([20])

    def test_correlation_alert_none_arbitrage_type_skipped(self, fake_db):
        """Если тип арбитража 'none', алерт не отправляется, даже если спред >= 5.0%."""
        import services.notifications as n
        from core.models import Market, CrossArbitrageSignal

        fake_corrs = [
            {"id": 30, "market_id_a": "mkt-a", "market_id_b": "mkt-b",
             "correlation_type": "thematic", "confidence": 0.8},
        ]
        fake_db.get_new_correlations.return_value = fake_corrs

        # Настраиваем рынки
        mkt_a = Market(
            id="mkt-a", platform="polymarket", title="Gold hit $4200", price=0.56,
            url="http://a", close_time=datetime(2026, 6, 30), description="desc a", outcome="YES"
        )
        mkt_b = Market(
            id="mkt-b", platform="polymarket", title="Crude Oil hit $115", price=0.12,
            url="http://b", close_time=datetime(2026, 6, 30), description="desc b", outcome="YES"
        )
        
        # Настраиваем мок адаптера
        adapter_instance = sys.modules["agents.shared.adapters.polymarket"].PolymarketAdapter.return_value
        adapter_instance.get_market.side_effect = lambda m_id: mkt_a if m_id == "mkt-a" else mkt_b

        # Настраиваем возвращаемый сигнал от ArbitrageAgent
        signal = CrossArbitrageSignal(
            market_a_id="mkt-a", market_a_platform="polymarket", market_a_title="Gold hit $4200", market_a_price=0.56, market_a_url="http://a",
            market_b_id="mkt-b", market_b_platform="polymarket", market_b_title="Crude Oil hit $115", market_b_price=0.12, market_b_url="http://b",
            has_arbitrage=False, arbitrage_type="none", spread_percent=44.0, reasoning="No relation", trade_instruction="SKIP",
            match_score=0.8
        )
        
        mock_agent_instance = MagicMock()
        mock_agent_instance.analyze_correlation.return_value = signal
        sys.modules["agents.polymarket_arbitrage_agent.src.agent"].ArbitrageAgent.return_value = mock_agent_instance

        mock_notify = MagicMock()
        n.send_correlation_alerts(summary_callback=mock_notify)

        mock_notify.assert_not_called()
        fake_db.mark_correlations_notified.assert_called_once_with([30])

    def test_correlation_alert_valid_arbitrage_type_sent(self, fake_db):
        """Если тип арбитража не 'none' (например, 'pair_trade'), алерт отправляется при спреде >= 5.0%."""
        import services.notifications as n
        from core.models import Market, CrossArbitrageSignal

        fake_corrs = [
            {"id": 40, "market_id_a": "mkt-a", "market_id_b": "mkt-b",
             "correlation_type": "thematic", "confidence": 0.8},
        ]
        fake_db.get_new_correlations.return_value = fake_corrs

        # Настраиваем рынки
        mkt_a = Market(
            id="mkt-a", platform="polymarket", title="Gold hit $4200", price=0.56,
            url="http://a", close_time=datetime(2026, 6, 30), description="desc a", outcome="YES"
        )
        mkt_b = Market(
            id="mkt-b", platform="polymarket", title="Crude Oil hit $115", price=0.12,
            url="http://b", close_time=datetime(2026, 6, 30), description="desc b", outcome="YES"
        )
        
        # Настраиваем мок адаптера
        adapter_instance = sys.modules["agents.shared.adapters.polymarket"].PolymarketAdapter.return_value
        adapter_instance.get_market.side_effect = lambda m_id: mkt_a if m_id == "mkt-a" else mkt_b

        # Настраиваем возвращаемый сигнал от ArbitrageAgent
        signal = CrossArbitrageSignal(
            market_a_id="mkt-a", market_a_platform="polymarket", market_a_title="Gold hit $4200", market_a_price=0.56, market_a_url="http://a",
            market_b_id="mkt-b", market_b_platform="polymarket", market_b_title="Crude Oil hit $115", market_b_price=0.12, market_b_url="http://b",
            has_arbitrage=False, arbitrage_type="pair_trade", spread_percent=44.0, reasoning="No relation", trade_instruction="SKIP",
            match_score=0.8
        )
        
        mock_agent_instance = MagicMock()
        mock_agent_instance.analyze_correlation.return_value = signal
        sys.modules["agents.polymarket_arbitrage_agent.src.agent"].ArbitrageAgent.return_value = mock_agent_instance

        mock_notify = MagicMock()
        n.send_correlation_alerts(summary_callback=mock_notify)

        mock_notify.assert_called_once()
        fake_db.mark_correlations_notified.assert_called_once_with([40])


# ──────────────────────────────────────────────────────────────────────────────
# Баг #4 — pnl=0.0 показывается как "+0.0%", не "N/A"
# ──────────────────────────────────────────────────────────────────────────────

class TestCrossArbitrageFormat:

    def _make_signal(self, **kwargs):
        from core.models import CrossArbitrageSignal
        defaults = dict(
            market_a_id="poly-1", market_a_platform="polymarket",
            market_a_title="Market A", market_a_price=0.65,
            market_a_url="https://poly.market/a",
            market_b_id="kalshi-1", market_b_platform="kalshi",
            market_b_title="Market B", market_b_price=0.40,
            market_b_url="https://kalshi.com/b",
            has_arbitrage=True, arbitrage_type="price_divergence",
            spread_percent=12.5, reasoning="Price gap",
            trade_instruction="Buy A YES, Buy B NO",
            match_score=0.88,
        )
        defaults.update(kwargs)
        return CrossArbitrageSignal(**defaults)

    def test_pnl_zero_shows_plus_zero(self):
        """expected_pnl_pct=0.0 → '+0.0%', не 'N/A'."""
        import services.notifications as n

        sig = self._make_signal(
            action_a="BUY_YES", action_b="BUY_NO",
            expected_pnl_pct=0.0, risk_level="LOW",
            entry_price_a_cents=65, entry_price_b_cents=40,
        )
        result = n.format_cross_arbitrage_alert(sig)

        assert "N/A" not in result
        assert "0.0%" in result

    def test_pnl_none_shows_na(self):
        """expected_pnl_pct=None → 'N/A'."""
        import services.notifications as n

        sig = self._make_signal(
            action_a="BUY_YES", action_b="BUY_NO",
            expected_pnl_pct=None, risk_level="MEDIUM",
            entry_price_a_cents=65, entry_price_b_cents=40,
        )
        result = n.format_cross_arbitrage_alert(sig)

        assert "N/A" in result

    def test_pnl_positive_shows_value(self):
        """expected_pnl_pct=8.5 → '+8.5%'."""
        import services.notifications as n

        sig = self._make_signal(
            action_a="BUY_YES", action_b="BUY_NO",
            expected_pnl_pct=8.5, risk_level="LOW",
            entry_price_a_cents=65, entry_price_b_cents=40,
        )
        result = n.format_cross_arbitrage_alert(sig)

        assert "+8.5%" in result
