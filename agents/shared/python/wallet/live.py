# agents/shared/python/wallet/live.py
import os
from datetime import datetime, timezone
from .base import WalletProvider
from .models import BalanceInfo, CredentialsStatus
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams

_cached_credentials = None

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
        global _cached_credentials
        self._credentials = _cached_credentials

    def _get_client(self):
        """Ленивая инициализация CLOB-клиента."""
        if self._client is None:
            if not self._private_key or not self._deposit_wallet:
                raise ValueError("PRIVATE_KEY и DEPOSIT_WALLET_ADDRESS должны быть заданы в окружении для LIVE-режима.")

            # Деривируем ключи только если не кэшированы
            if self._credentials is None:
                temp_client = ClobClient(
                    self._clob_host,
                    key=self._private_key,
                    chain_id=137,
                )
                self._credentials = temp_client.create_or_derive_api_key()
                global _cached_credentials
                _cached_credentials = self._credentials
            
            # Полноценный клиент с deposit wallet
            self._client = ClobClient(
                self._clob_host,
                key=self._private_key,
                chain_id=137,
                creds=self._credentials,
                signature_type=1,  # 1 соответствует POLY_1271 (POLY_PROXY)
                funder=self._deposit_wallet,
            )
        return self._client

    def preflight_check(self) -> BalanceInfo:
        client = self._get_client()
        params = BalanceAllowanceParams(asset_type="USDC")
        resp = client.get_balance_allowance(params=params)
        
        balance = float(resp.get("balance", 0.0))
        allowance = float(resp.get("allowance", 0.0))
        
        return BalanceInfo(
            usdc_balance=balance,
            allowance_ok=allowance > 0.0,
            wallet_address=self._deposit_wallet,
            fetched_at=datetime.now(timezone.utc),
            is_mock=False,
            provider_mode="live"
        )

    def get_credentials(self) -> CredentialsStatus:
        if self._credentials is None:
            self._get_client()  # Деривируем в процессе инициализации
            
        return CredentialsStatus(
            api_key=self._credentials.get("apiKey", ""),
            passphrase=self._credentials.get("passphrase", ""),
            secret=self._credentials.get("secret", ""),
            derived_at=datetime.now(timezone.utc),
            is_mock=False,
            provider_mode="live"
        )

    def is_live(self) -> bool:
        return True

