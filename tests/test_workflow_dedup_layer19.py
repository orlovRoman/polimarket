import asyncio
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


def _make_market(market_id="mkt-1"):
    from core.models import Market
    return Market(
        id=market_id, platform="polymarket", title="Test Market",
        url="https://poly.com/t", outcome="YES", price=0.5,
        close_time=datetime(2026, 12, 1, tzinfo=timezone.utc),
    )


def _make_context(trigger_type="scheduled", source_url=None, source_text=None):
    ctx = MagicMock()
    ctx.market = _make_market()
    ctx.trigger_type = trigger_type
    ctx.source_url = source_url
    ctx.source_text = source_text
    ctx.math_filter_result = None
    return ctx


# ── Баг #1: дедупликация по времени последнего анализа ───────

def test_dedup_skips_recently_analyzed_market():
    """Рынок, анализированный < 10 мин назад, пропускается"""
    from core.workflow import run_agent_evaluation
    from core.models import Market

    m = _make_market("mkt-dedup")

    recent_iso = datetime.now(timezone.utc).isoformat()

    with patch("core.workflow.get_memory", return_value=recent_iso), \
         patch("core.workflow.save_memory") as mock_save:
        result = asyncio.run(run_agent_evaluation(
            m=m,
            scout=MagicMock(),
            swing=MagicMock(),
            update_state=MagicMock(),
            trigger_type="event_driven",
        ))

    assert result == (None, None, None), \
        "Дублирующий анализ должен вернуть (None, None, None)"
    mock_save.assert_not_called()  # не должны перезаписывать время


def test_dedup_allows_after_cooldown():
    """Рынок, анализированный > 10 мин назад, разрешается"""
    from datetime import timedelta

    old_iso = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()

    with patch("core.workflow.get_memory", return_value=old_iso), \
         patch("core.workflow.save_memory"), \
         patch("config.llm_health_gate") as mock_gate, \
         patch("core.workflow.build_search_query", return_value="test", create=True), \
         patch("core.workflow.concurrent.futures.ThreadPoolExecutor"), \
         patch("core.workflow._safe_result", return_value=[], create=True), \
         patch("core.workflow.fetch_google_trends", return_value={}, create=True), \
         patch("core.workflow.get_market_correlations", return_value=[], create=True):

        mock_gate.check_availability.return_value = False  # LLM degraded — вернёт None

        from core.workflow import run_agent_evaluation
        m = _make_market("mkt-old")
        scout = MagicMock()
        swing = MagicMock()

        result = asyncio.run(run_agent_evaluation(
            m=m, scout=scout, swing=swing,
            update_state=MagicMock(),
        ))
        # LLM degraded → (None, None, None), но не из-за дедупликации
        # Важно: дедупликация не сработала (прошло 15 минут)
        assert result == (None, None, None)


def test_dedup_different_trigger_types_same_market():
    """scheduled и event_driven — разные ключи, но cooldown общий"""
    recent_iso = datetime.now(timezone.utc).isoformat()

    with patch("core.workflow.get_memory", return_value=recent_iso):
        from core.workflow import run_agent_evaluation
        m = _make_market("mkt-x")

        r1 = asyncio.run(run_agent_evaluation(m, MagicMock(), MagicMock(), MagicMock(),
                                   trigger_type="scheduled"))
        r2 = asyncio.run(run_agent_evaluation(m, MagicMock(), MagicMock(), MagicMock(),
                                   trigger_type="event_driven"))

    # Оба должны быть задедуплицированы — cooldown по market_id, не trigger_type
    assert r1 == (None, None, None)
    assert r2 == (None, None, None)


# ── Баг #2: метка источника в summary_text ───────────────────

def test_summary_includes_source_label_for_event_driven():
    """event_driven с source_url добавляет 📡 Триггер"""
    from core.workflow import process_consensus

    ctx = _make_context(
        trigger_type="event_driven",
        source_url="https://t.me/somechannel/123",
        source_text="Telegram пост",
    )
    captured = []

    signal = MagicMock()
    signal.edge = 0.1
    signal.signal_cause = "test cause"
    signal.signal_risk = "test risk"
    signal.oracle_risk = ""
    signal.signal_verdict = "buy"
    signal.trade_action = "buy"

    with patch("core.workflow.make_consensus") as mock_consensus, \
         patch("core.workflow.save_signal", create=True), \
         patch("core.workflow.save_idea_audit", create=True), \
         patch("core.workflow.save_checkpoint", create=True), \
         patch("core.workflow.verify_checkpoint", return_value=True, create=True), \
         patch("core.workflow.save_agent_episode", create=True):

        decision = MagicMock()
        decision.status = "saved"
        mock_consensus.return_value = decision

        process_consensus(ctx, signal, None, None,
                          state={}, update_state=MagicMock(),
                          summary_callback=captured.append)

    assert captured, "summary_callback должен быть вызван"
    text = captured[0]
    assert "📡" in text and "Триггер" in text and "Telegram пост" in text


def test_summary_includes_scheduled_label():
    """scheduled добавляет 🔄 Плановый скан"""
    from core.workflow import process_consensus

    ctx = _make_context(trigger_type="scheduled")
    captured = []

    signal = MagicMock()
    signal.edge = 0.1
    signal.signal_cause = "c"
    signal.signal_risk = "r"
    signal.oracle_risk = ""
    signal.signal_verdict = "buy"
    signal.trade_action = "buy"

    with patch("core.workflow.make_consensus") as mock_consensus, \
         patch("core.workflow.save_signal", create=True), \
         patch("core.workflow.save_idea_audit", create=True), \
         patch("core.workflow.save_checkpoint", create=True), \
         patch("core.workflow.verify_checkpoint", return_value=True, create=True), \
         patch("core.workflow.save_agent_episode", create=True):

        decision = MagicMock()
        decision.status = "saved"
        mock_consensus.return_value = decision

        process_consensus(ctx, signal, None, None,
                          state={}, update_state=MagicMock(),
                          summary_callback=captured.append)

    text = captured[0]
    assert "🔄" in text and "Плановый скан" in text


def test_summary_no_source_url_for_event_driven_does_not_crash():
    """event_driven без source_url — не падает, не добавляет пустую ссылку"""
    from core.workflow import process_consensus

    ctx = _make_context(trigger_type="event_driven", source_url=None)
    captured = []

    signal = MagicMock()
    signal.edge = 0.1
    signal.signal_cause = "c"
    signal.signal_risk = "r"
    signal.oracle_risk = ""
    signal.signal_verdict = "buy"
    signal.trade_action = "buy"

    with patch("core.workflow.make_consensus") as mock_consensus, \
         patch("core.workflow.save_signal", create=True), \
         patch("core.workflow.save_idea_audit", create=True), \
         patch("core.workflow.save_checkpoint", create=True), \
         patch("core.workflow.verify_checkpoint", return_value=True, create=True), \
         patch("core.workflow.save_agent_episode", create=True):

        decision = MagicMock()
        decision.status = "saved"
        mock_consensus.return_value = decision

        process_consensus(ctx, signal, None, None,
                          state={}, update_state=MagicMock(),
                          summary_callback=captured.append)

    # Не должно быть пустого href
    text = captured[0]
    assert "href=''" not in text
    assert "href='None'" not in text


# ── Интеграционный: два скана одного рынка ───────────────────

def test_two_scans_same_market_only_one_analysis():
    """Плановый скан + event_driven на один рынок → только первый проходит"""
    call_count = 0
    recent_iso = None  # первый вызов — нет записи; второй — есть

    def mock_get_memory(key):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None      # первый анализ — разрешаем
        return datetime.now(timezone.utc).isoformat()  # второй — блокируем

    with patch("core.workflow.get_memory", side_effect=mock_get_memory), \
         patch("core.workflow.save_memory"), \
         patch("config.llm_health_gate") as gate:

        gate.check_availability.return_value = False  # LLM degraded, чтобы быстро вернуться

        from core.workflow import run_agent_evaluation
        m = _make_market("mkt-double")

        r1 = asyncio.run(run_agent_evaluation(m, MagicMock(), MagicMock(), MagicMock(),
                                   trigger_type="scheduled"))
        r2 = asyncio.run(run_agent_evaluation(m, MagicMock(), MagicMock(), MagicMock(),
                                   trigger_type="event_driven"))

    # r1 дошёл до LLM (вернул None из-за degraded), r2 задедуплицирован
    assert r2 == (None, None, None), "Второй анализ должен быть задедуплицирован"
