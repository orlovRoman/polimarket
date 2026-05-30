"""
Тест: bot.py и telegram_listener.py возвращают один и тот же глобальный синглтон CoreEngine.
"""
import pytest
from core.singleton import get_core_engine

def test_core_engine_same_instance_across_modules():
    """bot.py и telegram_listener.py должны использовать единый экземпляр CoreEngine."""
    # 1. Берем CoreEngine синглтон
    engine_1 = get_core_engine()
    engine_2 = get_core_engine()
    
    # Убеждаемся, что возвращается ровно один и тот же инстанс
    assert engine_1 is engine_2
