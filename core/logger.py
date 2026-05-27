import time
import logging
from typing import Optional
from agents.shared.python.db import get_connection

logger = logging.getLogger("LLMLogger")

class LLMLogger:
    """Единая система оценки и логов LLM."""
    
    @staticmethod
    def log_call(
        agent_name: str,
        model_name: str,
        prompt: str,
        response: Optional[str] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: Optional[int] = None,
        error: Optional[str] = None,
        market_id: Optional[str] = None
    ):
        """Логирует вызов к LLM в таблицу llm_calls."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO llm_calls (
                        agent_name, model_name, market_id, prompt, response,
                        input_tokens, output_tokens, total_tokens, latency_ms, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    agent_name, model_name, market_id, prompt, response,
                    input_tokens, output_tokens, total_tokens, latency_ms, error
                ))
        except Exception as e:
            logger.error(f"Failed to log LLM call: {e}")

    @staticmethod
    def get_token_usage_last_24h(agent_name: str) -> dict:
        """Агрегирует статистику токенов из llm_calls за последние 24 часа."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT SUM(input_tokens) as in_t, SUM(output_tokens) as out_t, SUM(total_tokens) as tot_t 
                    FROM llm_calls 
                    WHERE agent_name = ? AND created_at >= datetime('now', '-1 day')
                """, (agent_name,))
                row = cursor.fetchone()
                if row and row['in_t'] is not None:
                    return {
                        "input_tokens": int(row['in_t']),
                        "output_tokens": int(row['out_t']),
                        "total_tokens": int(row['tot_t'])
                    }
        except Exception as e:
            logger.error(f"Error getting token usage: {e}")
            
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    @staticmethod
    def get_detailed_token_usage_last_24h(agent_name: str) -> list:
        """Агрегирует статистику токенов из llm_calls за последние 24 часа в разрезе по моделям."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT model_name, SUM(input_tokens) as in_t, SUM(output_tokens) as out_t, SUM(total_tokens) as tot_t 
                    FROM llm_calls 
                    WHERE agent_name = ? AND created_at >= datetime('now', '-1 day')
                    GROUP BY model_name
                """, (agent_name,))
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    if row['model_name']:
                        results.append({
                            "model_name": row['model_name'],
                            "input_tokens": int(row['in_t'] or 0),
                            "output_tokens": int(row['out_t'] or 0),
                            "total_tokens": int(row['tot_t'] or 0)
                        })
                return results
        except Exception as e:
            logger.error(f"Error getting detailed token usage: {e}")
            
        return []
