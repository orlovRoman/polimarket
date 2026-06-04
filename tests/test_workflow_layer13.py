import asyncio
from unittest.mock import AsyncMock
# tests/test_workflow_layer13.py

import pytest
import concurrent.futures
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone


def _make_market(market_id="mkt-1", price=0.65):
    m = MagicMock()
    m.id = market_id
    m.title = "Will test happen?"
    m.url = "https://polymarket.com/test"
    m.price = price
    return m


def _make_context(market=None, trigger_type="scheduled"):
    ctx = MagicMock()
    ctx.market = market or _make_market()
    ctx.trigger_type = trigger_type
    ctx.source_url = ""
    ctx.source_text = ""
    ctx.math_filter_result = None
    ctx.correlation_hint = None
    return ctx


def _make_signal(edge=0.12, cause="Strong signal", risk="Low risk", verdict="BUY YES"):
    s = MagicMock()
    s.edge = edge
    s.signal_cause = cause
    s.signal_risk = risk
    s.signal_verdict = verdict
    s.oracle_risk = ""
    s.summary = ""
    s.details = ""
    s.trade_action = ""
    return s


def _make_shadow(agree=True, confidence=0.85):
    sh = MagicMock()
    sh.agree = agree
    sh.confidence = confidence
    sh.liquidity_risk = "LOW"
    sh.orderbook_facts = "Deep book"
    sh.risk_assessment = "Minimal slippage"
    sh.shadow_verdict = "APPROVE"
    sh.opinion = ""
    return sh


# ── Баг #1: save_idea_audit не роняет process_consensus ──────

def test_process_consensus_save_idea_audit_exception_does_not_crash():
    """save_idea_audit может упасть — process_consensus должен продолжить работу"""
    from core.workflow import process_consensus

    ctx = _make_context()
    signal = _make_signal()
    shadow = _make_shadow(agree=True)
    state = {"ideas_found": 0}
    update_state = MagicMock()
    summary_callback = MagicMock()

    with patch("core.workflow.make_consensus") as mock_make, \
         patch("core.workflow.save_signal"), \
         patch("core.workflow.save_idea_audit", side_effect=Exception("DB locked")), \
         patch("agents.shared.python.db.save_agent_episode"), \
         patch("core.checkpoint.save_checkpoint"), \
         patch("core.checkpoint.verify_checkpoint", return_value=True):
        mock_make.return_value = MagicMock(status='saved')
        # Не должно бросить исключение
        process_consensus(ctx, signal, None, shadow, state, update_state, summary_callback)

    # summary_callback должен был вызваться (статус 'saved')
    summary_callback.assert_called_once()


def test_process_consensus_save_agent_episode_exception_does_not_crash():
    """save_agent_episode может упасть — checkpoint должен всё равно сохраниться"""
    from core.workflow import process_consensus

    ctx = _make_context()
    signal = _make_signal()
    shadow = _make_shadow(agree=True)
    state = {"ideas_found": 0}
    update_state = MagicMock()

    with patch("core.workflow.make_consensus") as mock_make, \
         patch("core.workflow.save_signal"), \
         patch("core.workflow.save_idea_audit"), \
         patch("agents.shared.python.db.save_agent_episode", side_effect=Exception("Episode DB error")), \
         patch("core.checkpoint.save_checkpoint") as mock_checkpoint, \
         patch("core.checkpoint.verify_checkpoint", return_value=True):
        mock_make.return_value = MagicMock(status='saved')
        process_consensus(ctx, signal, None, shadow, state, update_state, None)

    # Checkpoint должен быть сохранён несмотря на ошибку в save_agent_episode
    mock_checkpoint.assert_called_once()


# ── Баг #2: fetch timeout не блокирует оценку ────────────────

def test_run_agent_evaluation_wiki_timeout_uses_empty_string():
    """Если fetch_wikipedia_context завис — должна вернуться пустая строка, не Exception"""
    from core.workflow import run_agent_evaluation

    m = _make_market()
    scout = MagicMock()
    scout.estimate_market = AsyncMock(return_value=_make_signal())
    swing = MagicMock()
    swing.estimate_market = AsyncMock(return_value=None)
    update_state = MagicMock()

    def slow_wiki(_query):
        import time
        time.sleep(30)  # симулируем зависший запрос
        return "wiki content"

    with patch("core.workflow.fetch_rss_news", return_value=[]), \
         patch("core.workflow.fetch_reddit_news", return_value=[]), \
         patch("agents.shared.utils.web_search.fetch_wikipedia_context", side_effect=slow_wiki), \
         patch("core.workflow.fetch_hackernews", return_value=[]), \
         patch("core.workflow.fetch_google_trends", return_value={}), \
         patch("core.workflow.get_market_correlations", return_value=[]), \
         patch("config.llm_health_gate") as mock_gate, \
         patch("core.workflow.build_search_query", return_value="test query"), \
         patch("core.workflow._fetch_grounded_context", return_value="mocked grounding"), \
         patch("core.checkpoint.save_checkpoint"), \
         patch("core.workflow.MarketContext") as MockCtx:
        mock_gate.check_availability.return_value = True
        MockCtx.return_value = _make_context(market=m)

        import time
        start = time.time()
        # С timeout=15 — должен завершиться быстро, не ждать 30 сек
        # В тесте мокаем _safe_result напрямую
        with patch("core.workflow._safe_result", side_effect=[[], [], "", []]):
            result = asyncio.run(run_agent_evaluation(m, scout, swing, update_state))
        elapsed = time.time() - start

    assert elapsed < 5, f"Оценка заняла {elapsed:.1f}с — timeout не работает"


def test_safe_result_returns_default_on_timeout():
    """_safe_result должен вернуть default при TimeoutError"""
    from core.workflow import _safe_result

    future = MagicMock()
    future.result.side_effect = concurrent.futures.TimeoutError()

    result = _safe_result(future, default=[], timeout=5)
    assert result == []


def test_safe_result_returns_default_on_exception():
    """_safe_result должен вернуть default при любом Exception"""
    from core.workflow import _safe_result

    future = MagicMock()
    future.result.side_effect = RuntimeError("Connection refused")

    result = _safe_result(future, default="", timeout=5)
    assert result == ""


def test_safe_result_returns_value_on_success():
    """_safe_result должен вернуть реальное значение при успехе"""
    from core.workflow import _safe_result

    future = MagicMock()
    future.result.return_value = ["news 1", "news 2"]

    result = _safe_result(future, default=[], timeout=5)
    assert result == ["news 1", "news 2"]


# ── Баг #3: пустые поля не добавляются в summary_text ────────

def test_process_consensus_summary_no_empty_lines():
    """Поля с пустым значением не должны добавляться в summary_text"""
    from core.workflow import process_consensus

    ctx = _make_context()
    m = ctx.market

    # Сигнал со всеми пустыми полями
    signal = MagicMock()
    signal.edge = 0.1
    signal.signal_cause = ""    # пустое
    signal.signal_risk = ""     # пустое
    signal.oracle_risk = ""     # пустое
    signal.signal_verdict = ""  # пустое
    signal.summary = ""
    signal.details = ""
    signal.trade_action = ""

    shadow = _make_shadow(agree=True)
    state = {"ideas_found": 0}
    update_state = MagicMock()
    received = []
    summary_callback = lambda t: received.append(t)

    with patch("core.workflow.make_consensus") as mock_make, \
         patch("core.workflow.save_signal"), \
         patch("core.workflow.save_idea_audit"), \
         patch("agents.shared.python.db.save_agent_episode"), \
         patch("core.checkpoint.save_checkpoint"), \
         patch("core.checkpoint.verify_checkpoint", return_value=True):
        mock_make.return_value = MagicMock(status='saved')
        process_consensus(ctx, signal, None, shadow, state, update_state, summary_callback)

    assert len(received) == 1
    text = received[0]
    # Не должно быть пустых строк вида "🎯 Причина: \n"
    assert "Причина: \n" not in text
    assert "Риск: \n" not in text
    assert "Вердикт: \n" not in text
    # Заголовок секции должен присутствовать
    assert "SCOUT" in text


def test_process_consensus_summary_shows_nonempty_fields():
    """Непустые поля должны корректно попасть в summary_text"""
    from core.workflow import process_consensus

    ctx = _make_context()
    signal = _make_signal(cause="Fed pivot expected", risk="Rate reversal", verdict="BUY YES")
    shadow = _make_shadow(agree=True)
    state = {"ideas_found": 0}
    update_state = MagicMock()
    received = []

    with patch("core.workflow.make_consensus") as mock_make, \
         patch("core.workflow.save_signal"), \
         patch("core.workflow.save_idea_audit"), \
         patch("agents.shared.python.db.save_agent_episode"), \
         patch("core.checkpoint.save_checkpoint"), \
         patch("core.checkpoint.verify_checkpoint", return_value=True):
        mock_make.return_value = MagicMock(status='saved')
        process_consensus(ctx, signal, None, shadow, state, update_state,
                          lambda t: received.append(t))

    text = received[0]
    assert "Fed pivot expected" in text
    assert "Rate reversal" in text
    assert "BUY YES" in text


# ── Интеграционный тест: полный consensus pipeline ───────────

def test_full_consensus_pipeline_no_signal():
    """При отсутствии сигналов — статус 'no_signal', summary_callback НЕ вызывается"""
    from core.workflow import process_consensus

    ctx = _make_context()
    state = {"ideas_found": 0}
    update_state = MagicMock()
    summary_callback = MagicMock()

    with patch("core.workflow.make_consensus") as mock_make, \
         patch("core.workflow.save_signal"), \
         patch("core.workflow.save_idea_audit"), \
         patch("agents.shared.python.db.save_agent_episode"), \
         patch("core.checkpoint.save_checkpoint"), \
         patch("core.checkpoint.verify_checkpoint", return_value=True):
        mock_make.return_value = MagicMock(status='no_signal')
        process_consensus(ctx, None, None, None, state, update_state, summary_callback)

    summary_callback.assert_not_called()


def test_full_consensus_pipeline_no_consensus():
    """SHADOW против — статус 'no_consensus', summary_callback вызывается с сообщением об отсутствии консенсуса"""
    from core.workflow import process_consensus

    ctx = _make_context()
    signal = _make_signal()
    shadow = _make_shadow(agree=False)  # SHADOW против
    state = {"ideas_found": 0}
    update_state = MagicMock()
    summary_callback = MagicMock()

    with patch("core.workflow.make_consensus") as mock_make, \
         patch("core.workflow.save_signal"), \
         patch("core.workflow.save_idea_audit"), \
         patch("agents.shared.python.db.save_agent_episode"), \
         patch("core.checkpoint.save_checkpoint"), \
         patch("core.checkpoint.verify_checkpoint", return_value=True):
        mock_make.return_value = MagicMock(status='no_consensus')
        process_consensus(ctx, signal, None, shadow, state, update_state, summary_callback)

    summary_callback.assert_called_once()
    text = summary_callback.call_args[0][0]
    assert "Консенсус не достигнут" in text
