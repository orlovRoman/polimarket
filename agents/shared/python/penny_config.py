from dataclasses import dataclass

@dataclass(frozen=True)
class PennyConfig:
    yes_fair_value: float = 0.10
    min_edge_to_log_signal: float = 0.0

    @property
    def no_fair_value(self) -> float:
        return 1.0 - self.yes_fair_value
        
    def __post_init__(self):
        assert self.min_edge_to_log_signal >= 0, "min_edge_to_log_signal должен быть >= 0 (отрицательный edge — убыток)"

PENNY_CONFIG = PennyConfig()
