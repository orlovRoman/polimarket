import threading
from core.engine import CoreEngine
from agents.shared.python.db import init_db

_core_engine = None
_core_engine_lock = threading.Lock()

def get_core_engine() -> CoreEngine:
    """Возвращает единственный экземпляр CoreEngine (синглтон) для всего процесса."""
    global _core_engine
    if _core_engine is None or not isinstance(_core_engine, CoreEngine):
        with _core_engine_lock:
            if _core_engine is None or not isinstance(_core_engine, CoreEngine):
                # Гарантируем, что БД инициализирована до инстанцирования синглтона
                init_db()
                _core_engine = CoreEngine()
    return _core_engine
