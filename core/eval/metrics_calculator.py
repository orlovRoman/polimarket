from dataclasses import dataclass
from typing import Sequence, Optional

@dataclass
class SignalRecord:
    predicted_probability: float
    resolution_outcome: bool  # True = YES / Выиграло, False = NO / Проиграло
    edge_at_signal: float
    pnl_realized: Optional[float]

@dataclass  
class StrategyMetrics:
    total_signals: int
    resolved_signals: int
    profitable_signals: int
    win_rate: float
    avg_edge: float
    avg_realized_pnl: Optional[float]
    brier_score: float
    calibration_error: float  # Expected Calibration Error (ECE)

def calculate_brier_score(records: Sequence[SignalRecord]) -> float:
    """
    Brier Score = mean((predicted - outcome)^2)
    Чем ниже — тем лучше. 0.0 = идеально, 0.25 = случайно, 1.0 = всегда неверно.
    """
    if not records:
        return 0.0
        
    squared_errors = []
    for r in records:
        outcome_val = 1.0 if r.resolution_outcome else 0.0
        squared_errors.append((r.predicted_probability - outcome_val) ** 2)
        
    return sum(squared_errors) / len(records)

def calculate_ece(records: Sequence[SignalRecord], n_bins: int = 10) -> float:
    """
    Expected Calibration Error (ECE):
    Разбиваем на N бинов по предсказанной вероятности от 0.0 до 1.0.
    Сравниваем среднее предсказанное значение со средней фактической точностью в каждом бине.
    """
    if not records:
        return 0.0
        
    total_signals = len(records)
    ece = 0.0
    
    for i in range(n_bins):
        # Границы бина (например, для 10 бинов: [0.0, 0.1), [0.1, 0.2), ..., [0.9, 1.0])
        bin_lower = i / n_bins
        bin_upper = (i + 1) / n_bins
        
        # Находим сигналы, попадающие в данный бин
        bin_records = []
        for r in records:
            prob = r.predicted_probability
            # Для последнего бина верхняя граница включается (<= 1.0)
            if i == n_bins - 1:
                in_bin = bin_lower <= prob <= bin_upper
            else:
                in_bin = bin_lower <= prob < bin_upper
            
            if in_bin:
                bin_records.append(r)
                
        # Если бин пустой, пропускаем его
        if not bin_records:
            continue
            
        bin_size = len(bin_records)
        
        # Среднее предсказанное значение в бине
        avg_predicted = sum(r.predicted_probability for r in bin_records) / bin_size
        
        # Среднее фактическое значение (аккуратность) в бине
        avg_actual = sum(1.0 if r.resolution_outcome else 0.0 for r in bin_records) / bin_size
        
        # Разница между предсказанным и фактическим качеством
        bin_diff = abs(avg_predicted - avg_actual)
        
        # Взвешиваем разницу по доле сигналов в бине
        ece += (bin_size / total_signals) * bin_diff
        
    return ece

def calculate_metrics(records: Sequence[SignalRecord]) -> Optional[StrategyMetrics]:
    """
    Вычисляет агрегированные метрики для переданных сигналов.
    Возвращает StrategyMetrics или None, если нет записей.
    """
    if not records:
        return None
        
    resolved_records = [r for r in records] # все переданные записи должны быть resolved
    total_count = len(resolved_records)
    
    if total_count == 0:
        return None
        
    profitable_count = sum(1 for r in resolved_records if (r.pnl_realized is not None and r.pnl_realized > 0) or (r.pnl_realized is None and r.resolution_outcome))
    win_rate = profitable_count / total_count if total_count > 0 else 0.0
    
    avg_edge = sum(r.edge_at_signal for r in resolved_records) / total_count
    
    pnl_values = [r.pnl_realized for r in resolved_records if r.pnl_realized is not None]
    avg_pnl = sum(pnl_values) / len(pnl_values) if pnl_values else None
    
    brier = calculate_brier_score(resolved_records)
    ece = calculate_ece(resolved_records)
    
    return StrategyMetrics(
        total_signals=total_count,
        resolved_signals=total_count,
        profitable_signals=profitable_count,
        win_rate=round(win_rate, 4),
        avg_edge=round(avg_edge, 4),
        avg_realized_pnl=round(avg_pnl, 2) if avg_pnl is not None else None,
        brier_score=round(brier, 4),
        calibration_error=round(ece, 4)
    )
