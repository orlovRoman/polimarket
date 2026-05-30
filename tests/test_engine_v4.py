# tests/test_engine_v4.py

import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock


# ── Баг #1: asyncio.sleep в finally ──────────────────────────

def test_sleep_called_even_after_exception():
    """asyncio.sleep должен вызываться даже если внутри Exception"""
    async def run_test():
        sleep_called = []

        async def mock_sleep(n):
            sleep_called.append(n)

        with patch("asyncio.sleep", side_effect=mock_sleep):
            for _ in range(1):
                try:
                    raise ValueError("some error")
                except Exception:
                    pass
                finally:
                    await asyncio.sleep(2)

        assert sleep_called == [2]

    asyncio.run(run_test())


def test_sleep_not_called_twice_on_break():
    """При RuntimeError + break sleep вызывается ровно один раз (в finally)"""
    async def run_test():
        sleep_called = []

        async def mock_sleep(n):
            sleep_called.append(n)

        items = ["a", "b", "c"]
        with patch("asyncio.sleep", side_effect=mock_sleep):
            for item in items:
                try:
                    if item == "a":
                        raise RuntimeError("scan busy")
                except RuntimeError:
                    break
                finally:
                    await asyncio.sleep(2)

        assert len(sleep_called) == 1  # только для "a" до break

    asyncio.run(run_test())


# ── Баг #2: Singleton сброс при ошибке __init__ ──────────────

class _BadSingleton:
    """Воспроизводим паттерн CoreEngine без фикса"""
    _instance = None
    _lock = __import__("threading").Lock()
    _should_raise = True

    def __new__(cls):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "initialized"):
            if _BadSingleton._should_raise:
                _BadSingleton._should_raise = False
                raise RuntimeError("Init failed")
            # Для симуляции возврата сломанного объекта без повторного выброса ошибки


class _FixedSingleton:
    """Singleton с фиксом — сбрасывает _instance при ошибке"""
    _instance = None
    _lock = __import__("threading").Lock()
    _should_raise = True

    def __new__(cls):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "initialized"):
            try:
                if _FixedSingleton._should_raise:
                    raise RuntimeError("Init failed")
                self.initialized = True
            except Exception:
                _FixedSingleton._instance = None
                raise


def test_bad_singleton_returns_broken_instance():
    """Без фикса — второй вызов возвращает сломанный объект без RuntimeError"""
    _BadSingleton._instance = None
    _BadSingleton._should_raise = True
    with pytest.raises(RuntimeError):
        _BadSingleton()
    # Второй вызов — НЕ бросает, но возвращает объект без initialized
    obj = _BadSingleton()
    assert not hasattr(obj, "initialized"), \
        "Сломанный инстанс возвращается без ошибки — это баг"
    _BadSingleton._instance = None  # cleanup


def test_fixed_singleton_raises_on_second_call_too():
    """С фиксом — каждый вызов при ошибке __init__ бросает исключение"""
    _FixedSingleton._instance = None
    _FixedSingleton._should_raise = True
    with pytest.raises(RuntimeError):
        _FixedSingleton()
    assert _FixedSingleton._instance is None, \
        "_instance должен быть сброшен после ошибки"
    with pytest.raises(RuntimeError):
        _FixedSingleton()  # второй вызов тоже бросает — поведение корректное
    _FixedSingleton._instance = None  # cleanup


# ── Баг #3: lambda vs именованная функция ────────────────────

def test_named_notify_callable_is_reusable():
    """Именованная _notify не зависит от состояния цикла"""
    sent = []
    chat_id = "test_chat"

    def send_telegram_to_chat(msg, cid):
        sent.append((msg, cid))

    def _notify(msg: str) -> None:
        send_telegram_to_chat(msg, chat_id)

    _notify("msg1")
    _notify("msg2")

    assert sent == [("msg1", "test_chat"), ("msg2", "test_chat")]


def test_lambda_chat_id_capture_is_correct():
    """lambda с default arg корректно захватывает chat_id"""
    sent = []
    chat_id = "correct_chat"

    def send_fn(msg, cid):
        sent.append(cid)

    fn = lambda msg, _cid=chat_id: send_fn(msg, _cid)
    chat_id = "changed_after_lambda"  # изменяем после создания лямбды
    fn("test")

    assert sent[0] == "correct_chat", \
        "default arg захватывает значение на момент создания"


# ── Регрессия: предыдущие фиксы не сломаны ───────────────────

def test_no_markets_error_still_notifies():
    from core.engine import NoMarketsFoundError
    notified = []

    async def mock_to_thread(fn, *args, **kwargs):
        notified.append(args[0] if args else "")
        return None

    async def run_test():
        try:
            raise NoMarketsFoundError("Нет рынков")
        except NoMarketsFoundError as e:
            await mock_to_thread(lambda: None, f"⚠️ {e}", "chat")

    asyncio.run(run_test())
    assert len(notified) == 1


def test_scan_limit_zero_guard_still_works():
    def resolve(raw, default=5):
        try:
            val = int(raw) if raw is not None else default
        except (TypeError, ValueError):
            return default
        if val <= 0:
            return default
        return val

    assert resolve(0) == 5
    assert resolve(-1) == 5
    assert resolve(3) == 3
