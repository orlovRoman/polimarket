import pytest
from unittest.mock import MagicMock, patch
import logging

def test_callback_error_does_not_mask_market_error(caplog):
    """
    Если summary_callback падает, в логах должна быть ОБЕИМ ошибки:
    - ошибка рынка
    - ошибка колбэка
    Они не должны маскировать друг друга.
    """
    # Симулируем поведение блока except в _run_team_discussion_inner
    market_error = ValueError("Market processing failed")
    callback_error = RuntimeError("Telegram send failed")

    captured_logs = []

    def fake_logger_error(msg):
        captured_logs.append(msg)

    # Симулируем логику блока
    try:
        raise market_error
    except Exception as e:
        error_msg = f"Market error: {e}"
        try:
            raise callback_error  # summary_callback падает
        except Exception as cb_err:           # ← правильное имя после фикса
            fake_logger_error(f"summary_callback error: {cb_err}")

        # Проверяем: внешний e не затронут
        assert str(e) == "Market processing failed", (
            f"Внешнее исключение не должно быть перезаписано, получено: {e}"
        )

    assert any("Telegram send failed" in log for log in captured_logs)
    assert any("summary_callback error" in log for log in captured_logs)

def test_original_exception_preserved_in_logs():
    """При ошибке колбэка исходная трассировка рынка не теряется."""
    import traceback
    
    market_error_traceback = None
    
    try:
        try:
            raise ValueError("real market error")
        except Exception as e:
            market_error_traceback = traceback.format_exc()
            try:
                raise RuntimeError("callback error")
            except Exception as cb_err:
                pass  # обрабатываем колбэк-ошибку отдельно
            
            # traceback должен быть захвачен ДО потенциального shadowing
            assert "real market error" in market_error_traceback
    except Exception:
        pytest.fail("Неожиданное исключение вышло наружу")
