import inspect
import functools

@functools.lru_cache(maxsize=128)
def _callback_accepts_reply_markup(func) -> bool:
    """Кэширует результат проверки наличия reply_markup в сигнатуре колбэка."""
    try:
        sig = inspect.signature(func)
        return "reply_markup" in sig.parameters
    except (ValueError, TypeError):
        return False
