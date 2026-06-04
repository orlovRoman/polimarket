"""
Тесты для команд оценки и калибровки торговых стратегий в Telegram-боте (Evaluation Engine).
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from telegram.bot import (
    command_eval_handler,
    command_eval_status_handler,
    command_eval_history_handler,
    command_eval_apply_handler,
    command_eval_rollback_handler
)
from core.eval.calibration_store import CalibrationRecord

@pytest.mark.anyio
async def test_command_eval_handler_success():
    mock_message = AsyncMock()
    mock_message.text = "/eval"
    
    with patch("core.eval.evaluation_engine.EvaluationEngine.run_full_evaluation", new_callable=AsyncMock) as mock_run:
        await command_eval_handler(mock_message)
        mock_run.assert_called_once()
        
    assert mock_message.answer.call_count == 2
    assert "Запуск оценки" in mock_message.answer.call_args_list[0][0][0]
    assert "успешно завершена" in mock_message.answer.call_args_list[1][0][0]

@pytest.mark.anyio
async def test_command_eval_handler_error():
    mock_message = AsyncMock()
    mock_message.text = "/eval"
    
    with patch("core.eval.evaluation_engine.EvaluationEngine.run_full_evaluation", side_effect=ValueError("Test Error")):
        await command_eval_handler(mock_message)
        
    assert mock_message.answer.call_count == 2
    assert "ошибка при запуске" in mock_message.answer.call_args_list[1][0][0]

@pytest.mark.anyio
async def test_command_eval_status_handler():
    mock_message = AsyncMock()
    
    mock_store = MagicMock()
    # Мокаем get_latest_applied_value, возвращая фиксированные значения в зависимости от параметров
    async def side_effect_val(param, strategy):
        vals = {
            ("min_edge", "scout"): 0.06,
            ("min_spread", "synthetic_corridor"): 0.010,
            ("min_spread", "temporal_corridor"): 0.025,
            ("min_spread", "cross_platform"): 0.045,
            ("whale_win_rate_threshold", "whale"): 0.75
        }
        return vals.get((param, strategy))
        
    mock_store.get_latest_applied_value = AsyncMock(side_effect=side_effect_val)
    
    with patch("core.eval.calibration_store.CalibrationStore", return_value=mock_store):
        await command_eval_status_handler(mock_message)
        
    sent_text = mock_message.answer.call_args[0][0]
    assert "Текущие торговые пороги систем:" in sent_text
    assert "SCOUT" in sent_text
    assert "6.0%" in sent_text
    assert "1.0%" in sent_text
    assert "2.5%" in sent_text
    assert "4.5%" in sent_text
    assert "75%" in sent_text

@pytest.mark.anyio
async def test_command_eval_history_handler():
    mock_message = AsyncMock()
    mock_message.text = "/eval_history"
    
    await command_eval_history_handler(mock_message)
    
    sent_text = mock_message.answer.call_args[0][0]
    assert "История калибровок" in sent_text
    assert "Выберите стратегию:" in sent_text

@pytest.mark.anyio
async def test_callback_eval_history_invalid_strategy():
    from telegram.bot import callback_eval_history_handler
    mock_callback = AsyncMock()
    mock_callback.data = "evalhist_unknown"
    
    await callback_eval_history_handler(mock_callback)
    
    sent_text = mock_callback.message.answer.call_args[0][0]
    assert "Неизвестная стратегия" in sent_text

@pytest.mark.anyio
async def test_callback_eval_history_success():
    from telegram.bot import callback_eval_history_handler
    mock_callback = AsyncMock()
    mock_callback.data = "evalhist_scout"
    
    mock_store = MagicMock()
    mock_store.get_strategy_history = AsyncMock(return_value=[
        CalibrationRecord(
            id=12,
            strategy_type="scout",
            param_name="min_edge",
            param_value=0.06,
            previous_value=0.05,
            reason="High win rate",
            auto_applied=True,
            updated_at="2026-06-03 12:00:00"
        )
    ])
    
    with patch("core.eval.calibration_store.CalibrationStore", return_value=mock_store):
        await callback_eval_history_handler(mock_callback)
        
    sent_text = mock_callback.message.answer.call_args[0][0]
    assert "История калибровок (SCOUT)" in sent_text
    assert "Предложение #12" in sent_text
    assert "High win rate" in sent_text
    assert "5.0% → <b>6.0%</b>" in sent_text

@pytest.mark.anyio
async def test_command_eval_apply_handler_success():
    mock_message = AsyncMock()
    mock_message.text = "/eval_apply 42"
    
    mock_store = MagicMock()
    mock_store.apply_suggestion = AsyncMock(return_value=True)
    
    with patch("core.eval.calibration_store.CalibrationStore", return_value=mock_store):
        await command_eval_apply_handler(mock_message)
        
    sent_text = mock_message.answer.call_args[0][0]
    assert "успешно применено" in sent_text

@pytest.mark.anyio
async def test_command_eval_apply_handler_fail():
    mock_message = AsyncMock()
    mock_message.text = "/eval_apply 42"
    
    mock_store = MagicMock()
    mock_store.apply_suggestion = AsyncMock(return_value=False)
    
    with patch("core.eval.calibration_store.CalibrationStore", return_value=mock_store):
        await command_eval_apply_handler(mock_message)
        
    sent_text = mock_message.answer.call_args[0][0]
    assert "Не удалось применить предложение" in sent_text

@pytest.mark.anyio
async def test_command_eval_rollback_handler_success():
    mock_message = AsyncMock()
    mock_message.text = "/eval_rollback 42"
    
    mock_store = MagicMock()
    mock_store.rollback = AsyncMock(return_value=True)
    
    with patch("core.eval.calibration_store.CalibrationStore", return_value=mock_store):
        await command_eval_rollback_handler(mock_message)
        
    sent_text = mock_message.answer.call_args[0][0]
    assert "Успешный откат изменения" in sent_text
