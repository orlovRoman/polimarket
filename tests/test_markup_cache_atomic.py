import threading
import inspect
from unittest.mock import patch, MagicMock
import core.engine as eng

def test_inspect_signature_called_once_per_func():
    """inspect.signature должен вызываться не более 1 раза на функцию."""
    eng._markup_cache.clear()

    call_count = 0
    real_signature = inspect.signature

    def counting_signature(func):
        nonlocal call_count
        call_count += 1
        return real_signature(func)

    def target_func(text, reply_markup=None): pass

    with patch("core.engine.inspect.signature", side_effect=counting_signature):
        threads = [
            threading.Thread(target=eng._callback_accepts_reply_markup, args=(target_func,))
            for _ in range(10)
        ]
        for t in threads: t.start()
        for t in threads: t.join()

    # С setdefault: только первый поток вычисляет signature, остальные берут из кэша
    # Без фикса: может быть до 10 вызовов
    assert call_count <= 2, (
        f"inspect.signature вызван {call_count} раз вместо ≤2 "
        f"(допускаем 2 при race в начале)"
    )
    assert eng._markup_cache[target_func] is True

def test_setdefault_never_overwrites_cached_value():
    """setdefault не перезаписывает уже кэшированное значение."""
    eng._markup_cache.clear()
    
    def no_markup_func(text): pass
    
    # Первый вызов — записывает False
    result1 = eng._callback_accepts_reply_markup(no_markup_func)
    # Второй вызов — берёт из кэша
    result2 = eng._callback_accepts_reply_markup(no_markup_func)
    
    assert result1 is False
    assert result2 is False
    assert eng._markup_cache[no_markup_func] is False
