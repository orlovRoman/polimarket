from dataclasses import dataclass

@dataclass(frozen=True)
class UiConfig:
    default_page: int = 1
    default_limit: int = 50
    max_limit: int = 200
    whale_chart_height_px: int = 300

UI_CONFIG = UiConfig()
