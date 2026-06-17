# agents/shared/python/wallet/models.py
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class BalanceInfo:
    usdc_balance: float      # USDC на deposit wallet
    allowance_ok: bool       # Выдан ли approve для CLOB
    wallet_address: str
    fetched_at: datetime
    is_mock: bool = False    # True если это бумажный режим
    provider_mode: str = "paper" # "paper" | "live"

@dataclass(frozen=True)
class CredentialsStatus:
    api_key: str
    passphrase: str
    secret: str
    derived_at: datetime
    is_mock: bool = False
    provider_mode: str = "paper"
