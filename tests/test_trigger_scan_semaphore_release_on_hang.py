"""
Тест: Флаг сканирования (_scan_in_progress) гарантированно сбрасывается в False
даже в случае превышения таймаута сканирования (зависания run_team_discussion).
"""
import pytest
import asyncio
import time
from unittest.mock import MagicMock, patch
from services.telegram_listener import trigger_nexus_scan
import services.telegram_listener as listener_module

def test_semaphore_released_after_timeout():
    # Настраиваем фейковый CoreEngine, у которого run_team_discussion спит долго
    class SlowCoreEngine:
        def run_team_discussion(self, *args, **kwargs):
            time.sleep(10.0)  # Симулируем долгое выполнение
            
    # Сбрасываем флаг сканирования
    listener_module._scan_in_progress = False
    
    async def async_noop(*args, **kwargs):
        return None
        
    async def run_test_scenario():
        # Запускаем trigger_nexus_scan
        await trigger_nexus_scan("market_hang_123", amount_usd=1000.0, source="whale", timeout_sec=0.1)
        
        # Даем фоновой задаче _run_scan запуститься (один тик loop)
        await asyncio.sleep(0.02)
        
        # Теперь флаг должен быть True!
        assert listener_module._scan_in_progress is True
        
        # Ждем еще немного, чтобы сработал таймаут 0.1 сек
        await asyncio.sleep(0.2)
        
        # Флаг должен сброситься в False (блокировка снята)
        assert listener_module._scan_in_progress is False
        
    with patch("services.telegram_listener._get_core_engine", return_value=SlowCoreEngine()):
        with patch("services.telegram_listener.send_telegram_notify", side_effect=async_noop):
            asyncio.run(run_test_scenario())
