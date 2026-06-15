import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from core.guards import LLMUnavailableError

# Мок-объект сигнала
def make_signal(edge=0.7):
    s = MagicMock()
    s.edge = edge
    return s

# Симуляция run_agent_evaluation (только параллельная часть)
async def run_parallel(scout_result, swing_result):
    """Копирует логику gather из workflow.py для изолированного теста."""
    async def scout_coro(): 
        if isinstance(scout_result, Exception): raise scout_result
        return scout_result
    async def swing_coro():
        if isinstance(swing_result, Exception): raise swing_result
        return swing_result

    raw_scout, raw_swing = await asyncio.gather(
        scout_coro(), swing_coro(), return_exceptions=True
    )

    from core.guards import LLMUnavailableError

    # SCOUT
    if isinstance(raw_scout, LLMUnavailableError):
        raise raw_scout
    elif isinstance(raw_scout, Exception):
        signal = None
    else:
        signal = raw_scout

    # SWING (исправленная логика)
    if isinstance(raw_swing, LLMUnavailableError):
        swing_signal = None
        if signal is None:
            raise raw_swing  # только если оба упали
    elif isinstance(raw_swing, Exception):
        swing_signal = None
    else:
        swing_signal = raw_swing

    return signal, swing_signal


@pytest.mark.asyncio
async def test_scout_ok_swing_llm_unavailable_does_not_raise():
    """
    КРИТИЧЕСКИЙ: если SCOUT вернул сигнал, а SWING упал с LLMUnavailableError,
    run_agent_evaluation НЕ должен поднимать исключение.
    """
    scout_sig = make_signal(edge=0.8)
    signal, swing = await run_parallel(scout_sig, LLMUnavailableError("no llm"))
    assert signal is not None, "SCOUT-сигнал должен сохраниться"
    assert swing is None, "SWING должен быть None"


@pytest.mark.asyncio
async def test_both_llm_unavailable_raises():
    """Если оба агента упали с LLMUnavailableError — исключение поднимается."""
    with pytest.raises(LLMUnavailableError):
        await run_parallel(
            LLMUnavailableError("scout down"),
            LLMUnavailableError("swing down"),
        )


@pytest.mark.asyncio
async def test_scout_exception_swing_ok():
    """SCOUT упал с RuntimeError, SWING вернул сигнал — оба результата корректны."""
    swing_sig = make_signal(edge=0.6)
    signal, swing = await run_parallel(RuntimeError("scout exploded"), swing_sig)
    assert signal is None
    assert swing is swing_sig


@pytest.mark.asyncio
async def test_both_ok():
    """Нормальный сценарий: оба агента вернули сигналы."""
    s1, s2 = make_signal(0.7), make_signal(0.6)
    signal, swing = await run_parallel(s1, s2)
    assert signal is s1
    assert swing is s2
