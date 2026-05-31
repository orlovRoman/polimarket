from dataclasses import dataclass


@dataclass
class ROIFilterResult:
    passes: bool
    roi_percent: float
    absolute_edge: float
    rejection_reason: str  # "" если passes=True


MIN_ROI_PERCENT = 40.0
MIN_ABSOLUTE_EDGE = 0.04   # минимум 4 цента профита на доллар
MAX_ENTRY_PRICE = 0.22     # не покупать если уже не дёшево


def apply_roi_filter(
    current_price: float,
    target_price: float,
    direction: str = "YES"  # "YES" или "NO"
) -> ROIFilterResult:
    """
    Проверяет математическую привлекательность сделки.
    direction="NO" означает шорт: покупаем NO по (1 - current_price).
    """
    entry = current_price if direction == "YES" else (1.0 - current_price)
    entry = max(entry, 0.001)  # защита от деления на ноль

    if entry > MAX_ENTRY_PRICE:
        return ROIFilterResult(
            passes=False,
            roi_percent=0.0,
            absolute_edge=0.0,
            rejection_reason=f"Цена входа {entry:.3f} > максимум {MAX_ENTRY_PRICE} для swing"
        )

    roi_percent = ((target_price - entry) / entry) * 100
    absolute_edge = target_price - entry

    if roi_percent < MIN_ROI_PERCENT:
        return ROIFilterResult(
            passes=False,
            roi_percent=round(roi_percent, 1),
            absolute_edge=round(absolute_edge, 4),
            rejection_reason=f"ROI {roi_percent:.1f}% < минимум {MIN_ROI_PERCENT}%"
        )

    if absolute_edge < MIN_ABSOLUTE_EDGE:
        return ROIFilterResult(
            passes=False,
            roi_percent=round(roi_percent, 1),
            absolute_edge=round(absolute_edge, 4),
            rejection_reason=f"Edge {absolute_edge:.4f} < минимум {MIN_ABSOLUTE_EDGE}"
        )

    return ROIFilterResult(
        passes=True,
        roi_percent=round(roi_percent, 1),
        absolute_edge=round(absolute_edge, 4),
        rejection_reason=""
    )
