from dataclasses import dataclass

@dataclass(frozen=True)
class InfraConfig:
    backups_keep_days: int = 7
    backups_keep_max: int = 10
    cleanup_batch_size: int = 1000

INFRA_CONFIG = InfraConfig()
