import re
from typing import Optional

# Диапазоны не-латинских, не-кириллических алфавитов
_NON_ALLOWED_RANGES = [
    (0x4E00, 0x9FFF),   # CJK Chinese
    (0x3040, 0x30FF),   # Hiragana / Katakana
    (0x0600, 0x06FF),   # Arabic
    (0x0900, 0x097F),   # Devanagari
    (0x0E00, 0x0E7F),   # Thai
]

def has_forbidden_script(text: str) -> bool:
    """Возвращает True если текст содержит иероглифы или запрещённые алфавиты."""
    for char in text:
        cp = ord(char)
        for lo, hi in _NON_ALLOWED_RANGES:
            if lo <= cp <= hi:
                return True
    return False

# Разрешённые паттерны внутри русского текста:
_ALLOWED_NON_CYRILLIC = re.compile(
    r'\b(YES|NO|BUY|ROI|pump|hype|ATH|ETH|BTC|USD|GDP|'
    r'[A-Z]{2,6}|'           # тикеры: WE, LDK, KPRF
    r'\d[\d.,]*|'            # числа
    r'[%$€#@&()\[\]+\-=:/]'  # спецсимволы
    r')\b',
    re.IGNORECASE
)

_FORBIDDEN_SCRIPTS = re.compile(
    r'[\u4e00-\u9fff'      # китайский
    r'\u0600-\u06ff'       # арабский
    r'\u0900-\u097f'       # хинди
    r'\u3040-\u30ff]'      # японский
)

def validate_russian_fields(data: dict, fields: list[str]) -> Optional[str]:
    """
    Проверяет указанные поля словаря на наличие запрещённых символов.
    Возвращает имя первого поля-нарушителя или None если всё ок.
    
    ИЗМЕНЕНИЕ: разрешаем короткие латинские аббревиатуры (тикеры команд),
    запрещаем только явные нелатинские скрипты (китайский, арабский и т.д.)
    """
    for field in fields:
        val = data.get(field, "")
        if not isinstance(val, str):
            continue
        # Убираем разрешённые паттерны из проверки
        cleaned = _ALLOWED_NON_CYRILLIC.sub("", val)
        if _FORBIDDEN_SCRIPTS.search(cleaned):
            return field
    return None
