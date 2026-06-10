import asyncio
from unittest.mock import AsyncMock
import time
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

def _make_market(market_id="mkt-1"):
    from core.models import Market
    return Market(
        id=market_id, platform="polymarket", title="Test Market",
        url="https://poly.com/t", outcome="YES", price=0.5,
        close_time=datetime(2026, 12, 1, tzinfo=timezone.utc),
    )

# ── Баг #1: Python 3.9 совместимость типов ───────────────────

def test_workflow_importable_without_syntax_error():
    """core/workflow.py импортируется без TypeError (Python 3.9 compat)"""
    try:
        import core.workflow as wf
    except TypeError as e:
        pytest.fail(f"workflow.py упал с TypeError (set[str] вместо Set[str]): {e}")
    except Exception:
        pass  # другие ошибки импорта допустимы (нет реального окружения)


def test_analyzed_in_session_is_dict():
    """_analyzed_in_session должен быть dict, не set"""
    from core.workflow import _analyzed_in_session
    assert isinstance(_analyzed_in_session, dict), \
        f"Ожидали dict, получили {type(_analyzed_in_session)}"


# ── Баг #2: TTL-очистка _analyzed_in_session ─────────────────

def test_cleanup_removes_expired_keys():
    """Устаревшие ключи удаляются _cleanup_session_dedup"""
    from core import workflow

    workflow._analyzed_in_session.clear()
    # Добавляем "старый" ключ (за пределами TTL)
    workflow._analyzed_in_session["old-key:scheduled"] = \
        time.monotonic() - workflow._SESSION_DEDUP_TTL_SEC - 1

    # Добавляем свежий ключ
    workflow._analyzed_in_session["new-key:scheduled"] = time.monotonic()

    workflow._cleanup_session_dedup()

    assert "old-key:scheduled" not in workflow._analyzed_in_session, \
        "Устаревший ключ должен быть удалён"
    assert "new-key:scheduled" in workflow._analyzed_in_session, \
        "Свежий ключ должен остаться"


def test_cleanup_removes_nothing_if_all_fresh():
    """Если все ключи свежие — ничего не удаляется"""
    from core import workflow

    workflow._analyzed_in_session.clear()
    workflow._analyzed_in_session["key1:event_driven"] = time.monotonic()
    workflow._analyzed_in_session["key2:scheduled"]    = time.monotonic()

    workflow._cleanup_session_dedup()

    assert len(workflow._analyzed_in_session) == 2


def test_session_dedup_allows_after_ttl():
    """Рынок, добавленный > TTL назад, разрешается в следующем вызове"""
    from core import workflow

    workflow._analyzed_in_session.clear()
    # Симулируем: ключ добавлен давно
    workflow._analyzed_in_session["mkt-ttl:scheduled"] = \
        time.monotonic() - workflow._SESSION_DEDUP_TTL_SEC - 5

    workflow._cleanup_session_dedup()

    assert "mkt-ttl:scheduled" not in workflow._analyzed_in_session, \
        "Ключ с истёкшим TTL должен быть очищен и рынок разрешён"


def test_session_dedup_blocks_within_ttl():
    """Рынок, добавленный < TTL назад, блокируется"""
    from core import workflow
    from core.models import Market

    workflow._analyzed_in_session.clear()
    dedup_key = "mkt-block:scheduled"
    workflow._analyzed_in_session[dedup_key] = time.monotonic()  # только что добавлен

    m = MagicMock()
    m.id = "mkt-block"

    with patch("core.workflow.get_memory", return_value=None):
        result = asyncio.run(workflow.run_agent_evaluation(
            m=m, scout=MagicMock(), swing=MagicMock(),
            update_state=MagicMock(), trigger_type="scheduled"
        ))

    assert result == (None, None, None), "In-session дедупликация должна заблокировать"


def test_session_dedup_per_trigger_type_is_same_market():
    """scheduled и event_driven одного рынка — оба блокируются (cooldown по market_id)"""
    from core import workflow

    workflow._analyzed_in_session.clear()
    # Добавляем scheduled
    workflow._analyzed_in_session["mkt-multi:scheduled"] = time.monotonic()

    m = MagicMock()
    m.id = "mkt-multi"

    # event_driven того же рынка — в in-session нет, но проверяем БД:
    recent = datetime.now(timezone.utc).isoformat()
    with patch("core.workflow.get_memory", return_value=recent):
        result = asyncio.run(workflow.run_agent_evaluation(
            m=m, scout=MagicMock(), swing=MagicMock(),
            update_state=MagicMock(), trigger_type="event_driven"
        ))
    assert result == (None, None, None), "БД cooldown должен заблокировать event_driven после scheduled"


# ── Баг #3: документирование save_memory до анализа ──────────

class MockExecutor:
    def __init__(self, *args, **kwargs):
        pass
    def submit(self, fn, *args, **kwargs):
        from concurrent.futures import Future
        f = Future()
        try:
            res = fn(*args, **kwargs)
            f.set_result(res)
        except Exception as e:
            f.set_exception(e)
        return f
    def shutdown(self, *args, **kwargs):
        pass

def test_save_memory_called_before_llm():
    """save_memory вызывается ДО LLM (до scout.estimate_market)"""
    from core import workflow

    workflow._analyzed_in_session.clear()

    call_order = []

    def mock_save_memory(key, *args, **kwargs):
        if key.startswith("last_analysis:"):
            call_order.append("save_memory")

    m = _make_market("mkt-order")
    m.title = "Test"

    scout = MagicMock()
    def mock_scout_estimate(*a, **kw):
        call_order.append("scout_llm")
        return None
    scout.estimate_market = AsyncMock(side_effect=mock_scout_estimate)

    swing = MagicMock()
    swing.estimate_market = AsyncMock(side_effect=lambda *a, **kw: None)

    with patch("core.workflow.get_memory", return_value=None), \
         patch("core.workflow.save_memory", side_effect=mock_save_memory), \
         patch("config.llm_health_gate") as gate, \
         patch("core.workflow.build_search_query", return_value="q", create=True), \
         patch("core.workflow.concurrent.futures.ThreadPoolExecutor", new=MockExecutor), \
         patch("core.workflow._safe_result", return_value=[], create=True), \
         patch("core.workflow.fetch_google_trends", return_value="", create=True), \
         patch("core.workflow.get_market_correlations", return_value=[], create=True), \
         patch("core.workflow.save_checkpoint", create=True), \
         patch("core.workflow.fetch_wikipedia_context", return_value="", create=True):

        gate.check_availability.return_value = True
        asyncio.run(workflow.run_agent_evaluation(
            m=m, scout=scout, swing=swing,
            update_state=MagicMock(), trigger_type="scheduled"
        ))

    save_idx  = call_order.index("save_memory") if "save_memory" in call_order else -1
    scout_idx = call_order.index("scout_llm")   if "scout_llm"   in call_order else -1

    assert save_idx != -1,  "save_memory должен быть вызван"
    assert scout_idx != -1, "scout должен быть вызван"
    assert save_idx < scout_idx, \
        f"save_memory (idx={save_idx}) должен быть ДО scout_llm (idx={scout_idx})"


# ── Регрессия: старые тесты дедупликации ─────────────────────

def test_dedup_db_cooldown_still_works():
    """БД cooldown 10 мин — прошло 5 мин → блок"""
    from core import workflow

    workflow._analyzed_in_session.clear()

    five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    m = MagicMock()
    m.id = "mkt-5min"

    with patch("core.workflow.get_memory", return_value=five_min_ago):
        result = asyncio.run(workflow.run_agent_evaluation(
            m=m, scout=MagicMock(), swing=MagicMock(),
            update_state=MagicMock()
        ))

    assert result == (None, None, None)


def test_dedup_db_cooldown_expired():
    """БД cooldown — прошло 15 мин → LLM gate решает"""
    from core import workflow

    workflow._analyzed_in_session.clear()

    old = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()

    m = MagicMock()
    m.id = "mkt-15min"

    with patch("core.workflow.get_memory", return_value=old), \
         patch("core.workflow.save_memory"), \
         patch("config.llm_health_gate") as gate, \
         patch("core.workflow.build_search_query", return_value="test", create=True), \
         patch("core.workflow.concurrent.futures.ThreadPoolExecutor", new=MockExecutor), \
         patch("core.workflow._safe_result", return_value=[], create=True), \
         patch("core.workflow.fetch_google_trends", return_value={}, create=True), \
         patch("core.workflow.get_market_correlations", return_value=[], create=True):

        gate.check_availability.return_value = False  # DEGRADED
        result = asyncio.run(workflow.run_agent_evaluation(
            m=m, scout=MagicMock(), swing=MagicMock(),
            update_state=MagicMock()
        ))

    # DEGRADED → (None, None, None), но причина — не дедупликация
    assert result == (None, None, None)
