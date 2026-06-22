from dataclasses import dataclass

@dataclass(frozen=True)
class InfraConfig:
    backups_keep_days: int = 7
    backups_keep_max: int = 10
    cleanup_batch_size: int = 1000

INFRA_CONFIG = InfraConfig()

@dataclass(frozen=True)
class ReportConfig:
    daily_summary_window_days: int = 1

REPORT_CONFIG = ReportConfig()
