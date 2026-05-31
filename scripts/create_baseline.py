import os
import sys
import json
from datetime import datetime, timezone

# Добавляем корень проекта в пути импорта
sys.path.append(os.getcwd())

from config import logger
from core.engine import CoreEngine
from agents.shared.python.db import get_connection, save_memory

def dummy_callback(msg, reply_markup=None):
    logger.info(f"[Baseline Callback] Message received: {msg[:100]}...")

def run_baseline():
    logger.info("Инициализация CoreEngine для генерации baseline-отчета...")
    engine = CoreEngine()
    
    # 1. Настройка лимита сканирования (1 рынок для экономии токенов и избежания 429)
    save_memory("scan_limit", 1)
    
    # Очищаем кулдауны в analyzed_markets, чтобы скан не пропустил рынки
    with get_connection() as conn:
        conn.execute("DELETE FROM analyzed_markets")
        logger.info("Таблица analyzed_markets очищена для принудительного анализа рынков.")
    
    # Запоминаем время старта, чтобы собрать только новые аудиты
    start_time = datetime.now(timezone.utc)
    
    logger.info("Запуск run_team_discussion...")
    try:
        processed_count = engine.run_team_discussion(summary_callback=dummy_callback)
        logger.info(f"Скан завершен. Обработано рынков: {processed_count}")
    except Exception as e:
        logger.error(f"Ошибка при сканировании: {e}", exc_info=True)
        processed_count = 0

    # 2. Сбор метрик из БД (из таблицы idea_audit за время нашего сканирования)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT scout_edge, swing_found, shadow_agree, final_outcome 
            FROM idea_audit 
            WHERE created_at >= datetime('now', '-15 minutes')
        """)
        rows = cursor.fetchall()
        
    markets_scanned = len(rows)
    scout_signals = sum(1 for r in rows if r['scout_edge'] is not None)
    swing_signals = sum(1 for r in rows if r['swing_found'] == 1)
    shadow_passed = sum(1 for r in rows if r['shadow_agree'] == 1)
    ideas_found = sum(1 for r in rows if r['final_outcome'] == 'saved')
    
    report = {
        "markets_scanned": markets_scanned,
        "scout_signals": scout_signals,
        "swing_signals": swing_signals,
        "shadow_passed": shadow_passed,
        "ideas_found": ideas_found,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    os.makedirs("vault", exist_ok=True)
    report_path = "vault/baseline_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        
    logger.info(f"Baseline отчет успешно создан в {report_path}: {report}")

if __name__ == "__main__":
    run_baseline()
