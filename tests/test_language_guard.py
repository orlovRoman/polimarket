import pytest
from agents.shared.utils.language_guard import validate_russian_fields

@pytest.mark.parametrize("text,should_fail", [
    # Разрешено
    ("Команда WE выиграла матч", False),
    ("BTC достиг ATH в $100k", False),
    ("KPRF набрала 4% голосов", False),
    ("ROI: 12.5% | Edge: 0.034", False),
    # Запрещено
    ("团队赢делили比赛", True),                    # чистый китайский
    ("Команда 微博 WE выиграла 团队", True),    # русский + китайские иероглифы
    ("مرحبا بالعالم", True),                  # арабский
])
def test_validate_russian_fields(text, should_fail):
    data = {"reasoning": text}
    result = validate_russian_fields(data, ["reasoning"])
    if should_fail:
        assert result == "reasoning"
    else:
        assert result is None

def test_sanitize_forbidden_scripts():
    from agents.shared.utils.language_guard import sanitize_forbidden_scripts
    
    # Смешанный текст
    text = "Команда 微博 WE выиграла 团队!"
    sanitized = sanitize_forbidden_scripts(text)
    
    # Иероглифы должны быть заменены на пустую строку
    assert "微博" not in sanitized
    assert "团队" not in sanitized
    assert "Команда  WE выиграла !" in sanitized
    
    # После санитизации проверка на запрещённые скрипты должна проходить успешно
    data = {"reasoning": sanitized}
    assert validate_russian_fields(data, ["reasoning"]) is None

