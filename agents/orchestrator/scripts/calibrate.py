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
  "swing_overlay": "...",
  "shadow_overlay": "...",
  "reasoning": "Краткое обоснование изменений."
}
Если калибровка не требуется, оставь строку overlay пустой.
Не возвращай ничего, кроме валидного JSON (без markdown-блоков, только сырой JSON).
"""

async def run_calibration(window_days: int = 7, trigger_type: str = "scheduled") -> tuple[str, bool]:
    logger.info(f"Начало цикла калибровки за последние {window_days} дней...")
    
    with get_connection() as conn:
        # 1. Собираем метрики
        metrics = get_all_metrics(conn, window_days)
        metrics_json = json.dumps(metrics, ensure_ascii=False)
        total_analyzed = metrics.get('funnel', {}).get('total_analyzed', 0)
        
        # 2. Формируем текстовый отчёт (удобно для логов/отладки)
        report_text = generate_calibration_report(metrics)
        logger.info("\n" + report_text)
        
        # Если сделок/событий совсем мало, можем пропустить калибровку
        if total_analyzed < 5:
            logger.info("Недостаточно данных для калибровки (менее 5 рынков). Пропуск.")
            conn.execute("""
                INSERT INTO calibration_runs (trigger_type, window_days, signals_analyzed, metrics_json, status)
                VALUES (?, ?, ?, ?, ?)
            """, (trigger_type, window_days, total_analyzed, metrics_json, "skipped_low_data"))
            return report_text, False
            
        # 3. Формируем запрос к LLM
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
            conn.execute("""
                INSERT INTO calibration_runs (trigger_type, window_days, signals_analyzed, metrics_json, status)
                VALUES (?, ?, ?, ?, ?)
            """, (trigger_type, window_days, total_analyzed, metrics_json, f"error: {str(e)[:50]}"))
            return report_text, False
            
        # 4. Сохраняем overlay в memory и записываем параметры в calibration_params
        scout_overlay = calib_data.get("scout_overlay", "").strip()
        swing_overlay = calib_data.get("swing_overlay", "").strip()
        shadow_overlay = calib_data.get("shadow_overlay", "").strip()
        reasoning = calib_data.get("reasoning", "No reasoning provided")
        
        from agents.shared.python.db import get_memory
        old_scout = get_memory("scout_overlay_prompt", "")
        old_swing = get_memory("swing_overlay_prompt", "")
        old_shadow = get_memory("shadow_overlay_prompt", "")
        
        params_proposed = 0
        
        if scout_overlay:
            _save_param(conn, "scout", scout_overlay, reasoning, old_scout)
            params_proposed += 1
        if swing_overlay:
            _save_param(conn, "swing", swing_overlay, reasoning, old_swing)
            params_proposed += 1
        if shadow_overlay:
            _save_param(conn, "shadow", shadow_overlay, reasoning, old_shadow)
            params_proposed += 1
            
        # 5. Сохраняем run
        conn.execute("""
            INSERT INTO calibration_runs (trigger_type, window_days, signals_analyzed, metrics_json, nexus_response, params_proposed, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (trigger_type, window_days, total_analyzed, metrics_json, response_text, params_proposed, "completed"))
        
        logger.info(f"Калибровка завершена. Предложено {params_proposed} параметров.")
        return report_text, (params_proposed > 0)

def _save_param(conn, strategy_type: str, new_value: str, reason: str, old_value: str):
    # Сохраняем в таблицу
    conn.execute("""
        INSERT INTO calibration_params (strategy_type, param_name, param_value, previous_value, reason, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
    """, (strategy_type.upper(), "overlay_prompt", new_value, old_value, reason))
    
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_calibration(7))
