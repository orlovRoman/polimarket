import asyncio
from unittest.mock import AsyncMock
# tests/test_workflow_resilience.py
import pytest
from unittest.mock import patch, MagicMock
from core.workflow import run_agent_evaluation, _analyzed_in_session
from core.checkpoint import get_checkpoint
from core.models import Market
from datetime import datetime, timezone


# ── Фикстуры ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_session_dedup():
    """Сбрасываем глобальный дедупликатор перед каждым тестом."""
    _analyzed_in_session.clear()
    yield
    _analyzed_in_session.clear()


def get_fake_market(mid: str) -> Market:
    return Market(
        id=mid,
        platform="polymarket",
        title="Test Market",
        url="http://test",
        outcome="YES",
        price=0.5,
        close_time=datetime(2026, 12, 31, tzinfo=timezone.utc)
    )


# Общий контекст-патч для всех тестов, блокирующий сеть и БД
COMMON_PATCHES = [
    patch("core.workflow.fetch_rss_news", return_value=[]),
    patch("core.workflow.fetch_reddit_news", return_value=[]),
    patch("agents.shared.utils.web_search.fetch_wikipedia_context", return_value=[]),
    patch("core.workflow.fetch_hackernews", return_value=[]),
    patch("core.workflow.fetch_google_trends", return_value=""),
    patch("core.workflow.get_market_correlations", return_value=[]),
    patch("core.workflow.get_memory", return_value=None),
    patch("core.workflow.save_memory"),
    patch("core.workflow.mark_market_analyzed"),
    patch("config.llm_health_gate"),
]


# ── Тест 1: LLMUnavailableError прерывает пайплайн ────────────────────────

def test_scout_llm_unavailable_raises_and_saves_checkpoint():
    """LLMUnavailableError из SCOUT пробрасывается наверх и сохраняет checkpoint."""
    from core.guards import LLMUnavailableError

    mock_scout = MagicMock()
    mock_swing = MagicMock()
    mock_scout.estimate_market = AsyncMock(side_effect=LLMUnavailableError("429 rate limit"))
    mock_swing.estimate_market = AsyncMock(return_value=MagicMock(recommendation="buy"))

    m = get_fake_market("mkt-resilience-1")

    with (
        patch("core.workflow.fetch_rss_news", return_value=[]),
        patch("core.workflow.fetch_reddit_news", return_value=[]),
        patch("agents.shared.utils.web_search.fetch_wikipedia_context", return_value=[]),
        patch("core.workflow.fetch_hackernews", return_value=[]),
        patch("core.workflow.fetch_google_trends", return_value=""),
        patch("core.workflow.get_market_correlations", return_value=[]),
        patch("core.workflow.get_memory", return_value=None),
        patch("core.workflow.save_memory"),
        patch("config.llm_health_gate") as mock_gate,
    ):
        mock_gate.check_availability.return_value = True

        with pytest.raises(LLMUnavailableError):
            asyncio.run(run_agent_evaluation(
                m=m,
                scout=mock_scout,
                swing=mock_swing,
                update_state=lambda **k: None,
            ))

    cp = get_checkpoint(f"scout_{m.id}")
    assert cp["status"] == "llm_unavailable"


# ── Тест 2: ValueError из SCOUT не блокирует SWING ────────────────────────

def test_scout_parse_error_does_not_block_swing():
    """ValueError в SCOUT → scout_signal=None, swing продолжает работать."""
    mock_scout = MagicMock()
    mock_swing = MagicMock()
    mock_scout.estimate_market = AsyncMock(side_effect=ValueError("Parse error"))
    mock_swing.estimate_market = AsyncMock(return_value=MagicMock(recommendation="buy"))

    m = get_fake_market("mkt-resilience-2")

    with (
        patch("core.workflow.fetch_rss_news", return_value=[]),
        patch("core.workflow.fetch_reddit_news", return_value=[]),
        patch("agents.shared.utils.web_search.fetch_wikipedia_context", return_value=[]),
        patch("core.workflow.fetch_hackernews", return_value=[]),
        patch("core.workflow.fetch_google_trends", return_value=""),
        patch("core.workflow.get_market_correlations", return_value=[]),
        patch("core.workflow.get_memory", return_value=None),
        patch("core.workflow.save_memory"),
        patch("config.llm_health_gate") as mock_gate,
    ):
        mock_gate.check_availability.return_value = True

        scout_signal, swing_signal, context = asyncio.run(run_agent_evaluation(
            m=m,
            scout=mock_scout,
            swing=mock_swing,
            update_state=lambda **k: None,
        ))

    # SCOUT упал — его сигнал None
    assert scout_signal is None
    # SWING продолжил работу
    assert swing_signal is not None

    cp_scout = get_checkpoint(f"scout_{m.id}")
    assert cp_scout["status"] == "error"

    cp_swing = get_checkpoint(f"swing_{m.id}")
    assert cp_swing["status"] == "ok"


# ── Тест 3: LLMUnavailableError из SWING тоже пробрасывается ─────────────

def test_swing_llm_unavailable_raises_and_saves_checkpoint():
    """LLMUnavailableError из SWING пробрасывается наверх после успеха SCOUT."""
    from core.guards import LLMUnavailableError

    mock_scout = MagicMock()
    mock_swing = MagicMock()
    mock_scout.estimate_market = AsyncMock(return_value=MagicMock(edge=0.12))
    mock_swing.estimate_market = AsyncMock(side_effect=LLMUnavailableError("503 overloaded"))

    m = get_fake_market("mkt-resilience-3")

    with (
        patch("core.workflow.fetch_rss_news", return_value=[]),
        patch("core.workflow.fetch_reddit_news", return_value=[]),
        patch("agents.shared.utils.web_search.fetch_wikipedia_context", return_value=[]),
        patch("core.workflow.fetch_hackernews", return_value=[]),
        patch("core.workflow.fetch_google_trends", return_value=""),
        patch("core.workflow.get_market_correlations", return_value=[]),
        patch("core.workflow.get_memory", return_value=None),
        patch("core.workflow.save_memory"),
        patch("config.llm_health_gate") as mock_gate,
    ):
        mock_gate.check_availability.return_value = True

        with pytest.raises(LLMUnavailableError):
            asyncio.run(run_agent_evaluation(
                m=m,
                scout=mock_scout,
                swing=mock_swing,
                update_state=lambda **k: None,
            ))

    cp_scout = get_checkpoint(f"scout_{m.id}")
    assert cp_scout["status"] == "ok"

    cp_swing = get_checkpoint(f"swing_{m.id}")
    assert cp_swing["status"] == "llm_unavailable"


# ── Тест 4: Дедупликация работает (in-session) ────────────────────────────

def test_deduplication_skips_repeated_market():
    """Повторный вызов для того же рынка возвращает (None, None, None)."""
    mock_scout = MagicMock()
    mock_swing = MagicMock()
    mock_scout.estimate_market = AsyncMock(return_value=MagicMock(edge=0.10))
    mock_swing.estimate_market = AsyncMock(return_value=MagicMock(recommendation="buy"))

    m = get_fake_market("mkt-dedup-1")

    with (
        patch("core.workflow.fetch_rss_news", return_value=[]),
        patch("core.workflow.fetch_reddit_news", return_value=[]),
        patch("agents.shared.utils.web_search.fetch_wikipedia_context", return_value=[]),
        patch("core.workflow.fetch_hackernews", return_value=[]),
        patch("core.workflow.fetch_google_trends", return_value=""),
        patch("core.workflow.get_market_correlations", return_value=[]),
        patch("core.workflow.get_memory", return_value=None),
        patch("core.workflow.save_memory"),
        patch("config.llm_health_gate") as mock_gate,
    ):
        mock_gate.check_availability.return_value = True

        result1 = asyncio.run(run_agent_evaluation(
            m=m, scout=mock_scout, swing=mock_swing,
            update_state=lambda **k: None,
        ))
        result2 = asyncio.run(run_agent_evaluation(
            m=m, scout=mock_scout, swing=mock_swing,
            update_state=lambda **k: None,
        ))

    # Первый вызов — полный анализ
    assert result1 != (None, None, None)
    # Второй — дедуплицирован
    assert result2 == (None, None, None)
    # SCOUT вызван ровно один раз
    mock_scout.estimate_market.assert_called_once()
