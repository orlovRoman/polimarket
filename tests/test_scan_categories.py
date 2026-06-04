from agents.shared.scan_categories import SCAN_CATEGORIES
from telegram.bot import build_scan_keyboard

def test_scan_categories_has_required_fields():
    """Каждая категория в конфиге имеет label и tags."""
    for slug, cfg in SCAN_CATEGORIES.items():
        assert "label" in cfg, f"{slug}: нет label"
        assert "tags" in cfg,  f"{slug}: нет tags"
        assert len(cfg["tags"]) > 0, f"{slug}: пустой список tags"

def test_keyboard_matches_categories():
    """Клавиатура бота содержит кнопку для каждой категории из конфига."""
    kb = build_scan_keyboard()
    callbacks = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    for slug in SCAN_CATEGORIES:
        assert f"scan_{slug}" in callbacks, \
            f"scan_{slug} есть в SCAN_CATEGORIES, но нет в клавиатуре"

def test_no_orphan_keyboard_buttons():
    """В клавиатуре нет кнопок, которых нет в SCAN_CATEGORIES."""
    kb = build_scan_keyboard()
    known = {f"scan_{k}" for k in SCAN_CATEGORIES} | {"scan_all"}
    for row in kb.inline_keyboard:
        for btn in row:
            assert btn.callback_data in known, \
                f"Кнопка {btn.callback_data} есть в клавиатуре, но не в конфиге"

def test_polymarket_tag_map_uses_scan_categories():
    """polymarket.py берёт теги из SCAN_CATEGORIES, а не из собственного хардкода."""
    import inspect
    from agents.shared.adapters import polymarket
    src = inspect.getsource(polymarket)
    assert "POLYMARKET_TAG_MAP" not in src, \
        "POLYMARKET_TAG_MAP всё ещё хардкодится в polymarket.py — нужно использовать SCAN_CATEGORIES"
