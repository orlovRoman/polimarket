import threading
import core.engine
from agents.shared.python.db import init_db

_core_engine = None
_core_engine_lock = threading.Lock()

def get_core_engine():
    """Возвращает единственный экземпляр CoreEngine (синглтон) для всего процесса."""
    global _core_engine
    CoreEngineClass = core.engine.CoreEngine
    if _core_engine is None or not isinstance(_core_engine, CoreEngineClass):
        with _core_engine_lock:
            if _core_engine is None or not isinstance(_core_engine, CoreEngineClass):
                # Гарантируем, что БД инициализирована до инстанцирования синглтона
                init_db()
                _core_engine = CoreEngineClass()
    return _core_engine
