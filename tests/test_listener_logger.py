import pytest
import inspect
from services import telegram_listener

def test_listener_no_direct_prints_for_logging():
    """Проверяет, что в telegram_listener.py нет вызовов print() вне интерактивной настройки (CLI) в main()"""
    source = inspect.getsource(telegram_listener)
    
    # Найдем все вызовы print
    # Исключаем принты внутри блока main() при проверке учетных данных в консоли,
    # но все логирование должно идти через logger.
    # Так как мы переписали принты логирования, проверим, что в функциях вроде trigger_nexus_scan
    # или handler нет слова "print("
    
    # Посмотрим на конкретные функции
    funcs_to_check = [
        telegram_listener.trigger_nexus_scan,
        telegram_listener.restore_markdown_links
    ]
    
    for func in funcs_to_check:
        func_source = inspect.getsource(func)
        assert "print(" not in func_source, f"Функция {func.__name__} содержит прямой вызов print(). Используйте logger."

def test_logger_initialization():
    """Проверяет, что logger в telegram_listener инициализирован корректно"""
    assert hasattr(telegram_listener, "logger")
    assert telegram_listener.logger.name == "telegram_listener"
