import inspect
import pytest

def test_shadow_agent_analyze_idea_is_callable():
    """
    analyze_idea должна быть вызываема без SyntaxError.
    Проверяем что функция существует и имеет корректную сигнатуру.
    """
    try:
        from agents.polymarket_insider_agent.src.agent import ShadowAgent
    except ImportError:
        pytest.skip("ShadowAgent недоступен в тестовом окружении")

    method = getattr(ShadowAgent, "analyze_idea", None)
    assert method is not None, "analyze_idea не найден в ShadowAgent"

    # Проверяем согласованность: если использует await — должна быть async
    sig = inspect.signature(method)
    source = inspect.getsource(method)
    uses_await = "await " in source
    is_async = inspect.iscoroutinefunction(method)

    assert uses_await == is_async, (
        f"Несогласованность: uses_await={uses_await}, is_async={is_async}. "
        "Если функция использует await — она должна быть async def."
    )

def test_shadow_agent_analyze_idea_not_broken_by_import():
    """Импорт модуля ShadowAgent не должен вызывать SyntaxError."""
    try:
        import importlib
        spec = importlib.util.find_spec(
            "agents.polymarket_insider_agent.src.agent"
        )
        if spec is None:
            pytest.skip("Модуль недоступен")
        importlib.import_module("agents.polymarket_insider_agent.src.agent")
    except SyntaxError as e:
        pytest.fail(f"SyntaxError в ShadowAgent: {e}")
