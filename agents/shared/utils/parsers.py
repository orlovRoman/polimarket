import re
from typing import Optional, Tuple

def _mask_years(text: str) -> str:
    """Маскирует 4-значные годы, чтобы парсер не воспринял их как уровни."""
    patterns = [
        r'^\s*(1[9]\d{2}|20\d{2})\b',  # год в начале строки
        r'\b(1[9]\d{2}|20\d{2})\s+(season|election|midterm|world\s+cup|cup|championship)\b', # контекст
        r'\b(in|by|on|since|until|before|after)\s+\w*\s*(1[9]\d{2}|20\d{2})\b',  # "by April 2022"
        r'\b(1[9]\d{2}|20\d{2})[-/]\d{2}[-/]\d{2}\b',   # "2023-05-19", "2023/05/19"
        r'\(\d{2}/\d{2}/(1[9]\d{2}|20\d{2})\)',          # "(02/22/2023)"
        r':\s*(1[9]\d{2}|20\d{2})[-\s]',                  # ": 2023-", ": 2023 "
        r'\b(1[9]\d{2}|20\d{2})\b(?=\s*[)\].,])',         # год перед закрывающей скобкой/пунктуацией
        r'\b(1[9]\d{2}|20\d{2})\b(?!\s*[%$])',            # standalone год, за которым не идёт % или $
    ]
    masked = text
    for p in patterns:
        masked = re.sub(p, lambda m: 'YEAR_MASKED', masked, flags=re.IGNORECASE)
    return masked

def parse_numeric_level(question: str) -> Optional[Tuple[float, str]]:
    """
    Извлекает числовой уровень из заголовка рынка.
    
    Примеры:
      "Will Anthropic valuation hit $1.5T..."  -> (1.5, "T")
      "Will Anthropic valuation hit $2B..."    -> (2.0, "B")
      "Will unemployment reach 5%..."          -> (5.0, "%")
      "Will S&P 500 hit 6000..."               -> (6000.0, "points")
    """
    masked = _mask_years(question)

    def _parse(text: str) -> Optional[Tuple[float, str]]:
        # Денежные уровни с суффиксом
        m = re.search(r'\$\s*(\d+(?:\.\d+)?)\s*(T|B|M|K)\b', text, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            unit = m.group(2).upper()
            return val, unit
        
        # Денежные уровни без суффикса ($80, $150,000)
        m = re.search(r'\$\s*(\d+(?:[.,]\d+)*)\b', text)
        if m:
            val = float(m.group(1).replace(",", ""))
            return val, "points"

        # Проценты
        m = re.search(r'(\d+(?:\.\d+)?)\s*%', text)
        if m:
            return float(m.group(1)), "%"
        
        # Числа после ключевых слов (reach, above, exceed, hit, over, under, below)
        m = re.search(r'(?:above|reach|exceed|hit|to|over|under|below)\s+(\d+(?:[.,]\d+)*)\b', text, re.IGNORECASE)
        if m:
            val = float(m.group(1).replace(",", ""))
            return val, "points"

        # S&P 500 исключение (до фолбэка)
        if re.search(r'S&P\s*500', text, re.IGNORECASE):
            m = re.search(r'\b([5-9]\d{3,5}|[1-9]\d{4,5})\b', text)
            if m:
                return float(m.group(1).replace(",", "")), "points"
            return None

        # Просто числа (индексы, очки, цены) - фолбэк для старых или нестандартных форматов
        m = re.search(r'\b(\d{3,6}(?:[.,]\d+)?)\b', text)
        if m:
            return float(m.group(1).replace(",", "")), "points"
        
        return None

    result = _parse(masked)
    
    if result is None:
        return None

    value, unit = result
    if unit == 'points' and 1900 <= value <= 2100:
        return None  # Год, а не уровень
            
    return result
