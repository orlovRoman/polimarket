from datetime import datetime, timezone

def _parse_dt_utc(s: str | None) -> datetime | None:
    """Парсит строку в UTC-aware datetime. Единственный источник правды."""
    if not s:
        return None
    try:
        s_norm = s.strip().replace(" ", "T", 1)
        # Нормализуем Z → +00:00 (fromisoformat не принимает Z до Python 3.11)
        if s_norm.endswith("Z"):
            s_norm = s_norm[:-1] + "+00:00"
        dt = datetime.fromisoformat(s_norm)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None
