import os
import py_compile
import warnings

def test_no_invalid_escape_sequences_in_db_py():
    db_py_path = os.path.join("agents", "shared", "python", "db.py")
    assert os.path.exists(db_py_path), f"Файл не найден по пути: {db_py_path}"
    
    # Compile the file and capture any warnings (specifically invalid escape sequence SyntaxWarnings)
    with warnings.catch_warnings(record=True) as w_list:
        warnings.simplefilter("always")
        py_compile.compile(db_py_path, doraise=True)
        
        # Check if any SyntaxWarnings were triggered by invalid escape sequences
        syntax_warnings = [
            w for w in w_list 
            if issubclass(w.category, SyntaxWarning) and "invalid escape sequence" in str(w.message)
        ]
        
        assert len(syntax_warnings) == 0, f"Найдены SyntaxWarning по некорректным escape-последовательностям: {[str(w.message) for w in syntax_warnings]}"
        
    with open(db_py_path, "r", encoding="utf-8") as f:
        source = f.read()
        
    # Проверим, что ESCAPE '\' всегда пишется как r""" или r'''
    # Мы можем убедиться, что подстроки `"""\n                    SELECT key, value FROM memory` без префикса `r` в коде нет!
    # Иными словами, каждый раз когда есть `ESCAPE '\'`, перед тройными кавычками `"""` стоит буква `r`.
    # Для этого найдем все вхождения `ESCAPE '\'` и проверим, что перед ними в коде нет `"""` без предшествующего `r`.
    # Мы можем сделать это, проверив, что `r"""` есть в коде.
    assert "r\"\"\"" in source, "Ошибка: не найден raw-строковый префикс r\"\"\"!"
