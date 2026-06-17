# agents/shared/python/wallet/paper.py
from datetime import datetime, timezone
from .base import WalletProvider
from .models import BalanceInfo, CredentialsStatus

_PAPER_BALANCE = 1000.0  # виртуальный баланс для paper-режима

class PaperWalletProvider(WalletProvider):
    """
    Заглушка для paper/read-only режима.
    Не делает сетевых запросов, не требует ключей.
    Явно помечает все ответы is_mock=True.
    """

    def preflight_check(self) -> BalanceInfo:
        return BalanceInfo(
            usdc_balance=_PAPER_BALANCE,
            allowance_ok=True,
            wallet_address="0xPAPER_MOCK_ADDRESS",
            fetched_at=datetime.now(timezone.utc),
            is_mock=True,
            provider_mode="paper"
        )

    def get_credentials(self) -> CredentialsStatus:
        return CredentialsStatus(
            api_key="paper-api-key",
            passphrase="paper-passphrase",
            secret="paper-secret",
            derived_at=datetime.now(timezone.utc),
            is_mock=True,
            provider_mode="paper"
        )

    def is_live(self) -> bool:
        return False
