import pytest

def status_to_badge(status: str) -> str:
    """Зеркало JS-логики из scout.html (строка 114)."""
    if status == "WIN":
        return "badge-green"
    if status == "LOSS":
        return "badge-red"
    if status == "ARCHIVED":
        return "badge-gray"
    return "badge-blue"

def status_to_display(status: str) -> str:
    """Как отображать статус пользователю."""
    return "ОЖИДАЕТ ИТОГА" if status == "ARCHIVED" else status

@pytest.mark.parametrize("status, badge, display", [
    ("WIN",      "badge-green", "WIN"),
    ("LOSS",     "badge-red",   "LOSS"),
    ("PENDING",  "badge-blue",  "PENDING"),
    ("ACTIVE",   "badge-blue",  "ACTIVE"),
    # Главный: ARCHIVED не должен выглядеть как живой
    ("ARCHIVED", "badge-gray",  "ОЖИДАЕТ ИТОГА"),
])
def test_status_badge_and_display(status, badge, display):
    assert status_to_badge(status) == badge
    assert status_to_display(status) == display

def test_archived_not_active_visually():
    """ARCHIVED никогда не должен получать badge-blue (цвет активного)."""
    assert status_to_badge("ARCHIVED") != "badge-blue", (
        "ARCHIVED выглядит как активный сигнал — вводит в заблуждение"
    )

def test_unknown_status_is_blue():
    """Неизвестный статус → badge-blue (фоллбек)."""
    for s in ("UNKNOWN", "DRAFT", "IN_PROGRESS", ""):
        assert status_to_badge(s) == "badge-blue"
