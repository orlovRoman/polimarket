import os
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional
from pydantic import BaseModel, ConfigDict

from core.eval.signal_logger import StrategyType
from core.eval.metrics_calculator import StrategyMetrics
from core.eval.threshold_calibrator import ThresholdCalibrator, CalibrationSuggestion
from core.eval.metrics_repository import MetricsRepository
from core.eval.calibration_store import CalibrationStore
import config

logger = logging.getLogger("NexusPolyBot.EvaluationEngine")

class EvalResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    metrics: Optional[StrategyMetrics] = None
    suggestions: List[CalibrationSuggestion] = []
    error: Optional[str] = None

class EvaluationReport(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    results: Dict[str, EvalResult]
    generated_at: datetime

class EvaluationEngine:
    """
    Главный оркестратор системы оценки:
    - Запускает расчет метрик для всех стратегий.
    - Вычисляет калибровочные предложения.
    - Применяет их (автоматически или отложено).
    - Отправляет уведомления в Telegram.
    """
    
    AUTO_APPLY_MIN_CONFIDENCE = 0.85
    AUTO_APPLY_MIN_SIGNALS = 100
    EVAL_PERIOD_DAYS = 30

    def __init__(self):
        self.metrics_repository = MetricsRepository()
        self.calibrator = ThresholdCalibrator()
        self.calibration_store = CalibrationStore()

    async def run_full_evaluation(self, period_days: Optional[int] = None) -> EvaluationReport:
        """
        Запускает полный цикл оценки по всем стратегиям.
        Возвращает структурированный отчет.
        """
        days = period_days or self.EVAL_PERIOD_DAYS
        results = {}
        
        for strategy in StrategyType:
            try:
                # 1. Сбор метрик
                metrics = await self.metrics_repository.compute_and_store_metrics(strategy, days)
                
                if metrics:
                    # 2. Получение истории тренда (последние 4 периода)
                    trend = await self.metrics_repository.get_metrics_trend(strategy, last_n_periods=4)
                    
                    # 3. Выработка рекомендаций
                    suggestions = await self._generate_suggestions(strategy, metrics, trend)
                    
                    # 4. Обработка предложений (автоприменение / отложенный режим)
                    await self._process_suggestions(strategy, suggestions)
                    
                    results[strategy.value] = EvalResult(metrics=metrics, suggestions=suggestions)
                else:
                    results[strategy.value] = EvalResult(metrics=None, suggestions=[])
            except Exception as e:
                logger.error(f"Ошибка при оценке стратегии {strategy.value}: {e}", exc_info=True)
                results[strategy.value] = EvalResult(error=str(e))
                
        report = EvaluationReport(
            results=results,
            generated_at=datetime.now(timezone.utc)
        )
        
        # 5. Уведомление в Telegram
        try:
            await self._notify_telegram(report)
        except Exception as e:
            logger.error(f"Не удалось отправить отчет об оценке в Telegram: {e}", exc_info=True)
            
        return report

    async def _generate_suggestions(
        self,
        strategy: StrategyType,
        metrics: StrategyMetrics,
        trend: List[StrategyMetrics]
    ) -> List[CalibrationSuggestion]:
        """
        Генерирует рекомендации по калибровке порогов на основе текущих метрик и тренда.
        """
        suggestions = []
        
        if strategy == StrategyType.SCOUT:
            current_edge = await self.calibration_store.get_latest_applied_value("min_edge", strategy.value)
            if current_edge is None:
                current_edge = getattr(config, "MIN_EDGE_DEFAULT", 0.05)
                
            sug = self.calibrator.suggest_edge_threshold(metrics, current_edge, trend)
            if sug:
                suggestions.append(sug)
                
        elif strategy in (StrategyType.SYNTHETIC_CORRIDOR, StrategyType.TEMPORAL_CORRIDOR, StrategyType.CROSS_PLATFORM):
            # Для коридоров и кросс-платформы параметр называется min_spread
            current_spread = await self.calibration_store.get_latest_applied_value("min_spread", strategy.value)
            if current_spread is None:
                # Значения по умолчанию
                if strategy == StrategyType.SYNTHETIC_CORRIDOR:
                    current_spread = 0.8
                elif strategy == StrategyType.TEMPORAL_CORRIDOR:
                    current_spread = 2.0
                else:
                    current_spread = 5.0
                    
            sug = self.calibrator.suggest_spread_threshold(metrics, current_spread, trend)
            if sug:
                suggestions.append(sug)
                
        elif strategy == StrategyType.WHALE:
            current_whale_tr = await self.calibration_store.get_latest_applied_value("whale_win_rate_threshold", strategy.value)
            if current_whale_tr is None:
                current_whale_tr = getattr(config, "WHALE_GATE_MIN_CONFIDENCE", 0.70)
                
            sug = self.calibrator.suggest_whale_win_rate_threshold(metrics, current_whale_tr, trend)
            if sug:
                suggestions.append(sug)
                
        return suggestions

    async def _process_suggestions(self, strategy_type: StrategyType, suggestions: List[CalibrationSuggestion]) -> None:
        """
        Сохраняет рекомендации в БД. Автоматически применяет их, если включен режим автоприменения
        и выполнены все жесткие критерии безопасности.
        """
        # Считываем глобальный переключатель автоприменения из .env
        env_auto_apply = os.getenv("EVAL_AUTO_APPLY_ENABLED", "False").lower() in ("true", "1", "yes")
        
        for suggestion in suggestions:
            # Проверяем жесткие критерии автоприменения:
            cond_confidence = suggestion.confidence >= self.AUTO_APPLY_MIN_CONFIDENCE
            cond_signals = suggestion.supporting_signals_count >= self.AUTO_APPLY_MIN_SIGNALS
            
            # Изменение не должно превышать 10% для автоматического принятия
            curr = suggestion.current_value
            sugg = suggestion.suggested_value
            change_ratio = abs(sugg - curr) / curr if curr > 0.0 else 0.0
            cond_change = change_ratio <= 0.10
            
            should_apply = env_auto_apply and cond_confidence and cond_signals and cond_change
            
            # Сохраняем предложение
            sug_id = await self.calibration_store.save_suggestion(
                suggestion=suggestion,
                strategy_type=strategy_type,
                auto_apply=should_apply
            )
            
            if should_apply:
                logger.info(f"Рекомендация #{sug_id} ({suggestion.param_name}) применена автоматически.")
            else:
                logger.info(f"Рекомендация #{sug_id} ({suggestion.param_name}) сохранена в ожидании ручного подтверждения.")

    async def _notify_telegram(self, report: EvaluationReport) -> None:
        """
        Форматирует отчет об оценке и отправляет его в Telegram.
        """
        try:
            # Динамический импорт форматировщика из Фазы 5
            from telegram.formatters.eval_formatter import format_eval_report
            from telegram.bot import bot, AUTHORIZED_CHAT_ID
            
            if not AUTHORIZED_CHAT_ID:
                logger.warning("TELEGRAM_CHAT_ID не настроен. Отправка отчета отменена.")
                return
                
            text = format_eval_report(report)
            
            # Лимит на длину сообщения в Telegram 4096 символов.
            # Если сообщение превышает этот лимит, разбиваем его на части.
            if len(text) > 4096:
                parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
                for part in parts:
                    await bot.send_message(
                        chat_id=AUTHORIZED_CHAT_ID,
                        text=part,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
            else:
                await bot.send_message(
                    chat_id=AUTHORIZED_CHAT_ID,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
        except ImportError:
            # Если форматтер еще не написан (мы в Фазе 4), просто логируем отчет
            logger.info("Форматтер Telegram-отчетов еще не доступен. Отчет в логе:")
            logger.info(report.model_dump_json(indent=2))
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения в Telegram: {e}", exc_info=True)
