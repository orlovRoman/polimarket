import re
from typing import Optional

# Диапазоны не-латинских, не-кириллических алфавитов
_NON_ALLOWED_RANGES = [
    (0x4E00, 0x9FFF),   # CJK Chinese
    (0x3040, 0x30FF),   # Hiragana / Katakana
    (0x0600, 0x06FF),   # Arabic
    (0x0900, 0x097F),   # Devanagari
    (0x0E00, 0x0E7F),   # Thai
    (0x00C0, 0x00D6),   # Latin Extended (частично French/German)
    (0x00D8, 0x00F6),
    (0x00F8, 0x00FF),
]

def has_forbidden_script(text: str) -> bool:
    """Возвращает True если текст содержит иероглифы или запрещённые алфавиты."""
    for char in text:
        cp = ord(char)
        for lo, hi in _NON_ALLOWED_RANGES:
            if lo <= cp <= hi:
                return True
    return False


def validate_russian_fields(data: dict, fields: list[str]) -> Optional[str]:
    """
    Проверяет указанные поля словаря на наличие запрещённых символов.
    Возвращает имя первого поля-нарушителя или None если всё ок.
    """
    for field in fields:
        value = data.get(field, "")
        if value and has_forbidden_script(str(value)):
            return field
    return None
