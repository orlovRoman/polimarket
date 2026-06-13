import pytest
from unittest.mock import MagicMock, patch
from core.eval.outcome_tracker import OutcomeTracker
from core.eval.polymarket_resolution_client import MarketResolution

def _make_tracker(pending_signals, resolution_map):
    client = MagicMock()
    client.fetch_resolution.side_effect = lambda mid: resolution_map.get(mid)

    signal_logger = MagicMock()
    metrics_repo = MagicMock()
    calibrator = MagicMock()

    tracker = OutcomeTracker(
        resolution_client=client,
        signal_logger=signal_logger,
        metrics_repo=metrics_repo,
        calibrator=calibrator,
    )
    tracker._get_pending_expired = MagicMock(return_value=pending_signals)
    return tracker, signal_logger, metrics_repo, calibrator


def test_resolved_signal_triggers_log_and_metrics():
    from core.eval.outcome_tracker import PendingSignal
    from datetime import datetime, timezone

    pending = [PendingSignal("sig1", "0xABC", "temporal_corridor",
                              datetime(2025, 1, 1, tzinfo=timezone.utc))]
    resolution_map = {
        "0xABC": MarketResolution("0xABC", True, "YES", 1.0)
    }
    tracker, sig_logger, metrics_repo, calibrator = _make_tracker(pending, resolution_map)

    with patch("core.eval.outcome_tracker.DB_PATH", "fake_path"), \
         patch("core.eval.outcome_tracker.sqlite3.connect") as mock_conn:
        mock_conn.return_value.__enter__.return_value = mock_conn.return_value
        stats = tracker.run_cycle()

    assert stats["resolved"] == 1
    assert stats["errors"] == 0
    sig_logger.log_resolution.assert_called_once_with(
        signal_id="sig1", resolution_outcome="YES", resolution_price=1.0
    )
    metrics_repo.compute_and_store_metrics_sync.assert_called_once()
    calibrator.recalibrate.assert_called_once()


def test_unresolved_signal_is_skipped():
    from core.eval.outcome_tracker import PendingSignal
    from datetime import datetime, timezone

    pending = [PendingSignal("sig2", "0xDEF", "scout",
                              datetime(2025, 1, 1, tzinfo=timezone.utc))]
    resolution_map = {
        "0xDEF": MarketResolution("0xDEF", False, None, 0.0)
    }
    tracker, sig_logger, metrics_repo, calibrator = _make_tracker(pending, resolution_map)

    with patch("core.eval.outcome_tracker.DB_PATH", "fake_path"), \
         patch("core.eval.outcome_tracker.sqlite3.connect") as mock_conn:
        mock_conn.return_value.__enter__.return_value = mock_conn.return_value
        stats = tracker.run_cycle()

    assert stats["skipped"] == 1
    assert stats["resolved"] == 0
    sig_logger.log_resolution.assert_not_called()
    calibrator.recalibrate.assert_not_called()


def test_api_error_is_counted_not_raised():
    from core.eval.outcome_tracker import PendingSignal
    from datetime import datetime, timezone

    pending = [PendingSignal("sig3", "0xERR", "whale",
                              datetime(2025, 1, 1, tzinfo=timezone.utc))]
    tracker, sig_logger, *_ = _make_tracker(pending, {"0xERR": None})

    with patch("core.eval.outcome_tracker.DB_PATH", "fake_path"), \
         patch("core.eval.outcome_tracker.sqlite3.connect") as mock_conn:
        mock_conn.return_value.__enter__.return_value = mock_conn.return_value
        stats = tracker.run_cycle()

    assert stats["errors"] == 1
    sig_logger.log_resolution.assert_not_called()


def test_no_pending_no_calibration():
    tracker, _, _, calibrator = _make_tracker([], {})
    
    with patch("core.eval.outcome_tracker.DB_PATH", "fake_path"), \
         patch("core.eval.outcome_tracker.sqlite3.connect") as mock_conn:
        mock_conn.return_value.__enter__.return_value = mock_conn.return_value
        stats = tracker.run_cycle()
        
    assert stats["checked"] == 0
    calibrator.recalibrate.assert_not_called()


def test_calibration_cooldown_robust_parsing():
    import json
    from core.eval.outcome_tracker import PendingSignal
    from datetime import datetime, timezone, timedelta

    # Задаем пендинг сигналы, чтобы запустить блок калибровки
    pending = [PendingSignal("sig_test", "market_test", "scout",
                             datetime.now(timezone.utc))]
    resolution_map = {
        "market_test": MarketResolution("market_test", True, "YES", 1.0)
    }

    # Форматы записей в БД, которые мы тестируем:
    # a) Сырая ISO строка
    # b) Строка внутри JSON-а (которая парсится как строка)
    # c) Новая структура (JSON-словарь)
    test_cases = [
        # a) Сырая ISO строка
        ("2026-06-13T10:00:00+00:00", True), # Прошло много времени -> True
        (datetime.now(timezone.utc).isoformat(), False), # Только что было -> False
        
        # b) JSON строка
        (json.dumps("2026-06-13T10:00:00+00:00"), True),
        (json.dumps(datetime.now(timezone.utc).isoformat()), False),

        # c) JSON dict
        (json.dumps({"timestamp": "2026-06-13T10:00:00+00:00"}), True),
        (json.dumps({"timestamp": datetime.now(timezone.utc).isoformat()}), False),
    ]

    for db_value, expected_calibrate in test_cases:
        tracker, sig_logger, metrics_repo, calibrator = _make_tracker(pending, resolution_map)
        
        with patch("core.eval.outcome_tracker.DB_PATH", "fake_path"), \
             patch("core.eval.outcome_tracker.sqlite3.connect") as mock_conn:
            
            # Настраиваем мок соединения и курсора
            mock_db_conn = mock_conn.return_value.__enter__.return_value
            mock_cursor = mock_db_conn.cursor.return_value
            
            # Возвращаем наш db_value при SELECT
            mock_cursor.fetchone.return_value = (db_value,)
            
            stats = tracker.run_cycle()
            
            if expected_calibrate:
                calibrator.recalibrate.assert_called_once()
            else:
                calibrator.recalibrate.assert_not_called()
