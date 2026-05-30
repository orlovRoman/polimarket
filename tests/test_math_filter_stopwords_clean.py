import inspect, re, pytest

def _extract_stopwords_set(source: str) -> list[str]:
    """Извлекает строковые литералы из блока stopwords = {...}"""
    m = re.search(r"stopwords\s*=\s*\{([^}]+)\}", source, re.DOTALL)
    if not m:
        return []
    block = m.group(1)
    return re.findall(r"'(\w+)'", block)

def test_no_duplicate_stopwords():
    """В stopwords не должно быть дублирующихся строковых литералов."""
    from core import math_filter as mf
    source = inspect.getsource(mf._check_same_event)
    words = _extract_stopwords_set(source)
    duplicates = [w for w in set(words) if words.count(w) > 1]
    assert not duplicates, (
        f"Дублирующиеся стопворды в _check_same_event: {duplicates}"
    )

def test_close_appears_once():
    """'close' должен встречаться ровно один раз в стопвордах."""
    from core import math_filter as mf
    source = inspect.getsource(mf._check_same_event)
    words = _extract_stopwords_set(source)
    assert words.count('close') == 1, (
        f"'close' встречается {words.count('close')} раз(а), ожидался 1"
    )
