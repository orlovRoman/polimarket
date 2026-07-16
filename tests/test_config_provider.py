"""
Тесты для провайдера динамических конфигураций ConfigProvider (синхронные и асинхронные интерфейсы).
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from core.config_provider import ConfigProvider

@pytest.mark.anyio
async def test_config_provider_cache_and_invalidation():
    # Сбрасываем кэш перед тестом
    ConfigProvider.invalidate_cache()
    
    mock_store = MagicMock()
    # Возвращаем None первый раз, затем 0.08
    mock_store.get_latest_applied_value_sync = MagicMock(side_effect=[None, 0.08, 0.08])
    
    with patch("core.config_provider.ConfigProvider._get_store", return_value=mock_store):
        # 1. Первый запрос (БД возвращает None -> берем дефолт 0.05)
        val = ConfigProvider.get_min_edge_sync("scout")
        assert val == 0.05
        
        # 2. Второй запрос (должен сработать кэш, вызов БД не происходит)
        val = ConfigProvider.get_min_edge_sync("scout")
        assert val == 0.05
        
        # 3. Инвалидируем кэш
        ConfigProvider.invalidate_cache()
        
        # 4. Третий запрос (должен пойти в БД и вернуть 0.08)
        val = ConfigProvider.get_min_edge_sync("scout")
        assert val == 0.08
        
        # 5. Проверка асинхронной версии (должна вернуть кэшированное 0.08)
        val_async = ConfigProvider.get_min_edge("scout")
        assert val_async == 0.08

        # Проверка min_spread для удаленных стратегий убрана
        mock_store.get_latest_applied_value_sync = MagicMock(return_value=None)
        
        # Проверка whale_win_rate_threshold
        val_whale = ConfigProvider.get_whale_win_rate_threshold_sync()
        assert val_whale == 0.5
