"""
Регрессионные тесты для последних 5 коммитов (2026-06-15).

Покрывает:
  1. normalize_strategy_name
  2. signals_count не перезаписывается нулём
  3. get_compound_settings: robustness при None/пустых/невалидных значениях
  4. _normalize_close_time: форматы ISO, datetime, None, мусор
  5. _should_ignore_message: возвращает tuple, chat инициализирован
  6. get_equity_curve: normalize применяется к алиасу
  7. add_penny_stock_to_monitoring: close_time передаётся явно
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime
import contextlib


# ─────────────────────────────────────────────────────────────────────────────
# 1. normalize_strategy_name
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizeStrategyName:

    @pytest.fixture
    def fn(self):
        from web.data_provider import normalize_strategy_name
        return normalize_strategy_name

    def test_alias_favourite_compound(self, fn):
        assert fn("favourite_compound") == "favourite_compounding"

    def test_already_correct(self, fn):
        assert fn("favourite_compounding") == "favourite_compounding"

    def test_uppercase_lowercased(self, fn):
        assert fn("Whale") == "whale"

    def test_whitespace_stripped(self, fn):
        assert fn("  whale  ") == "whale"

    def test_empty_string(self, fn):
        assert fn("  ") == ""

    def test_none_returns_empty(self, fn):
        assert fn(None) == ""

    def test_uppercase_alias(self, fn):
        """FAVOURITE_COMPOUND после lower() тоже должен стать favourite_compounding."""
        assert fn("FAVOURITE_COMPOUND") == "favourite_compounding"

    def test_unknown_strategy_passthrough(self, fn):
        assert fn("synthetic_corridor") == "synthetic_corridor"


# ─────────────────────────────────────────────────────────────────────────────
# 2. signals_count — max() не перезаписывает большее значение
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalsCountMax:

    def _make_row(self, **kwargs):
        r = MagicMock()
        r.__getitem__ = lambda self, k: kwargs[k]
        return r

    def test_load_signals_pnl_keeps_larger_value(self):
        """signals_count=50 не должен быть перезаписан total=10."""
        from web.data_provider import _load_signals_pnl

        stats = {"whale": {"signals_count": 50, "pnl_7d": 0.0, "pnl_30d": 0.0}}
        row = self._make_row(strategy_type="whale", pnl_7d=1.0, pnl_30d=2.0, total=10)
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [row]

        _load_signals_pnl(conn, stats)
        assert stats["whale"]["signals_count"] == 50

    def test_load_signals_pnl_updates_when_pnl_source_larger(self):
        """signals_count=5 должен обновиться до total=99."""
        from web.data_provider import _load_signals_pnl

        stats = {"whale": {"signals_count": 5, "pnl_7d": 0.0, "pnl_30d": 0.0}}
        row = self._make_row(strategy_type="whale", pnl_7d=1.0, pnl_30d=2.0, total=99)
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [row]

        _load_signals_pnl(conn, stats)
        assert stats["whale"]["signals_count"] == 99

    def test_load_penny_stocks_empty_rows_keeps_existing(self):
        """Если данных нет — signals_count не обнуляется."""
        from web.data_provider import _load_penny_stocks_stats

        stats = {
            "penny_stocks": {
                "signals_count": 30,
                "pnl_7d": 0.0,
                "pnl_30d": 0.0,
                "win_rate": None,
            }
        }
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []

        _load_penny_stocks_stats(conn, stats, virtual_stake=10.0)
        assert stats["penny_stocks"]["signals_count"] == 30

    def test_load_signals_pnl_none_total_treated_as_zero(self):
        """total=None не должен вызвать TypeError через 'or 0'."""
        from web.data_provider import _load_signals_pnl

        stats = {"whale": {"signals_count": 0, "pnl_7d": 0.0, "pnl_30d": 0.0}}
        row = self._make_row(strategy_type="whale", pnl_7d=None, pnl_30d=None, total=None)
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [row]

        _load_signals_pnl(conn, stats)  # Не должен упасть
        assert stats["whale"]["signals_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. get_compound_settings — робастная загрузка
# ─────────────────────────────────────────────────────────────────────────────

class TestGetCompoundSettings:

    def _call(self, db_rows):
        import agents.shared.python.db as db_mod

        conn_mock = MagicMock()
        conn_mock.execute.return_value.fetchall.return_value = db_rows

        @contextlib.contextmanager
        def fake_conn():
            yield conn_mock

        with patch.object(db_mod, "get_connection", fake_conn):
            return db_mod.get_compound_settings()

    def _row(self, key, value):
        r = MagicMock()
        r.__getitem__ = lambda self, k: {"key": key, "value": value}[k]
        return r

    def test_normal_float(self):
        result = self._call([self._row("min_price", "0.95")])
        assert isinstance(result["min_price"], float)
        assert result["min_price"] == pytest.approx(0.95)

    def test_enabled_is_int(self):
        result = self._call([self._row("enabled", "1")])
        assert isinstance(result["enabled"], int)
        assert result["enabled"] == 1

    def test_empty_string_falls_back_to_default(self):
        import agents.shared.python.db as db_mod
        expected = float(db_mod.COMPOUND_DEFAULTS["min_price"])
        result = self._call([self._row("min_price", "")])
        assert result["min_price"] == pytest.approx(expected)

    def test_none_value_falls_back_to_default(self):
        import agents.shared.python.db as db_mod
        expected = float(db_mod.COMPOUND_DEFAULTS["min_confidence"])
        result = self._call([self._row("min_confidence", None)])
        assert result["min_confidence"] == pytest.approx(expected)

    def test_invalid_string_falls_back_to_default(self):
        import agents.shared.python.db as db_mod
        expected = float(db_mod.COMPOUND_DEFAULTS["min_price"])
        result = self._call([self._row("min_price", "not_a_number")])
        assert result["min_price"] == pytest.approx(expected)

    def test_unknown_key_with_none_default_does_not_raise(self):
        """Ключ которого нет в COMPOUND_DEFAULTS — не должен вызвать float(None)."""
        result = self._call([self._row("unknown_legacy_key", "garbage")])
        # Просто не должен упасть
        assert isinstance(result, dict)

    def test_all_defaults_present(self):
        import agents.shared.python.db as db_mod
        result = self._call([])
        for key in db_mod.COMPOUND_DEFAULTS:
            assert key in result, f"Отсутствует ключ: {key}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. _normalize_close_time
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizeCloseTime:

    @pytest.fixture
    def fn(self):
        from services.onchain_trend_alert import _normalize_close_time
        return _normalize_close_time

    def test_none_returns_none(self, fn):
        assert fn(None) is None

    def test_empty_string_returns_none(self, fn):
        assert fn("") is None

    def test_whitespace_returns_none(self, fn):
        assert fn("   ") is None

    def test_z_suffix_parsed(self, fn):
        assert fn("2025-12-31T23:59:00Z") == "2025-12-31 23:59:00"

    def test_offset_iso_parsed(self, fn):
        assert fn("2025-06-15T10:00:00+00:00") == "2025-06-15 10:00:00"

    def test_plain_iso_parsed(self, fn):
        assert fn("2025-01-01T00:00:00") == "2025-01-01 00:00:00"

    def test_garbage_returns_none(self, fn):
        assert fn("not-a-date") is None

    def test_datetime_object_handled(self, fn):
        """datetime-объект должен корректно форматироваться, не падать на .replace()."""
        dt = datetime(2026, 3, 20, 15, 30, 45)
        result = fn(dt)
        assert result == "2026-03-20 15:30:45"

    def test_output_format_is_correct(self, fn):
        result = fn("2026-03-20T15:30:45Z")
        # Должен парситься обратно без ошибок
        parsed = datetime.strptime(result, "%Y-%m-%d %H:%M:%S")
        assert parsed.year == 2026


# ─────────────────────────────────────────────────────────────────────────────
# 5. _should_ignore_message — возвращает tuple, chat инициализирован
# ─────────────────────────────────────────────────────────────────────────────

class TestShouldIgnoreMessage:

    def _make_event(self, text="signal", chat_id="9999", bot_id=None):
        event = MagicMock()
        chat = MagicMock()
        chat.id = int(chat_id)
        event.get_chat = AsyncMock(return_value=chat)
        event.get_sender = AsyncMock(return_value=MagicMock(id=bot_id))
        event.message.message = text
        event.message.entities = None
        return event, chat

    @pytest.mark.asyncio
    async def test_empty_text_returns_ignore_tuple(self):
        from services.telegram_listener import _should_ignore_message
        event, _ = self._make_event(text="")
        result = await _should_ignore_message(event, target_chat_id=None)
        assert isinstance(result, tuple) and len(result) == 2
        assert result == (True, None)

    @pytest.mark.asyncio
    async def test_valid_message_returns_false_with_chat(self):
        from services.telegram_listener import _should_ignore_message
        event, chat = self._make_event(text="Real signal text here")
        with patch("services.telegram_listener.is_bot_message", return_value=False), \
             patch("services.telegram_listener.TELEGRAM_BOT_ID", None):
            result = await _should_ignore_message(event, target_chat_id=None)
        assert result[0] is False
        assert result[1] is chat  # chat передаётся наружу, чтобы не вызывать get_chat() повторно

    @pytest.mark.asyncio
    async def test_bot_message_ignored(self):
        from services.telegram_listener import _should_ignore_message
        event, _ = self._make_event(text="[BOT] some message")
        with patch("services.telegram_listener.is_bot_message", return_value=True), \
             patch("services.telegram_listener.TELEGRAM_BOT_ID", None):
            result = await _should_ignore_message(event, target_chat_id=None)
        assert result == (True, None)

    @pytest.mark.asyncio
    async def test_chat_id_match_ignored(self):
        from services.telegram_listener import _should_ignore_message
        event, _ = self._make_event(text="signal", chat_id="1234567890")
        result = await _should_ignore_message(event, target_chat_id="-1001234567890")
        assert result[0] is True

    @pytest.mark.asyncio
    async def test_get_chat_exception_returns_false_none(self):
        """При исключении в get_chat — (False, None), не NameError."""
        from services.telegram_listener import _should_ignore_message
        event = MagicMock()
        event.get_chat = AsyncMock(side_effect=Exception("network error"))
        event.message.message = "some text"
        result = await _should_ignore_message(event, target_chat_id=None)
        assert result == (False, None)

    @pytest.mark.asyncio
    async def test_chat_initialized_before_try(self):
        """
        Регрессия: chat должен быть инициализирован до try-блока (= None),
        чтобы 'return False, chat' не бросал NameError при будущих рефакторингах.
        Проверяем это косвенно: исключение в get_chat не приводит к NameError.
        """
        from services.telegram_listener import _should_ignore_message
        event = MagicMock()
        event.get_chat = AsyncMock(side_effect=RuntimeError("timeout"))
        event.message.message = "text"
        try:
            result = await _should_ignore_message(event, target_chat_id=None)
            assert result[1] is None
        except NameError:
            pytest.fail("chat не инициализирован до try-блока — NameError")


# ─────────────────────────────────────────────────────────────────────────────
# 6. add_penny_stock_to_monitoring — close_time передаётся параметром
# ─────────────────────────────────────────────────────────────────────────────

class TestAddPennyStockCloseTime:

    def _call(self, close_time=None):
        import agents.shared.python.db as db_mod

        executed_sqls = []
        executed_params_list = []

        conn_mock = MagicMock()

        def capture(sql, *args):
            executed_sqls.append(sql)
            executed_params_list.append(list(args[0]) if args else [])
            return MagicMock()

        conn_mock.execute = capture

        @contextlib.contextmanager
        def fake_conn():
            yield conn_mock

        with patch.object(db_mod, "get_connection", fake_conn):
            db_mod.add_penny_stock_to_monitoring(
                market_id="test-market-id",
                title="Test Market",
                url="https://polymarket.com/test",
                initial_price=0.55,
                close_time=close_time,
            )
        return executed_sqls, executed_params_list

    def test_explicit_close_time_in_insert(self):
        sqls, params = self._call(close_time="2026-12-31 23:59:00")
        assert "2026-12-31 23:59:00" in params[0]

    def test_none_close_time_generates_fallback(self):
        _, params = self._call(close_time=None)
        close_val = params[0][-1]
        # Должна быть строка формата YYYY-MM-DD HH:MM:SS
        parsed = datetime.strptime(close_val, "%Y-%m-%d %H:%M:%S")
        # И быть в будущем (примерно +30 дней)
        assert parsed > datetime.now()

    def test_no_sqlite_datetime_function_in_sql(self):
        """datetime('now', '+30 days') в SQL заменён на Python-значение."""
        sqls, _ = self._call(close_time="2026-06-15 10:00:00")
        assert not any("datetime('now'" in s for s in sqls)


# ─────────────────────────────────────────────────────────────────────────────
# 7. get_equity_curve — normalize_strategy_name применяется к stype
# ─────────────────────────────────────────────────────────────────────────────

class TestEquityCurveStrategyNormalization:

    def _patch_db(self):
        import agents.shared.python.db as db_mod
        conn_mock = MagicMock()
        conn_mock.execute.return_value.fetchall.return_value = []
        conn_mock.execute.return_value.__iter__ = lambda s: iter([])

        @contextlib.contextmanager
        def fake_conn():
            yield conn_mock

        return patch.object(db_mod, "get_connection", fake_conn)

    def test_alias_does_not_raise(self):
        from web.data_provider import get_equity_curve
        with self._patch_db():
            result = get_equity_curve("favourite_compound", days=7)
        assert result is not None

    def test_alias_same_result_as_canonical(self):
        from web.data_provider import get_equity_curve
        with self._patch_db():
            r1 = get_equity_curve("favourite_compound", days=7)
            r2 = get_equity_curve("favourite_compounding", days=7)
        assert r1 == r2

    def test_unknown_strategy_returns_empty(self):
        from web.data_provider import get_equity_curve
        with self._patch_db():
            result = get_equity_curve("nonexistent_xyz", days=7)
        assert result == [] or result == {}
