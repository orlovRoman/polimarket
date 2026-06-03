import pytest
from core.eval.metrics_calculator import StrategyMetrics
from core.eval.threshold_calibrator import ThresholdCalibrator

def test_calibrator_ignores_under_min_signals():
    calibrator = ThresholdCalibrator()
    metrics = StrategyMetrics(
        total_signals=40,
        resolved_signals=40,
        profitable_signals=10,
        win_rate=0.25,
        avg_edge=0.05,
        avg_realized_pnl=-2.0,
        brier_score=0.15,
        calibration_error=0.05
    )
    # Сигналов 40 < 50 -> предложений быть не должно
    assert calibrator.suggest_edge_threshold(metrics, 0.05) is None

def test_calibrator_ignores_high_brier():
    calibrator = ThresholdCalibrator()
    metrics = StrategyMetrics(
        total_signals=60,
        resolved_signals=60,
        profitable_signals=15,
        win_rate=0.25,
        avg_edge=0.05,
        avg_realized_pnl=-2.0,
        brier_score=0.22,  # > 0.20
        calibration_error=0.05
    )
    # Brier Score > 0.20 -> не калиброванная модель, отмена изменений
    assert calibrator.suggest_edge_threshold(metrics, 0.05) is None

def test_calibrator_increases_on_poor_metrics():
    calibrator = ThresholdCalibrator()
    metrics = StrategyMetrics(
        total_signals=100,
        resolved_signals=100,
        profitable_signals=30,
        win_rate=0.30,  # < 0.45
        avg_edge=0.05,
        avg_realized_pnl=-5.0,
        brier_score=0.18,
        calibration_error=0.05
    )
    suggestion = calibrator.suggest_edge_threshold(metrics, 0.050)
    assert suggestion is not None
    assert suggestion.param_name == "min_edge"
    assert suggestion.suggested_value > 0.050
    # Изменение не должно превышать +20% (0.050 * 1.2 = 0.060)
    assert suggestion.suggested_value <= 0.060
    assert "Порог повышен" in suggestion.reason

def test_calibrator_decreases_on_excellent_metrics():
    calibrator = ThresholdCalibrator()
    metrics = StrategyMetrics(
        total_signals=100,
        resolved_signals=100,
        profitable_signals=75,
        win_rate=0.75,  # > 0.65
        avg_edge=0.08,
        avg_realized_pnl=5.0,
        brier_score=0.10,  # < 0.15
        calibration_error=0.02
    )
    suggestion = calibrator.suggest_edge_threshold(metrics, 0.050)
    assert suggestion is not None
    assert suggestion.suggested_value < 0.050
    # Изменение не должно быть ниже -20% (0.050 * 0.8 = 0.040)
    assert suggestion.suggested_value >= 0.040
    assert "Снижаем порог" in suggestion.reason

def test_calibrator_worsening_trend_safety():
    calibrator = ThresholdCalibrator()
    
    # 3 ухудшающихся периода: win_rate падает
    trend = [
        StrategyMetrics(100, 100, 60, 0.60, 0.05, 0.0, 0.15, 0.05),
        StrategyMetrics(100, 100, 50, 0.50, 0.05, 0.0, 0.16, 0.05),
        StrategyMetrics(100, 100, 30, 0.30, 0.05, -5.0, 0.18, 0.05)
    ]
    
    # Сравниваем поведение без тренда и с трендом
    metrics_now = trend[-1]
    
    # Без тренда (повышение на 15%)
    sug_no_trend = calibrator.suggest_edge_threshold(metrics_now, 0.050, trend=None)
    # С ухудшающимся трендом (повышение на 20%)
    sug_with_trend = calibrator.suggest_edge_threshold(metrics_now, 0.050, trend=trend)
    
    assert sug_no_trend is not None
    assert sug_with_trend is not None
    
    # С ухудшающимся трендом порог должен стать еще жестче (выше)
    assert sug_with_trend.suggested_value > sug_no_trend.suggested_value
    assert sug_with_trend.suggested_value == 0.060  # 0.05 * 1.2
    
    # С ухудшающимся трендом мы никогда не должны снижать порог, даже если текущий период хороший
    good_metrics = StrategyMetrics(100, 100, 75, 0.75, 0.05, 5.0, 0.10, 0.02)
    sug_good_trend = calibrator.suggest_edge_threshold(good_metrics, 0.050, trend=trend)
    assert sug_good_trend is None
