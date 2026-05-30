"""
Тест для проверки синглтона get_core_engine:
Убеждаемся, что синглтон не пересоздаётся, если _core_engine уже является isinstance-совместимым наследником.
"""
import pytest
import core.singleton as singleton_module
from core.engine import CoreEngine

def test_core_engine_singleton_inheritance():
    # 1. Создаем класс-наследник от CoreEngine
    class SubclassCoreEngine(CoreEngine):
        def __init__(self):
            pass  # Переопределяем инициализатор, чтобы не вызывать исходный с тяжелыми зависимостями
            
    singleton_module._core_engine = None  # Сброс
    
    # 2. Инициализируем синглтон наследником
    fake_subclass_instance = SubclassCoreEngine()
    singleton_module._core_engine = fake_subclass_instance
    
    # 3. Вызываем get_core_engine и убеждаемся, что он возвращает именно наш существующий subclass-инстанс
    returned_engine = singleton_module.get_core_engine()
    assert returned_engine is fake_subclass_instance
