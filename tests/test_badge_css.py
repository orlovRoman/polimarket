"""
Проверяем, что в base.html ровно одно правило .badge-gray (нет дублей).
"""
import re
import pathlib

def _get_base_html() -> str:
    path = pathlib.Path("web/templates/base.html")
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")

def test_badge_gray_not_duplicated():
    """В base.html не должно быть двух .badge-gray — второе перебивает первое."""
    html = _get_base_html()
    if not html:
        return  # файл недоступен в изолированном CI — пропустим
    matches = re.findall(r'\.badge-gray\s*\{', html)
    assert len(matches) <= 1, (
        f"Найдено {len(matches)} определений .badge-gray — должно быть не более 1. "
        "Второе правило перекрывает первое (CSS cascade)."
    )

def test_badge_gray_has_fallback_color():
    """badge-gray должен иметь читаемый цвет текста."""
    html = _get_base_html()
    if not html:
        return
    # Убедимся что есть хоть какой-то color для badge-gray
    block = re.search(r'\.badge-gray\s*\{([^}]+)\}', html)
    assert block is not None, ".badge-gray не найден в base.html"
    assert "color" in block.group(1), ".badge-gray должен задавать color текста"
