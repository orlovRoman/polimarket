from dataclasses import dataclass

@dataclass(frozen=True)
class WhaleDashboardConfig:
    pnl_window_days_short: int = 7
    pnl_window_days_long: int = 30
    default_active_limit: int = 100
    default_wins_limit: int = 50
    default_losses_limit: int = 50
    default_whales_limit: int = 10

WHALE_DASHBOARD_CONFIG = WhaleDashboardConfig()
