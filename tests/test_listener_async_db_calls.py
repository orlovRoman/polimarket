import inspect
import re
import pytest

def test_save_trader_transaction_is_awaited():
    """save_trader_transaction должен вызываться через asyncio.to_thread."""
    from services import telegram_listener as tl
    source = inspect.getsource(tl)
    # Ищем паттерн: asyncio.to_thread с save_trader_transaction
    assert re.search(r'asyncio\.to_thread\s*\(\s*save_trader_transaction', source), (
        "save_trader_transaction вызывается синхронно в async handler — "
        "заблокирует event loop Telethon. Оберни в asyncio.to_thread()"
    )

def test_save_telegram_post_is_awaited():
    """save_telegram_post должен вызываться через asyncio.to_thread."""
    from services import telegram_listener as tl
    source = inspect.getsource(tl)
    assert re.search(r'asyncio\.to_thread\s*\(\s*save_telegram_post', source), (
        "save_telegram_post вызывается синхронно в async handler — "
        "заблокирует event loop Telethon. Оберни в asyncio.to_thread()"
    )
