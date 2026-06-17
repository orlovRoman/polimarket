# agents/shared/python/wallet/live.py
import os
from datetime import datetime, timezone
from .base import WalletProvider
from .models import BalanceInfo, CredentialsStatus

class LivePolymarketProvider(WalletProvider):
    """
    Реальная интеграция с Polymarket CLOB API.
    Требует PRIVATE_KEY и DEPOSIT_WALLET_ADDRESS в окружении.
    НЕ подключается при APP_MODE=paper — factory.py не создаёт этот класс.
    """

    def __init__(self):
        # Читаем ключи
        self._private_key = os.environ.get("PRIVATE_KEY", "")
        self._deposit_wallet = os.environ.get("DEPOSIT_WALLET_ADDRESS", "")
        self._clob_host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
        self._client = None  # Инициализируется лениво при первом вызове
        self._credentials = None

    def _get_client(self):
        """Ленивая инициализация CLOB-клиента."""
        if self._client is None:
            # При активации реальной live торговли раскомментировать:
            # from py_clob_client_v2 import ClobClient, SignatureTypeV2
            # self._client = ClobClient(
            #     self._clob_host,
            #     key=self._private_key,
            #     chain_id=137,
            #     signature_type=SignatureTypeV2.POLY_1271,
            #     funder=self._deposit_wallet,
            # )
            raise NotImplementedError(
                "LivePolymarketProvider не активирован. "
                "Раскомментируй инициализацию CLOB-клиента в live.py."
            )
        return self._client

    def preflight_check(self) -> BalanceInfo:
        # TODO при переходе на боевой режим:
        # client = self._get_client()
        # balance_resp = client.get_balance_allowance(...)
        # return BalanceInfo(
        #     usdc_balance=float(balance_resp["balance"]),
        #     allowance_ok=float(balance_resp["allowance"]) > 0,
        #     wallet_address=self._deposit_wallet,
        #     fetched_at=datetime.now(timezone.utc),
        #     is_mock=False,
        #     provider_mode="live"
        # )
        raise NotImplementedError("Реальный preflight check в LivePolymarketProvider не реализован.")

    def get_credentials(self) -> CredentialsStatus:
        # TODO при переходе на боевой режим:
        # from py_clob_client_v2 import ClobClient
        # temp = ClobClient(self._clob_host, key=self._private_key, chain_id=137)
        # creds = temp.create_or_derive_api_key()
        # return CredentialsStatus(
        #     api_key=creds["apiKey"],
        #     passphrase=creds["passphrase"],
        #     secret=creds["secret"],
        #     derived_at=datetime.now(timezone.utc),
        #     is_mock=False,
        #     provider_mode="live"
        # )
        raise NotImplementedError("Реальный дериватор API-ключей в LivePolymarketProvider не реализован.")

    def is_live(self) -> bool:
        return True
