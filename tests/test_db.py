"""
Тесты для agents/shared/python/db.py — Слой #10

Покрывают 4 исправленных бага:
  Баг #1 — авто-инициализация БД через get_connection()
  Баг #2 — атомарная очистка chat_history
  Баг #3 — защита от бесконечного цикла в get_performance_summary
  Баг #4 — try/except в save_idea_audit
"""
import json
import sqlite3
import sys
import types
import importlib
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ─────────────────────────────────────────────
# Фикстура: подключаем db.py с временной БД
# ─────────────────────────────────────────────

@pytest.fixture()
def db_module(tmp_path):
    """
    Загружает db.py в изолированном окружении с временной БД.
    Сбрасывает _db_initialized и _db_initializing перед каждым тестом.
    """
    db_file = tmp_path / "test.db"

    # Подготовим заглушки для импортов, которые нужны db.py
    original_config = sys.modules.get("config")
    original_core = sys.modules.get("core")
    original_core_models = sys.modules.get("core.models")

    fake_config = types.ModuleType("config")
    fake_config.DB_PATH = db_file
    sys.modules["config"] = fake_config

    # core.models — минимальные заглушки
    core_pkg = types.ModuleType("core")
    core_models = types.ModuleType("core.models")
    for cls_name in ("Market", "Signal", "MarketCorrelation"):
        setattr(core_models, cls_name, object)
    core_pkg.models = core_models
    sys.modules["core"] = core_pkg
    sys.modules["core.models"] = core_models

    # Перезагружаем db каждый раз, чтобы сбросить глобальное состояние
    db_mod_path = str(
        Path(__file__).parent.parent / "agents" / "shared" / "python" / "db.py"
    )
    spec = importlib.util.spec_from_file_location("db_fresh", db_mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Гарантируем чистый старт
    mod._db_initialized = False
    mod._db_initializing = False

    yield mod

    # Очистка sys.modules от нашего модуля
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


# ─────────────────────────────────────────────
# Баг #1 — Авто-инициализация
# ─────────────────────────────────────────────

class TestAutoInit:
    """get_connection() должен сам вызывать init_db() — таблицы создаются автоматически."""

    def test_save_chat_message_without_explicit_init_db(self, db_module):
        """save_chat_message работает без явного вызова init_db()."""
        # НЕ вызываем init_db() вручную
        db_module.save_chat_message(chat_id=1, role="user", content="привет")
        history = db_module.get_chat_history(chat_id=1)
        assert len(history) == 1
        assert history[0]["parts"][0]["text"] == "привет"

    def test_save_memory_without_explicit_init(self, db_module):
        """save_memory/get_memory работают без явного init_db()."""
        db_module.save_memory("test_key", {"val": 42})
        result = db_module.get_memory("test_key")
        assert result == {"val": 42}

    def test_get_memory_returns_default_on_miss(self, db_module):
        """get_memory возвращает default, если ключа нет."""
        result = db_module.get_memory("nonexistent_key", default="fallback")
        assert result == "fallback"

    def test_no_infinite_recursion_on_first_call(self, db_module):
        """get_connection() не уходит в бесконечную рекурсию."""
        # Просто убеждаемся, что вызов проходит без RecursionError
        with db_module.get_connection() as conn:
            assert conn is not None

    def test_db_initialized_flag_set_after_init(self, db_module):
        """_db_initialized устанавливается в True после первого обращения."""
        assert not db_module._db_initialized
        db_module.save_memory("k", "v")
        assert db_module._db_initialized

    def test_init_db_idempotent(self, db_module):
        """Повторный вызов init_db() не вызывает ошибок."""
        db_module.init_db()
        db_module.init_db()  # второй вызов должен быть no-op
        assert db_module._db_initialized


# ─────────────────────────────────────────────
# Баг #2 — Атомарная очистка chat_history
# ─────────────────────────────────────────────

class TestCompressCleanup:
    """compress_and_cleanup_chat_history должна работать атомарно."""

    def _populate_chat(self, db_module, chat_id: int, count: int):
        for i in range(count):
            role = "user" if i % 2 == 0 else "model"
            db_module.save_chat_message(chat_id, role, f"msg_{i}")

    def test_compress_cleanup_keeps_last_n(self, db_module):
        """После очистки остаётся keep_last сообщений (или keep_last-1 если первое не от user)."""
        self._populate_chat(db_module, chat_id=10, count=25)
        db_module.compress_and_cleanup_chat_history(chat_id=10, keep_last=10, summarize_threshold=40)
        history = db_module.get_chat_history(chat_id=10, limit=100)
        # get_chat_history обрезает первое сообщение если оно не от 'user',
        # поэтому допустимо 9 или 10
        assert len(history) in (9, 10)

    def test_compress_cleanup_below_threshold_no_archive(self, db_module):
        """Если count <= threshold — архивирование не происходит, но лишние удаляются."""
        self._populate_chat(db_module, chat_id=20, count=15)
        db_module.compress_and_cleanup_chat_history(chat_id=20, keep_last=5, summarize_threshold=40)
        history = db_module.get_chat_history(chat_id=20, limit=100)
        assert len(history) == 5
        # Архивная запись в memory не должна появиться
        from datetime import datetime, timezone
        key = f"chat_archive_20_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        assert db_module.get_memory(key) is None

    def test_compress_cleanup_above_threshold_saves_archive(self, db_module):
        """Если count > threshold — старые сообщения архивируются в memory."""
        self._populate_chat(db_module, chat_id=30, count=50)
        db_module.compress_and_cleanup_chat_history(chat_id=30, keep_last=10, summarize_threshold=40)
        # Проверяем что запись в memory создана
        from datetime import datetime, timezone
        key = f"chat_archive_30_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        archived = db_module.get_memory(key)
        assert archived is not None
        assert "Архив диалога" in archived

    def test_compress_cleanup_thread_safe(self, db_module):
        """Конкурентные вызовы не вызывают исключений."""
        self._populate_chat(db_module, chat_id=40, count=60)
        errors = []

        def cleanup():
            try:
                db_module.compress_and_cleanup_chat_history(
                    chat_id=40, keep_last=10, summarize_threshold=40
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=cleanup) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Ошибки при конкурентном вызове: {errors}"


# ─────────────────────────────────────────────
# Баг #3 — Защита от бесконечного цикла в get_performance_summary
# ─────────────────────────────────────────────

class TestPerformanceSummary:
    """get_performance_summary не должна зависать на вложенном/некорректном JSON."""

    def _insert_episode(self, db_module, agent: str, context_raw: str, outcome: str = "correct"):
        """Напрямую вставляем эпизод с произвольным context в БД."""
        with db_module.get_connection() as conn:
            conn.execute(
                """INSERT INTO agent_episodes
                   (agent_name, event_type, market_id, summary, context, outcome)
                   VALUES (?, 'signal_resolved', 'mkt_test', 'Test summary', ?, ?)""",
                (agent, context_raw, outcome),
            )

    def test_performance_summary_with_plain_dict_json(self, db_module):
        """Обычный JSON-контекст с predicted_prob отображается корректно."""
        ctx = json.dumps({"predicted_prob": 0.75})
        self._insert_episode(db_module, "agent_a", ctx)
        result = db_module.get_performance_summary("agent_a")
        assert "75%" in result

    def test_performance_summary_with_double_encoded_json(self, db_module):
        """Двойной JSON-encode не вызывает бесконечный цикл."""
        inner = json.dumps({"predicted_prob": 0.60})
        double_encoded = json.dumps(inner)  # строка внутри строки
        self._insert_episode(db_module, "agent_b", double_encoded)
        result = db_module.get_performance_summary("agent_b")
        assert "60%" in result

    def test_performance_summary_with_invalid_json_context(self, db_module):
        """Невалидный JSON не вызывает исключение — prob отображается как '?'."""
        self._insert_episode(db_module, "agent_c", "NOT_JSON_AT_ALL")
        result = db_module.get_performance_summary("agent_c")
        assert "[прогноз был: ?]" in result

    def test_performance_summary_with_none_context(self, db_module):
        """None-контекст (в БД NULL) не вызывает исключение."""
        with db_module.get_connection() as conn:
            conn.execute(
                """INSERT INTO agent_episodes
                   (agent_name, event_type, market_id, summary, context, outcome)
                   VALUES ('agent_d', 'signal_resolved', 'mkt_d', 'Summary D', NULL, 'incorrect')"""
            )
        result = db_module.get_performance_summary("agent_d")
        assert "[прогноз был: ?]" in result

    def test_performance_summary_empty_when_no_episodes(self, db_module):
        """Если эпизодов нет — возвращается пустая строка."""
        result = db_module.get_performance_summary("agent_nobody")
        assert result == ""

    def test_performance_summary_no_infinite_loop_on_triple_encoded(self, db_module):
        """Тройной encode — цикл ограничен 3 итерациями и завершается."""
        inner = json.dumps({"predicted_prob": 0.55})
        triple = json.dumps(json.dumps(inner))
        self._insert_episode(db_module, "agent_e", triple)
        # Не должно зависнуть
        result = db_module.get_performance_summary("agent_e")
        assert isinstance(result, str)


# ─────────────────────────────────────────────
# Баг #4 — try/except в save_idea_audit
# ─────────────────────────────────────────────

class TestSaveIdeaAudit:
    """save_idea_audit не должна ронять пайплайн при ошибках БД."""

    def test_save_idea_audit_normal(self, db_module):
        """Корректный вызов сохраняет запись."""
        db_module.save_idea_audit(
            market_id="mkt_001",
            market_title="Test Market",
            audit_data={
                "scout_edge": 0.15,
                "swing_found": 1,
                "shadow_agree": 1,
                "shadow_confidence": 0.82,
                "shadow_reason": "Strong signal",
                "final_outcome": "passed",
            },
        )
        with db_module.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM idea_audit WHERE market_id = 'mkt_001'"
            ).fetchone()
        assert row is not None
        assert row["market_title"] == "Test Market"
        assert abs(row["scout_edge"] - 0.15) < 1e-9

    def test_save_idea_audit_does_not_raise_on_db_error(self, db_module, caplog):
        """Даже при ошибках записи в базу функция не должна падать."""
        import sqlite3
        import logging
        log_parent = logging.getLogger("NexusPolyBot")
        orig_propagate = log_parent.propagate
        log_parent.propagate = True
        try:
            with caplog.at_level(logging.ERROR, logger="NexusPolyBot.DB"):
                with patch.object(db_module, "get_connection") as mock_conn:
                    mock_conn.side_effect = sqlite3.OperationalError("disk full")
                    # Вызываем функцию
                    db_module.save_idea_audit(
                        market_id="mkt_bad",
                        market_title="Bad Market",
                        audit_data={},
                    )
                assert any("idea_audit" in r.message for r in caplog.records)
        finally:
            log_parent.propagate = orig_propagate

    def test_save_idea_audit_partial_data(self, db_module):
        """Вызов с пустым audit_data не вызывает исключение."""
        db_module.save_idea_audit(
            market_id="mkt_empty",
            market_title="Empty Audit",
            audit_data={},
        )
        with db_module.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM idea_audit WHERE market_id = 'mkt_empty'"
            ).fetchone()
        assert row is not None
        assert row["final_outcome"] == "unknown"


# ─────────────────────────────────────────────
# Тест бага парсинга даты из SQLite в is_alert_already_sent
# ─────────────────────────────────────────────

class TestAlertSentDatetimeBug:
    def test_is_alert_sent_sqlite_format(self, db_module):
        """is_alert_already_sent корректно парсит даты с пробелом (формат SQLite CURRENT_TIMESTAMP) без ValueError."""
        alert_key = "test_alert_key_format"
        # Напрямую вставляем в sent_alerts дату с пробелом
        with db_module.get_connection() as conn:
            conn.execute(
                """INSERT INTO sent_alerts (alert_key, alert_type, sent_at)
                   VALUES (?, 'test_type', '2020-06-07 14:51:22')""",
                (alert_key,)
            )
        # Этот вызов не должен бросать ValueError
        res = db_module.is_alert_already_sent(alert_key, ttl_hours=12)
        assert res is False


# ─────────────────────────────────────────────
# Тест whale_stocks_monitoring с wallet_address
# ─────────────────────────────────────────────

class TestWhaleStocksMonitoringWalletAddress:
    def test_whale_stock_monitoring_saves_wallet_address(self, db_module):
        """Проверяем, что add_whale_stock_to_monitoring сохраняет и get_active_whale_stocks возвращает wallet_address."""
        market_id = "test_whale_market_1"
        title = "Test Whale Market"
        url = "http://test_whale"
        initial_price = 0.5
        wallet_address = "0x1234567890123456789012345678901234567890"

        db_module.add_whale_stock_to_monitoring(
            market_id=market_id,
            title=title,
            url=url,
            initial_price=initial_price,
            wallet_address=wallet_address
        )

        active_stocks = db_module.get_active_whale_stocks()
        found = [s for s in active_stocks if s['market_id'] == market_id]
        assert len(found) == 1
        assert found[0]['wallet_address'] == wallet_address

def test_get_agent_episodes_filter_by_event_type(db_module):
    """Проверяем, что get_agent_episodes корректно фильтрует записи по event_type."""
    # Сохраняем тестовые эпизоды
    db_module.save_agent_episode(
        agent_name="SCOUT",
        event_type="signal_resolved",
        market_id="m1",
        market_title="Market 1",
        summary="Summary 1",
        outcome="correct"
    )
    db_module.save_agent_episode(
        agent_name="SCOUT",
        event_type="opinion_formed",
        market_id="m2",
        market_title="Market 2",
        summary="Summary 2",
        outcome="none"
    )
    
    # Запрашиваем с фильтром по event_type='signal_resolved'
    resolved_episodes = db_module.get_agent_episodes(
        agent_name="SCOUT",
        event_type="signal_resolved",
        limit=5
    )
    assert len(resolved_episodes) == 1
    assert resolved_episodes[0]['market_id'] == "m1"
    assert resolved_episodes[0]['event_type'] == "signal_resolved"

    # Запрашиваем без фильтра по event_type
    all_episodes = db_module.get_agent_episodes(
        agent_name="SCOUT",
        limit=5
    )
    assert len(all_episodes) >= 2

def test_update_episodes_saves_accuracy_to_memory(db_module):
    """Проверяем, что update_episodes_for_market обновляет эпизоды и записывает точность в memory."""
    import json
    ctx_str = json.dumps({"target_outcome": "YES"})
    
    with db_module.get_connection() as conn:
        conn.execute('''
            INSERT INTO agent_episodes (agent_name, event_type, market_id, market_title, summary, context, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', ("SCOUT", "signal_resolved", "market_accurate_1", "Title 1", "Summary 1", ctx_str, "unknown"))
    
    # Запускаем обновление
    db_module.update_episodes_for_market("market_accurate_1", "YES")
    
    # Проверяем, что эпизод обновлен в базе
    eps = db_module.get_agent_episodes(agent_name="SCOUT", limit=5)
    found = [e for e in eps if e['market_id'] == "market_accurate_1"]
    assert len(found) == 1
    assert found[0]['outcome'] == "correct"
    
    # Проверяем, что в memory записались точность и общее количество
    acc = db_module.get_memory("scout_accuracy_pct")
    total = db_module.get_memory("scout_evaluated_total")
    assert total == 1
    assert acc == 100.0

def test_update_episodes_with_empty_agent_name(db_module):
    """Проверяем, что пустой agent_name не вызывает ошибку."""
    with db_module.get_connection() as conn:
        conn.execute('''
            INSERT INTO agent_episodes 
            (agent_name, event_type, market_id, market_title, summary, context, outcome)
            VALUES ('', 'signal_resolved', 'market_empty_agent', 'Title', 'Summary', '{}', 'unknown')
        ''')
    # Не должно бросать исключение
    db_module.update_episodes_for_market("market_empty_agent", "YES")
    # memory не должна получить ключи для пустого агента
    val = db_module.get_memory("_accuracy_pct")
    assert val is None
