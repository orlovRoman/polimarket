import sys
import types
import importlib
from pathlib import Path
import pytest

@pytest.fixture()
def db_module(tmp_path):
    """
    Загружает db.py в изолированном окружении с временной БД.
    """
    db_file = tmp_path / "test.db"

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


class TestAgentEpisodesAndAccuracy:
    def test_get_agent_accuracy_filtering_unresolved(self, db_module):
        """Проверяет, что get_agent_accuracy исключает unresolved исходы и правильно считает точность."""
        db_module.save_agent_episode(
            agent_name="SWING",
            event_type="signal_evaluated",
            summary="Rec=BUY 1",
            market_id="m1",
            outcome="correct"
        )
        db_module.save_agent_episode(
            agent_name="SWING",
            event_type="signal_evaluated",
            summary="Rec=BUY 2",
            market_id="m2",
            outcome="incorrect"
        )
        db_module.save_agent_episode(
            agent_name="SWING",
            event_type="signal_evaluated",
            summary="Rec=IGNORE 3",
            market_id="m3",
            outcome="unresolved"
        )

        stats = db_module.get_agent_accuracy("SWING")
        assert stats["total"] == 2
        assert stats["correct"] == 1
        assert stats["incorrect"] == 1
        assert stats["accuracy"] == 0.5

    def test_get_agent_accuracy_context_dynamic(self, db_module):
        """Проверяет, что get_agent_accuracy_context возвращает правильную динамическую строку."""
        db_module.save_agent_episode("SWING", "signal_evaluated", "Rec=BUY 1", "m1", outcome="correct")
        db_module.save_agent_episode("SWING", "signal_evaluated", "Rec=BUY 2", "m2", outcome="incorrect")

        # При min_samples=3 должен вернуть None (так как total=2)
        ctx_none = db_module.get_agent_accuracy_context("swing", min_samples=3)
        assert ctx_none is None

        # При min_samples=2 должен вернуть статистику
        ctx_ok = db_module.get_agent_accuracy_context("swing", min_samples=2)
        assert ctx_ok is not None
        assert "1/2 правильных прогнозов" in ctx_ok
        assert "50% точность" in ctx_ok
        assert "Ошибок: 1" in ctx_ok

    def test_get_performance_summary_supports_signal_evaluated(self, db_module):
        """Проверяет, что get_performance_summary ищет и возвращает эпизоды с event_type='signal_evaluated'."""
        db_module.save_agent_episode(
            agent_name="SWING",
            event_type="signal_evaluated",
            summary="Эпизод оценки",
            market_id="m1",
            outcome="correct",
            context={"predicted_prob": 0.75}
        )

        summary = db_module.get_performance_summary("SWING", limit=5)
        assert summary != ""
        assert "Твоя история прогнозов" in summary
        assert "Эпизод оценки" in summary
        assert "прогноз был: 75%" in summary

    def test_compress_and_cleanup_chat_history_transaction(self, db_module):
        """Проверяет, что очистка истории чата выполняется успешно (без ошибок транзакций)."""
        chat_id = 123
        for i in range(45):
            db_module.save_chat_message(chat_id=chat_id, role="user", content=f"msg {i}")

        # Вызываем сжатие без суммаризации
        db_module.compress_and_cleanup_chat_history(chat_id=chat_id, keep_last=20, summarize_threshold=999)

        history = db_module.get_chat_history(chat_id=chat_id)
        assert len(history) == 20
        assert history[0]["parts"][0]["text"] == "msg 25"
