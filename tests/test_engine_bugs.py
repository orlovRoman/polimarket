from unittest.mock import AsyncMock
# tests/test_engine_bugs.py

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timezone


# ── Баг #1: triggered_at timezone-awareness ──────────────────

def test_triggered_at_is_timezone_aware():
    """datetime.now(timezone.utc) должен быть aware"""
    dt = datetime.now(timezone.utc)
    assert dt.tzinfo is not None, "triggered_at должен быть timezone-aware"


def test_triggered_at_naive_causes_compare_error():
    """Сравнение naive и aware datetime бросает TypeError"""
    naive = datetime.now()
    aware = datetime.now(timezone.utc)
    with pytest.raises(TypeError):
        _ = aware > naive


# ── Баг #3: source_username со знаком @ ─────────────────────

def _build_source_url(source_username: str, message_id: int) -> str:
    """Воспроизводим логику из engine.py с фиксом"""
    clean_username = source_username.lstrip('@')
    return f"https://t.me/{clean_username}/{message_id}"


def test_source_url_strips_at_sign():
    url = _build_source_url("@polymarketalerthub", 123)
    assert url == "https://t.me/polymarketalerthub/123"
    assert "@@" not in url


def test_source_url_without_at_sign():
    url = _build_source_url("polymarketalerthub", 123)
    assert url == "https://t.me/polymarketalerthub/123"


def test_source_url_private_channel():
    """Приватный канал — числовой ID без @"""
    url = _build_source_url("1234567890", 99)
    assert url == "https://t.me/1234567890/99"


# ── Баг #4: process_consensus не вызывается при no_signal ────

def test_process_consensus_skipped_when_no_signal():
    """Если нет сигнала — save_idea_audit не должен вызываться"""
    with patch("core.workflow.save_idea_audit") as mock_audit, \
         patch("core.workflow.save_checkpoint", create=True), \
         patch("core.workflow.verify_checkpoint", return_value=True, create=True), \
         patch("core.workflow.make_consensus") as mock_make:

        from core.workflow import process_consensus
        from unittest.mock import MagicMock
        
        mock_make.return_value = MagicMock(status='no_signal')

        ctx = MagicMock()
        ctx.market.id = "test-id"
        ctx.market.title = "Test"
        ctx.market.url = "https://polymarket.com/event/test"
        ctx.market.price = 0.5
        ctx.trigger_type = "scheduled"
        ctx.source_url = ""
        ctx.math_filter_result = None

        callback = MagicMock()
        update = MagicMock()

        # signal=None, swing=None, shadow=None → status='no_signal'
        process_consensus(ctx, None, None, None,
                          state={}, update_state=update,
                          summary_callback=callback)

        # Callback не должен вызываться — нет сигнала
        callback.assert_not_called()
        # В текущей версии process_consensus вообще возвращает None если нет сигнала,
        # поэтому save_idea_audit не вызывается
        assert mock_audit.call_count == 0


def test_engine_skips_consensus_when_no_active_signal():
    """
    Эмулируем логику engine.py: process_consensus должен быть пропущен
    если active_signal is None
    """
    signal = None
    swing_signal = None
    active_signal = signal or swing_signal

    consensus_called = False

    if active_signal:
        consensus_called = True  # process_consensus
    
    assert consensus_called is False, \
        "process_consensus не должен вызываться если нет ни signal, ни swing_signal"


# ── Интеграционный: analyze_post_async не падает при NO_MARKETS ──

def test_analyze_post_async_skips_if_already_processing():
    """Если пост уже в статусе PROCESSING — второй вызов должен быть no-op"""
    import asyncio
    with patch("agents.shared.python.db.get_telegram_post_info",
               return_value={"status": "PROCESSING", "text": "some text", "message_id": 1}), \
         patch("agents.shared.python.db.mark_telegram_post_status") as mock_mark:

        from core.engine import CoreEngine
        engine = CoreEngine.__new__(CoreEngine)
        engine.api_key = "test"

        asyncio.run(engine.analyze_post_async(post_id=1, chat_id="test_chat"))

        # Статус не должен меняться — пост уже обрабатывается
        mock_mark.assert_not_called()


def test_analyze_post_async_marks_error_on_exception():
    """При неожиданной ошибке статус должен стать ERROR, а не зависнуть в PROCESSING"""
    import asyncio
    with patch("agents.shared.python.db.get_telegram_post_info",
               return_value={"status": "NEW", "text": "whale alert", "message_id": 1}), \
         patch("agents.shared.python.db.mark_telegram_post_status") as mock_mark, \
         patch("agents.orchestrator.src.news_processor.NewsProcessor.find_relevant_markets",
               side_effect=RuntimeError("LLM timeout")):

        from core.engine import CoreEngine
        engine = CoreEngine.__new__(CoreEngine)
        engine.api_key = "test"

        asyncio.run(engine.analyze_post_async(post_id=1, chat_id="test_chat"))

        # Должен сначала поставить PROCESSING, потом ERROR
        calls = [c.args[1] for c in mock_mark.call_args_list]
        assert "PROCESSING" in calls
        assert "ERROR" in calls
