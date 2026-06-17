# agents/shared/python/wallet/base.py
from abc import ABC, abstractmethod
from .models import BalanceInfo, CredentialsStatus

class WalletProvider(ABC):
    """
    Абстракция над wallet/auth layer.
    Реализации: PaperWalletProvider, LivePolymarketProvider.
    """

    @abstractmethod
    def preflight_check(self) -> BalanceInfo:
        """
        Проверяет баланс и allowance перед исполнением.
        В paper-режиме возвращает мок с is_mock=True.
        В live-режиме делает реальный запрос к CLOB API.
        """
        pass

    @abstractmethod
    def get_credentials(self) -> CredentialsStatus:
        """
        Возвращает актуальные API credentials.
        В paper-режиме — мок creds.
        В live-режиме — derive/refresh через CLOB client.
        """
        pass

    @abstractmethod
    def is_live(self) -> bool:
        """Возвращает True только для LivePolymarketProvider."""
        pass
