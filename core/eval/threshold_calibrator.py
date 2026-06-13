from dataclasses import dataclass
from typing import Optional, Sequence
import logging
from core.eval.metrics_calculator import StrategyMetrics

logger = logging.getLogger("NexusPolyBot.ThresholdCalibrator")

@dataclass
class CalibrationSuggestion:
    param_name: str
    current_value: float
    suggested_value: float
    confidence: float  # 0.0 - 1.0
    reason: str
    supporting_signals_count: int  # сколько данных за рекомендацией

class ThresholdCalibrator:
    """
    Анализирует метрики эффективности стратегий и вырабатывает рекомендации
    по калибровке пороговых значений (min_edge, min_spread и т.д.).
    """
    
    MIN_SIGNALS_FOR_CALIBRATION = 50
    MIN_SIGNALS_EARLY_MODE = 15

    def _get_min_signals(self, strategy_type: str) -> int:
        """Снижает порог для новых стратегий (<90 дней)."""
        try:
            from agents.shared.python.db import get_strategy_first_signal_date
            from datetime import datetime, timezone
            first_signal = get_strategy_first_signal_date(strategy_type)
            if first_signal is None:
                return self.MIN_SIGNALS_EARLY_MODE
            age_days = (datetime.now(timezone.utc) - first_signal).days
            return self.MIN_SIGNALS_FOR_CALIBRATION if age_days >= 90 else self.MIN_SIGNALS_EARLY_MODE
        except Exception as e:
            logger.warning(f"Ошибка при вычислении возраста стратегии {strategy_type}: {e}. Используем early mode.")
            return self.MIN_SIGNALS_EARLY_MODE

    def _is_trending_down(self, trend: Sequence[StrategyMetrics]) -> bool:
        """
        Проверяет, ухудшаются ли метрики последние 3 периода подряд.
        Ухудшение = падение win_rate или рост brier_score.
        """
        if len(trend) < 3:
            return False
            
        # Берем последние 3 периода
        last_three = trend[-3:]
        
        # Проверяем последовательное падение win_rate
        wr_decline = (last_three[0].win_rate > last_three[1].win_rate > last_three[2].win_rate)
        # Проверяем последовательный рост brier_score
        brier_incline = (last_three[0].brier_score < last_three[1].brier_score < last_three[2].brier_score)
        
        return wr_decline or brier_incline

    def suggest_edge_threshold(
        self,
        metrics: StrategyMetrics,
        current_min_edge: float,
        trend: Optional[Sequence[StrategyMetrics]] = None,
        strategy_type: str = "scout"
    ) -> Optional[CalibrationSuggestion]:
        """
        Корректировка порога минимального преимущества (min_edge).
        Если win_rate < 45% -> повысить порог (более консервативно).
        Если brier_score > 0.20 -> модель не калибрована, не менять порог (шум).
        """
        min_signals = self._get_min_signals(strategy_type)
        if metrics.resolved_signals < min_signals:
            return None
            
        # Рост Brier score > 0.20 указывает на плохую калибровку модели, изменения опасны
        if metrics.brier_score > 0.20:
            return None

        trend_down = self._is_trending_down(trend) if trend else False
        
        # Ситуация 1: Низкий win_rate (< 45%) -> Повышаем порог
        if metrics.win_rate < 0.45:
            # Если тренд падает, делаем максимальное увеличение порога (+20%), иначе (+15%)
            change_pct = 0.20 if trend_down else 0.15
            suggested = current_min_edge * (1.0 + change_pct)
            # Ограничение изменения на ±20% за раз
            suggested = min(suggested, current_min_edge * 1.20)
            
            return CalibrationSuggestion(
                param_name="min_edge",
                current_value=current_min_edge,
                suggested_value=round(suggested, 4),
                confidence=0.90 if trend_down else 0.80,
                reason=f"Низкий win_rate ({metrics.win_rate:.1%}). Порог повышен на {change_pct:.0%} для фильтрации слабых сигналов.",
                supporting_signals_count=metrics.resolved_signals
            )
            
        # Ситуация 2: Отличный win_rate (> 65%) и низкий Brier score (< 0.15) -> Расширяем охват (снижаем порог)
        elif metrics.win_rate > 0.65 and metrics.brier_score < 0.15:
            # Если тренд плохой, никогда не снижаем порог!
            if trend_down or metrics.brier_score > 0.25:
                return None
                
            suggested = current_min_edge * 0.90  # снижаем на 10%
            suggested = max(suggested, current_min_edge * 0.80)  # не более чем на 20%
            
            return CalibrationSuggestion(
                param_name="min_edge",
                current_value=current_min_edge,
                suggested_value=round(suggested, 4),
                confidence=0.75,
                reason=f"Высокий win_rate ({metrics.win_rate:.1%}) и отличный Brier score ({metrics.brier_score:.3f}). Снижаем порог на 10% для увеличения охвата.",
                supporting_signals_count=metrics.resolved_signals
            )
            
        return None

    def suggest_spread_threshold(
        self,
        metrics: StrategyMetrics,
        current_min_spread: float,
        trend: Optional[Sequence[StrategyMetrics]] = None,
        strategy_type: str = "scout"
    ) -> Optional[CalibrationSuggestion]:
        """
        Корректировка порога спреда (min_spread) для арбитража/коридоров.
        """
        min_signals = self._get_min_signals(strategy_type)
        if metrics.resolved_signals < min_signals:
            return None
            
        if metrics.brier_score > 0.20:
            return None

        trend_down = self._is_trending_down(trend) if trend else False
        
        if metrics.win_rate < 0.45:
            change_pct = 0.20 if trend_down else 0.15
            suggested = current_min_spread * (1.0 + change_pct)
            suggested = min(suggested, current_min_spread * 1.20)
            
            return CalibrationSuggestion(
                param_name="min_spread",
                current_value=current_min_spread,
                suggested_value=round(suggested, 4),
                confidence=0.85,
                reason=f"Низкий win_rate коридора ({metrics.win_rate:.1%}). Повышаем требования к спреду на {change_pct:.0%}.",
                supporting_signals_count=metrics.resolved_signals
            )
            
        elif metrics.win_rate > 0.65 and metrics.brier_score < 0.15:
            if trend_down or metrics.brier_score > 0.25:
                return None
                
            suggested = current_min_spread * 0.90
            suggested = max(suggested, current_min_spread * 0.80)
            
            return CalibrationSuggestion(
                param_name="min_spread",
                current_value=current_min_spread,
                suggested_value=round(suggested, 4),
                confidence=0.70,
                reason=f"Высокая эффективность коридора ({metrics.win_rate:.1%}). Понижаем порог спреда на 10% для поиска большего числа вилок.",
                supporting_signals_count=metrics.resolved_signals
            )
            
        return None

    def suggest_whale_win_rate_threshold(
        self,
        metrics: StrategyMetrics,
        current_threshold: float,
        trend: Optional[Sequence[StrategyMetrics]] = None,
        strategy_type: str = "scout"
    ) -> Optional[CalibrationSuggestion]:
        """
        Корректировка порога копирования сделок китов (whale_win_rate).
        """
        min_signals = self._get_min_signals(strategy_type)
        if metrics.resolved_signals < min_signals:
            return None
            
        if metrics.brier_score > 0.20:
            return None

        trend_down = self._is_trending_down(trend) if trend else False
        
        if metrics.win_rate < 0.45:
            change_pct = 0.20 if trend_down else 0.15
            suggested = current_threshold * (1.0 + change_pct)
            suggested = min(suggested, current_threshold * 1.20)
            
            return CalibrationSuggestion(
                param_name="whale_win_rate_threshold",
                current_value=current_threshold,
                suggested_value=round(suggested, 4),
                confidence=0.80,
                reason=f"Низкий win_rate при следовании за китами ({metrics.win_rate:.1%}). Повышаем порог надежности китов на {change_pct:.0%}.",
                supporting_signals_count=metrics.resolved_signals
            )
            
        elif metrics.win_rate > 0.65 and metrics.brier_score < 0.15:
            if trend_down or metrics.brier_score > 0.25:
                return None
                
            suggested = current_threshold * 0.90
            suggested = max(suggested, current_threshold * 0.80)
            
            return CalibrationSuggestion(
                param_name="whale_win_rate_threshold",
                current_value=current_threshold,
                suggested_value=round(suggested, 4),
                confidence=0.70,
                reason=f"Высокая точность следования за китами ({metrics.win_rate:.1%}). Снижаем требования к win_rate китов на 10% для расширения базы.",
                supporting_signals_count=metrics.resolved_signals
            )
            
        return None

    def recalibrate(self) -> None:
        """
        Запускает цикл перекалибровки порогов для всех основных стратегий.
        Вызывается синхронно после пересчета метрик в OutcomeTracker.
        """
        from core.eval.metrics_repository import MetricsRepository
        from core.eval.calibration_store import CalibrationStore
        from core.config_provider import ConfigProvider
        from core.eval.signal_logger import StrategyType

        metrics_repo = MetricsRepository()
        store = CalibrationStore()

        strategies = [
            (
                StrategyType.SCOUT,
                "min_edge",
                lambda: ConfigProvider.get_min_edge_sync("scout"),
                lambda m, c, t: self.suggest_edge_threshold(m, c, t, "scout")
            ),
            (
                StrategyType.WHALE,
                "min_edge",
                lambda: ConfigProvider.get_min_edge_sync("whale"),
                lambda m, c, t: self.suggest_edge_threshold(m, c, t, "whale")
            ),
            (
                StrategyType.SYNTHETIC_CORRIDOR,
                "min_spread",
                lambda: ConfigProvider.get_min_spread_sync("synthetic_corridor"),
                lambda m, c, t: self.suggest_spread_threshold(m, c, t, "synthetic_corridor")
            ),
            (
                StrategyType.TEMPORAL_CORRIDOR,
                "min_spread",
                lambda: ConfigProvider.get_min_spread_sync("temporal_corridor"),
                lambda m, c, t: self.suggest_spread_threshold(m, c, t, "temporal_corridor")
            ),
            (
                StrategyType.WHALE,
                "whale_win_rate_threshold",
                lambda: ConfigProvider.get_whale_win_rate_threshold_sync(),
                lambda m, c, t: self.suggest_whale_win_rate_threshold(m, c, t, "whale")
            )
        ]

        for strategy_type, param_name, get_curr_val, suggest_method in strategies:
            try:
                # Получаем последние метрики
                metrics = metrics_repo.get_latest_metrics_sync(strategy_type)
                if not metrics:
                    continue

                # Получаем тренд
                trend = metrics_repo.get_metrics_trend_sync(strategy_type)
                current_value = get_curr_val()

                # Вычисляем предложение по калибровке
                suggestion = suggest_method(metrics, current_value, trend)
                if suggestion:
                    # Сохраняем предложение в БД и автоматически применяем его (auto_apply=True)
                    store.save_suggestion_sync(suggestion, strategy_type, auto_apply=True)
            except Exception as e:
                logger.error(f"Ошибка калибровки параметра {param_name} для {strategy_type.value}: {e}", exc_info=True)
