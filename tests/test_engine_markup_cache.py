import threading
import pytest

def test_markup_cache_concurrent_writes():
    """Конкурентные вызовы не вызывают гонки."""
    # Сбрасываем кэш
    import core.engine as eng
    eng._markup_cache.clear()

    def func_with_markup(text, reply_markup=None): pass
    def func_without(text): pass

    errors = []
    results = []

    def worker(func):
        try:
            res = eng._callback_accepts_reply_markup(func)
            results.append(res)
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=worker, args=(func_with_markup,))
        for _ in range(20)
    ] + [
        threading.Thread(target=worker, args=(func_without,))
        for _ in range(20)
    ]
    for t in threads: t.start()
    for t in threads: t.join()

    assert not errors, f"Гонки при конкурентном доступе: {errors}"
    assert all(r is True for r in results[:20]), "func_with_markup должен вернуть True"
    assert all(r is False for r in results[20:]), "func_without должен вернуть False"

def test_markup_cache_unhashable_func():
    """functools.partial (нехэшируемый) не вызывает TypeError."""
    import core.engine as eng
    import functools
    partial_fn = functools.partial(lambda text, reply_markup=None: None)
    # Не должно бросать исключение
    result = eng._callback_accepts_reply_markup(partial_fn)
    assert isinstance(result, bool)
