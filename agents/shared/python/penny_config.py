from dataclasses import dataclass

@dataclass(frozen=True)
class PennyConfig:
    yes_fair_value: float = 0.10
    no_fair_value: float = 0.90  # 1.0 - yes_fair_value
    min_edge_to_log_signal: float = 0.0

PENNY_CONFIG = PennyConfig()
