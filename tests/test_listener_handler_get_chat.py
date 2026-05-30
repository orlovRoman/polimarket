import inspect
import ast
from services import telegram_listener

def test_handler_only_one_get_chat_call():
    """Проверяет, что event.get_chat() вызывается строго один раз внутри handler в main()"""
    main_source = inspect.getsource(telegram_listener.main)
    
    # Считаем количество вызовов await event.get_chat() в исходном коде main
    calls_count = main_source.count("await event.get_chat()")
    
    assert calls_count == 1, (
        f"event.get_chat() вызывается {calls_count} раз(а) в main/handler. "
        "Должен быть ровно один вызов для экономии RPC-запросов."
    )

def test_handler_ast_inspection():
    """Альтернативная детальная проверка через AST на отсутствие дублирующихся вызовов get_chat"""
    main_source = inspect.getsource(telegram_listener.main)
    parsed = ast.parse(main_source)
    
    # Ищем определение handler внутри main
    handler_node = None
    for node in ast.walk(parsed):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handler":
            handler_node = node
            break
            
    assert handler_node is not None, "Внутренняя функция 'handler' не найдена в main()"
    
    # Считаем вызовы get_chat внутри handler
    get_chat_calls = 0
    for node in ast.walk(handler_node):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Attribute) and func.attr == "get_chat":
                if isinstance(func.value, ast.Name) and func.value.id == "event":
                    get_chat_calls += 1
                    
    assert get_chat_calls == 1, (
        f"Найдено {get_chat_calls} вызова(ов) event.get_chat() через AST. "
        "Должен быть ровно один."
    )
