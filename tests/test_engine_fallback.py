import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from core.engine import CoreEngine
from core.models import Market
from datetime import datetime, timezone

@pytest.mark.anyio
async def test_engine_fallback_math_gate_sync():
    """
    Проверяет, что _run_math_gate_sync отрабатывает корректно через fallback-ветку
    (в отдельном потоке), если вызывается из уже запущенного event loop.
    """
    engine = CoreEngine()
    
    # Создаем фиктивный список рынков
    market_a = Market(
        id="mkt_a",
        platform="polymarket",
        title="Event Market A",
        description="Description A",
        url="https://polymarket.com/market/a",
        outcome="YES",
        price=0.45,
        close_time=datetime.now(timezone.utc)
    )
    
    # Патчим _run_math_gate, чтобы он возвращал фиктивный список обработанных ID
    with patch("core.engine._run_math_gate", new_callable=AsyncMock) as mock_gate:
        mock_gate.return_value = ["mkt_a"]
        
        # Поскольку этот тест выполняется в асинхронном контексте (pytest running loop),
        # вызов синхронного _run_math_gate_sync гарантированно вызовет RuntimeError
        # ("cannot be called from a running event loop") и уйдет в fallback поток.
        processed_ids = engine._run_math_gate_sync([market_a], summary_callback=MagicMock())
        
        # Проверяем, что _run_math_gate был вызван 2 раза (один раз в основном loop, который упал, и один раз в fallback потоке)
        assert mock_gate.call_count == 2
        
        # Проверяем результат
        assert processed_ids == ["mkt_a"]
