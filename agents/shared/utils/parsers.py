import re
from typing import Optional, Tuple

def parse_numeric_level(question: str) -> Tuple[Optional[float], str]:
    """
    Извлекает числовой уровень из заголовка рынка.
    
    Примеры:
      "Will Anthropic valuation hit $1.5T..."  -> (1.5, "T")
      "Will Anthropic valuation hit $2B..."    -> (2.0, "B")
      "Will unemployment reach 5%..."          -> (5.0, "%")
      "Will S&P 500 hit 6000..."               -> (6000.0, "points")
    """
    # Денежные уровни с суффиксом
    m = re.search(r'\$\s*(\d+(?:\.\d+)?)\s*(T|B|M|K)\b', question, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        unit = m.group(2).upper()
        return val, unit
    
    # Проценты
    m = re.search(r'(\d+(?:\.\d+)?)\s*%', question)
    if m:
        return float(m.group(1)), "%"
    
    # Просто числа (индексы, очки, цены) - игнорируем запятые
    m = re.search(r'\b(\d{3,6}(?:[.,]\d+)?)\b', question)
    if m:
        return float(m.group(1).replace(",", "")), "points"
    
    return None, "unknown"
