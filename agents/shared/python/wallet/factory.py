# agents/shared/python/wallet/factory.py
import logging
import os
from .base import WalletProvider
from .paper import PaperWalletProvider

logger = logging.getLogger("NexusPolyBot.Wallet")

_provider: WalletProvider | None = None

def get_wallet_provider() -> WalletProvider:
    """
    Синглтон. Возвращает нужную реализацию по APP_MODE.
    При APP_MODE=live проверяет наличие обязательных env-переменных
    и выбрасывает ValueError при старте.
    """
    global _provider
    if _provider is not None:
        return _provider

    # Загружаем config для чтения APP_MODE
    try:
        import config
        app_mode = getattr(config, "APP_MODE", "paper").lower()
    except ImportError:
        app_mode = os.getenv("APP_MODE", "paper").lower()

    if app_mode == "live":
        _validate_live_env()
        # Лениво импортируем, чтобы не грузить модули, если live не используется
        from .live import LivePolymarketProvider
        _provider = LivePolymarketProvider()
        logger.warning("🔴 LIVE-режим: включена реальная интеграция с Polymarket!")
    else:
        _provider = PaperWalletProvider()
        logger.info("📄 Paper-режим: провайдер кошелька работает в режиме симуляции (mock)")

    return _provider

def _validate_live_env() -> None:
    """Проверяет наличие секретов для live-режима."""
    required = ["PRIVATE_KEY", "DEPOSIT_WALLET_ADDRESS"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise ValueError(
            f"Инфраструктурный режим APP_MODE=live требует наличия секретов в окружении: {missing}."
        )
