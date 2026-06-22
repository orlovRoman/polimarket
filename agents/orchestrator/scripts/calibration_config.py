from dataclasses import dataclass

@dataclass(frozen=True)
class CalibrationConfig:
    default_window_days: int = 7
    min_markets_for_calibration: int = 5
    brier_bad_threshold: float = 0.20
    brier_warn_threshold: float = 0.15
    top_reasons_limit: int = 10
    report_recent_days: int = 1

CALIB_CONFIG = CalibrationConfig()
