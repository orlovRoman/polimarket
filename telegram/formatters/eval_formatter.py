from datetime import datetime
from core.eval.signal_logger import StrategyType
from core.eval.evaluation_engine import EvaluationReport, EvalResult

def format_eval_report(report: EvaluationReport) -> str:
    """
    Форматирует объект EvaluationReport в красивое HTML-сообщение для Telegram.
    """
    date_str = report.generated_at.strftime("%Y-%m-%d")
    msg = f"📊 <b>ОТЧЕТ ОБ ОЦЕНКЕ СИСТЕМЫ — {date_str}</b>\n\n"
    
    # Маппинг названий стратегий для вывода
    strategy_names = {
        StrategyType.SCOUT.value: "🕵️ SCOUT (LLM)",
        StrategyType.SYNTHETIC_CORRIDOR.value: "🔬 SYNTHETIC CORRIDOR",
        StrategyType.TEMPORAL_CORRIDOR.value: "⏳ TEMPORAL CORRIDOR",
        StrategyType.CROSS_PLATFORM.value: "🔄 CROSS PLATFORM",
        StrategyType.WHALE.value: "🐋 WHALE FOLLOWING"
    }

    for strategy_val, res in report.results.items():
        name = strategy_names.get(strategy_val, strategy_val.upper())
        msg += f"━━━ <b>{name}</b> ━━━\n"
        
        if res.error:
            msg += f"❌ Ошибка оценки: {res.error}\n\n"
            continue
            
        metrics = res.metrics
        if not metrics:
            msg += "⚠️ Недостаточно данных для расчета метрик за период.\n\n"
            continue
            
        # Индикаторы для win_rate
        wr = metrics.win_rate
        if wr >= 0.60:
            wr_emoji = "🟢"
        elif wr < 0.45:
            wr_emoji = "🔴"
        else:
            wr_emoji = "🟡"
            
        # Индикаторы для brier_score
        brier = metrics.brier_score
        if brier <= 0.20:
            brier_emoji = "🟢"
        elif brier > 0.25:
            brier_emoji = "🔴"
        else:
            brier_emoji = "🟡"

        msg += f"Сигналов за 30д:     <b>{metrics.total_signals}</b> (из них resolved: {metrics.resolved_signals})\n"
        msg += f"Win Rate:             <b>{wr:.0%}</b> {wr_emoji}\n"
        msg += f"Brier Score:          <b>{brier:.3f}</b> {brier_emoji} (&lt; 0.25 = хорошо)\n"
        
        avg_edge_val = metrics.avg_edge
        edge_sign = "+" if avg_edge_val >= 0 else ""
        msg += f"Avg Edge:             <b>{edge_sign}{avg_edge_val:.1%}</b>\n"
        
        if metrics.avg_realized_pnl is not None:
            pnl_sign = "+" if metrics.avg_realized_pnl >= 0 else ""
            msg += f"Avg PnL:              <b>{pnl_sign}${metrics.avg_realized_pnl:.2f}</b> / сигнал\n"
            
        msg += "\n"
        
        # Калибровочные предложения
        if res.suggestions:
            msg += "🔧 <b>Калибровка:</b>\n"
            for sug in res.suggestions:
                # В зависимости от параметра выводим в процентах или просто флоат
                is_pct = sug.param_name in ("min_edge", "min_spread")
                if is_pct:
                    curr_str = f"{sug.current_value:.1%}"
                    sugg_str = f"{sug.suggested_value:.1%}"
                else:
                    curr_str = f"{sug.current_value:.2f}"
                    sugg_str = f"{sug.suggested_value:.2f}"
                    
                msg += f"  • {sug.param_name}: <code>{curr_str}</code> → <b>{sugg_str}</b> (уверенность: {sug.confidence:.0%})\n"
                
                # Показываем статус предложения
                # Мы не знаем точный ID предложения здесь, но можем условно написать:
                # Если автоприменение прошло, то пишем auto-applied
                # Мы можем передать в метаданных или предположить по уверенности и сигналам:
                # Но лучше, если в предложении будет указано, применено ли оно.
                # Так как calibrator просто возвращает Suggestion, а обработчик сохраняет его.
                # Если suggestion имеет confidence >= 0.85, сигналов >= 100 и изменение <= 10%:
                import os
                env_auto_apply = os.getenv("EVAL_AUTO_APPLY_ENABLED", "False").lower() in ("true", "1", "yes")
                curr = sug.current_value
                sugg = sug.suggested_value
                change_ratio = abs(sugg - curr) / curr if curr > 0.0 else 0.0
                
                is_auto = env_auto_apply and sug.confidence >= 0.85 and sug.supporting_signals_count >= 100 and change_ratio <= 0.10
                if is_auto:
                    msg += "    [auto-applied ✅]\n"
                else:
                    msg += f"    [ожидает подтверждения — <code>/eval_apply</code>]\n"
        else:
            if metrics.total_signals < 50:
                msg += "⚠️ Недостаточно данных для калибровки (мин. 50)\n"
            else:
                msg += "✅ Калибровка не требуется (модель стабильна)\n"
                
        msg += "\n"
        
    return msg.strip()
