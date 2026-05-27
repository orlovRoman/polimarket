import sys
import re

with open('agents/shared/adapters/polymarket.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Add _get_end_date
if '_get_end_date' not in text:
    method = '''
    def _get_end_date(self, item: dict):
        """
        Универсальный парсер даты закрытия.
        Polymarket API возвращает разные имена поля в зависимости от эндпоинта.
        """
        from datetime import datetime, timezone
        for field in ("endDate", "end_date_iso", "endDateIso", "end"):
            raw = item.get(field)
            if raw:
                try:
                    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    continue
        # Fallback: рынок без даты — ставим далёкое будущее, чтобы не фильтровался
        return datetime(2099, 12, 31, tzinfo=timezone.utc)
'''
    # Insert it right after the __init__ method
    text = re.sub(r'(def __init__.*?)(def list_markets)', r'\1' + method + r'\n    \2', text, flags=re.DOTALL)

# Replacement 1: list_markets
text = re.sub(
    r'close_time=datetime\.fromisoformat\(item\["endDate"\]\.replace\("Z", "\+00:00"\)\),',
    r'close_time=self._get_end_date(item),',
    text
)

# Replacement 2: _parse_markets
text = re.sub(
    r'close_time=datetime\.fromisoformat\(item\.get\("endDate", item\.get\("end_date_iso", ""\)\)\.replace\("Z", "\+00:00"\)\),',
    r'close_time=self._get_end_date(item),',
    text
)
# just in case it was item["endDate"] in _parse_markets
text = re.sub(
    r'close_time=datetime\.fromisoformat\(item\["endDate"\]\.replace\("Z", "\+00:00"\)\),',
    r'close_time=self._get_end_date(item),',
    text
)

# Replacement 3: get_market
text = re.sub(
    r'close_time=datetime\.fromisoformat\(item\["endDate"\]\.replace\("Z", "\+00:00"\)\),',
    r'close_time=self._get_end_date(item),',
    text
)

# Replacement 4: list_all_markets_compact
text = re.sub(
    r'"end": item\.get\("endDate", ""\)',
    r'"end": item.get("endDate") or item.get("end_date_iso") or item.get("endDateIso") or ""',
    text
)

with open('agents/shared/adapters/polymarket.py', 'w', encoding='utf-8') as f:
    f.write(text)
print('Fixed E3')
