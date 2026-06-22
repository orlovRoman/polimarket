import os
import json
import logging
from datetime import datetime, timezone
import asyncio
import functools

from agents.shared.python.db import get_connection, save_memory
from agents.shared.utils.gemini_client import generate_content_with_fallback, extract_response_text
from .calibration_metrics import get_all_metrics
from .calibration_report import generate_calibration_report
from .calibration_config import CALIB_CONFIG

logger = logging.getLogger("NexusPolyBot.Calibrate")

CALIBRATION_SYSTEM_PROMPT = """ТЫ — СТРАТЕГИЧЕСКИЙ КАЛИБРАТОР (CALIBRATOR).
Твоя задача — анализировать недельные метрики ИИ-агентов, находить слабые места и предлагать микро-корректировки их промптов.
У нас есть три агента:
- SCOUT: оценивает рынки
- SWING: оценивает долгосрочные перспективы
- SHADOW: критик (approval/rejection)

Твой ответ ДОЛЖЕН БЫТЬ строго в формате JSON:
{
  "scout_overlay": "Краткая инструкция (до 1-2 предложений), например: 'Снизь уверенность на 15% в спорте'.",
  "scout_reasoning": "Краткое обоснование изменений для SCOUT или почему они не требуются.",
  "swing_overlay": "...",
  "swing_reasoning": "...",
  "shadow_overlay": "...",
  "shadow_reasoning": "..."
}
Если калибровка не требуется, оставь строку overlay пустой.
Не возвращай ничего, кроме валидного JSON (без markdown-блоков, только сырой JSON).
"""

async def run_calibration(window_days: int = None, trigger_type: str = "scheduled") -> tuple[str, bool]:
    if window_days is None:
        window_days = CALIB_CONFIG.default_window_days
    logger.info(f"Начало цикла калибровки за последние {window_days} дней...")
    
    # 1. Собираем метрики (Блок транзакции 1: Чтение)
    with get_connection() as conn:
        metrics = get_all_metrics(conn, window_days)
        metrics_json = json.dumps(metrics, ensure_ascii=False)
        total_analyzed = metrics.get('funnel', {}).get('total_analyzed', 0)
        
        # 2. Формируем текстовый отчёт (удобно для логов/отладки)
        report_text = generate_calibration_report(metrics)
        logger.info("\n" + report_text)
        
        # Если сделок/событий совсем мало, пропускаем калибровку
        if total_analyzed < CALIB_CONFIG.min_markets_for_calibration:
            logger.info(f"Недостаточно данных для калибровки (менее {CALIB_CONFIG.min_markets_for_calibration} рынков). Пропуск.")
            # Пишем пропуск в БД внутри этого же блока
            conn.execute("""
                INSERT INTO calibration_runs (trigger_type, window_days, signals_analyzed, metrics_json, status)
                VALUES (?, ?, ?, ?, ?)
            """, (trigger_type, window_days, total_analyzed, metrics_json, "skipped_low_data"))
            return report_text, False
            
    # 3. Формируем запрос к LLM (БЕЗ удержания соединения SQLite)
    payload = {
        "systemInstruction": {
            "parts": [{"text": CALIBRATION_SYSTEM_PROMPT}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": f"Вот отчет за {window_days} дней:\n{report_text}\nПредложи overlay-инструкции для агентов."
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "response_mime_type": "application/json"
        }
    }
    
    # Вызываем LLM
    logger.info("Вызов LLM (CALIBRATOR)...")
    try:
        import config
        llm_result, _ = await asyncio.to_thread(
            functools.partial(
                generate_content_with_fallback,
                api_key=config.GOOGLE_API_KEY,
                payload=payload,
                default_model="gemini-2.5-pro",
                agent_name="CALIBRATOR"
            )
        )
        response_text = extract_response_text(llm_result)
        # Парсим JSON
        calib_data = json.loads(response_text)
        
    except Exception as e:
        logger.error(f"Ошибка вызова LLM для калибровки: {e}")
        # Пишем ошибку в БД (Блок транзакции 2: Запись ошибки)
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO calibration_runs (trigger_type, window_days, signals_analyzed, metrics_json, status)
                VALUES (?, ?, ?, ?, ?)
            """, (trigger_type, window_days, total_analyzed, metrics_json, f"error: {str(e)[:50]}"))
        return report_text, False
        
    # 4. Сохраняем overlay в memory и записываем параметры в calibration_params (Блок транзакции 3: Запись результатов)
    scout_overlay = calib_data.get("scout_overlay", "").strip()
    swing_overlay = calib_data.get("swing_overlay", "").strip()
    shadow_overlay = calib_data.get("shadow_overlay", "").strip()
    
    # Поагентное обоснование (Решение проблемы #5)
    scout_reason = calib_data.get("scout_reasoning", "").strip()
    swing_reason = calib_data.get("swing_reasoning", "").strip()
    shadow_reason = calib_data.get("shadow_reasoning", "").strip()
    
    with get_connection() as conn:
        # Сначала сохраняем запись калибровки, чтобы получить run_id (Решение проблемы #6)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO calibration_runs (trigger_type, window_days, signals_analyzed, metrics_json, nexus_response, params_proposed, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (trigger_type, window_days, total_analyzed, metrics_json, response_text, 0, "completed"))
        run_id = cursor.lastrowid
        
        from agents.shared.python.db import get_memory
        old_scout = get_memory("scout_overlay_prompt", "")
        old_swing = get_memory("swing_overlay_prompt", "")
        old_shadow = get_memory("shadow_overlay_prompt", "")
        
        params_proposed = 0
        
        if scout_overlay:
            _save_param(conn, "scout", scout_overlay, scout_reason or "No reasoning provided", old_scout, run_id)
            params_proposed += 1
        if swing_overlay:
            _save_param(conn, "swing", swing_overlay, swing_reason or "No reasoning provided", old_swing, run_id)
            params_proposed += 1
        if shadow_overlay:
            _save_param(conn, "shadow", shadow_overlay, shadow_reason or "No reasoning provided", old_shadow, run_id)
            params_proposed += 1
            
        if params_proposed > 0:
            conn.execute("""
                UPDATE calibration_runs 
                SET params_proposed = ? 
                WHERE id = ?
            """, (params_proposed, run_id))
            
    logger.info(f"Калибровка завершена. Предложено {params_proposed} параметров.")
    return report_text, (params_proposed > 0)

def _save_param(conn, strategy_type: str, new_value: str, reason: str, old_value: str, run_id: int = None):
    # Сохраняем в таблицу с привязкой к run_id
    conn.execute("""
        INSERT INTO calibration_params (strategy_type, param_name, param_value, previous_value, reason, status, run_id)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
    """, (strategy_type.upper(), "overlay_prompt", new_value, old_value, reason, run_id))
    
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_calibration(7))
