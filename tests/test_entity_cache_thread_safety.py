"""Тест: _entity_username_cache thread-safe при параллельных обновлениях."""
import threading
from services.telegram_listener import _set_cached_username, _get_cached_username

def test_cache_no_race_condition():
    errors = []
    def writer(chat_id):
        try:
            for i in range(100):
                _set_cached_username(chat_id, f"user_{i}")
        except Exception as e:
            errors.append(e)

    def reader(chat_id):
        try:
            for _ in range(100):
                _get_cached_username(chat_id)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i % 5,)) for i in range(5)]
    threads += [threading.Thread(target=reader, args=(i % 5,)) for i in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert not errors, f"Race condition обнаружен: {errors}"
