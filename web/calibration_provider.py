import logging
from typing import List, Dict, Any
from agents.shared.python.db import get_connection

logger = logging.getLogger("NexusPolyBot.web.calibration")

class CalibrationProvider:
    @staticmethod
    def get_recent_calibration_runs(limit: int = 10) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            conn.row_factory = dict_factory
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, run_at, trigger_type, window_days, signals_analyzed, metrics_json, params_proposed, status 
                FROM calibration_runs 
                ORDER BY id DESC 
                LIMIT ?
            """, (limit,))
            return cursor.fetchall()

    @staticmethod
    def get_pending_calibration_params() -> List[Dict[str, Any]]:
        with get_connection() as conn:
            conn.row_factory = dict_factory
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, strategy_type, param_name, param_value, previous_value, reason, created_at 
                FROM calibration_params 
                WHERE status = 'pending'
                ORDER BY id ASC
            """)
            return cursor.fetchall()

    @staticmethod
    def approve_calibration_param(param_id: int, approved_by: str = "dashboard") -> bool:
        with get_connection() as conn:
            conn.row_factory = dict_factory
            cursor = conn.cursor()
            
            # Получаем сам параметр
            cursor.execute("SELECT strategy_type, param_name, param_value FROM calibration_params WHERE id = ? AND status = 'pending'", (param_id,))
            param = cursor.fetchone()
            
            if not param:
                return False
                
            strategy = param['strategy_type'].lower()
            val = param['param_value']
            
            # Сначала обновляем статус в БД
            cursor.execute("""
                UPDATE calibration_params 
                SET status = 'approved', approved_at = CURRENT_TIMESTAMP, approved_by = ? 
                WHERE id = ?
            """, (approved_by, param_id))
            
            # Сохраняем в память
            from agents.shared.python.db import save_memory
            if param['param_name'] == 'overlay_prompt':
                save_memory(f"{strategy}_overlay_prompt", str(val))
            else:
                # На всякий случай поддержка других параметров (числовых)
                try:
                    save_memory(f"{strategy}_{param['param_name']}", str(val))
                except Exception as e:
                    logger.error(f"Error saving parameter to memory: {e}")
                    
            return True

    @staticmethod
    def reject_calibration_param(param_id: int, rejected_by: str = "dashboard") -> bool:
        with get_connection() as conn:
            conn.row_factory = dict_factory
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE calibration_params 
                SET status = 'rejected', rejected_at = CURRENT_TIMESTAMP, rejected_by = ?
                WHERE id = ? AND status = 'pending'
            """, (rejected_by, param_id))
            return cursor.rowcount > 0

    @staticmethod
    def get_calibration_history(limit: int = 50) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            conn.row_factory = dict_factory
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, strategy_type, param_name, param_value, previous_value, reason, 
                       status, created_at, approved_at, approved_by, rejected_at, rejected_by
                FROM calibration_params 
                ORDER BY id DESC
                LIMIT ?
            """, (limit,))
            return cursor.fetchall()

    @staticmethod
    def get_current_overlays() -> Dict[str, str]:
        from agents.shared.python.db import get_memory
        return {
            "SCOUT": get_memory("scout_overlay_prompt", ""),
            "SWING": get_memory("swing_overlay_prompt", ""),
            "SHADOW": get_memory("shadow_overlay_prompt", "")
        }

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d
