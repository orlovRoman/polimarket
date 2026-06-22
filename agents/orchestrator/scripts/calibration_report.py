import json
from datetime import datetime, timezone
from agents.orchestrator.scripts.calibration_config import CALIB_CONFIG

def generate_calibration_report(metrics: dict) -> str:
    """Формирует текстовый отчёт на основе JSON с метриками."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    window_days = metrics.get('window_days', 7)
    
    report = []
    report.append(f"# Отчет калибровки системы (за {window_days} дней)")
    report.append(f"Дата формирования: {now_str}\n")
    
    # 1. Brier Score
    brier = metrics.get('brier_score', {})
    score = brier.get('brier_score')
    samples = brier.get('samples', 0)
    report.append("## 1. Точность вероятностей (Brier Score)")
    if score is not None:
        report.append(f"- **Brier Score (SCOUT):** {score} (по {samples} завершенным событиям)")
        if score > CALIB_CONFIG.brier_bad_threshold:
            report.append("- ⚠️ Оценка: Плохая калибровка (агент слишком самоуверен или ошибается).")
        elif score > CALIB_CONFIG.brier_warn_threshold:
            report.append("- 🟡 Оценка: Удовлетворительно, но можно улучшить.")
        else:
            report.append("- 🟢 Оценка: Отличная калибровка.")
    else:
        report.append("- Нет данных для расчета Brier Score за этот период.")
    report.append("")

    # 2. Win Rate
    report.append("## 2. Win Rate по стратегиям")
    wr_stats = metrics.get('win_rate', {})
    if wr_stats:
        for stype, data in wr_stats.items():
            report.append(f"- **{stype.upper()}**: {data.get('win_rate')}% ({data.get('wins')}/{data.get('total')} побед)")
    else:
        report.append("- Нет завершенных сделок.")
    report.append("")

    # 3. PnL
    report.append("## 3. PnL по стратегиям")
    pnl = metrics.get('pnl', {})
    if pnl:
        for stype, val in pnl.items():
            sign = "+" if val > 0 else ""
            report.append(f"- **{stype.upper()}**: {sign}{val} USD")
    else:
        report.append("- Нет данных по PnL.")
    report.append("")

    # 4. Воронка отказов
    report.append("## 4. Воронка генерации сигналов")
    funnel = metrics.get('funnel', {})
    total_analyzed = funnel.get('total_analyzed', 0)
    report.append(f"Всего проанализировано рынков: {total_analyzed}")
    breakdown = funnel.get('breakdown', {})
    if breakdown:
        for outcome, count in breakdown.items():
            report.append(f"- {outcome}: {count}")
    report.append("")

    # 5. Частые причины отказов SHADOW
    report.append("## 5. Топ причин отказов от SHADOW")
    shadow = metrics.get('shadow_rejections', [])
    if shadow:
        for i, item in enumerate(shadow, 1):
            reason = str(item.get('reason')).replace('\n', ' ')
            report.append(f"{i}. {reason} (Кол-во: {item.get('count')})")
    else:
        report.append("- Нет отказов от SHADOW или нет данных.")
    report.append("")

    # 6. Расход токенов
    report.append("## 6. Расход токенов по агентам")
    tokens = metrics.get('tokens', {})
    if tokens:
        total_tokens = sum(data.get('tokens', 0) for data in tokens.values())
        report.append(f"**Всего токенов:** {total_tokens}")
        for agent, data in tokens.items():
            report.append(f"- {agent.upper()}: {data.get('tokens')} токенов ({data.get('calls')} вызовов)")
    else:
        report.append("- Нет данных по токенам.")

    return "\n".join(report)
