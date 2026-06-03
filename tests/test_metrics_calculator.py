import pytest
from core.eval.metrics_calculator import (
    SignalRecord, calculate_brier_score, calculate_ece, calculate_metrics
)

def test_brier_score_known_values():
    # predict=1.0, outcome=True -> score=0.0
    rec1 = [SignalRecord(predicted_probability=1.0, resolution_outcome=True, edge_at_signal=0.5, pnl_realized=10.0)]
    assert calculate_brier_score(rec1) == 0.0
    
    # predict=0.5, outcome=True -> score=0.25
    rec2 = [SignalRecord(predicted_probability=0.5, resolution_outcome=True, edge_at_signal=0.0, pnl_realized=5.0)]
    assert calculate_brier_score(rec2) == 0.25
    
    # predict=0.0, outcome=True -> score=1.0
    rec3 = [SignalRecord(predicted_probability=0.0, resolution_outcome=True, edge_at_signal=-0.5, pnl_realized=-10.0)]
    assert calculate_brier_score(rec3) == 1.0

def test_brier_score_empty_sequence():
    assert calculate_brier_score([]) is None

def test_ece_empty_sequence():
    assert calculate_ece([]) is None

def test_ece_perfect_calibration():
    # 5 сигналов с предсказанием 0.8, все 4 из 5 (80%) выиграли. Точность = 80%. Разница = 0.
    records = [
        SignalRecord(predicted_probability=0.8, resolution_outcome=True, edge_at_signal=0.3, pnl_realized=10.0),
        SignalRecord(predicted_probability=0.8, resolution_outcome=True, edge_at_signal=0.3, pnl_realized=10.0),
        SignalRecord(predicted_probability=0.8, resolution_outcome=True, edge_at_signal=0.3, pnl_realized=10.0),
        SignalRecord(predicted_probability=0.8, resolution_outcome=True, edge_at_signal=0.3, pnl_realized=10.0),
        SignalRecord(predicted_probability=0.8, resolution_outcome=False, edge_at_signal=0.3, pnl_realized=-10.0),
    ]
    # Все попадают в бин [0.8, 0.9). Среднее предсказанное = 0.8. Среднее фактическое = 0.8.
    assert calculate_ece(records, n_bins=10) == 0.0

def test_ece_poor_calibration():
    # Все предсказания 0.9, но все проиграли (точность 0%). Разница = 0.9.
    records = [
        SignalRecord(predicted_probability=0.9, resolution_outcome=False, edge_at_signal=0.4, pnl_realized=-10.0),
        SignalRecord(predicted_probability=0.9, resolution_outcome=False, edge_at_signal=0.4, pnl_realized=-10.0),
    ]
    assert calculate_ece(records, n_bins=10) == pytest.approx(0.9)

def test_calculate_metrics_all_correct():
    records = [
        SignalRecord(predicted_probability=0.9, resolution_outcome=True, edge_at_signal=0.4, pnl_realized=10.0),
        SignalRecord(predicted_probability=0.8, resolution_outcome=True, edge_at_signal=0.3, pnl_realized=15.0),
    ]
    metrics = calculate_metrics(records)
    assert metrics is not None
    assert metrics.total_signals == 2
    assert metrics.profitable_signals == 2
    assert metrics.win_rate == 1.0
    assert metrics.avg_realized_pnl == 12.50
    assert metrics.brier_score == pytest.approx(0.025)  # mean((0.1)^2, (0.2)^2) = (0.01 + 0.04) / 2 = 0.025
    assert metrics.sharpe_ratio == pytest.approx(5.0)

def test_calculate_metrics_empty():
    assert calculate_metrics([]) is None
