import threading
import core.engine
from agents.shared.python.db import init_db

_core_engine = None
_core_engine_lock = threading.Lock()

def get_core_engine():
    """Возвращает единственный экземпляр CoreEngine (синглтон) для всего процесса."""
    global _core_engine
    CoreEngineClass = core.engine.CoreEngine
    
    try:
        is_valid = isinstance(_core_engine, CoreEngineClass)
    except TypeError:
        is_valid = False
        
    if _core_engine is None or not is_valid:
        with _core_engine_lock:
            try:
                is_valid_locked = isinstance(_core_engine, CoreEngineClass)
            except TypeError:
                is_valid_locked = False
                
            if _core_engine is None or not is_valid_locked:
                # Гарантируем, что БД инициализирована до инстанцирования синглтона
                init_db()
                _core_engine = CoreEngineClass()
    return _core_engine
