import time
import functools
import logging
import asyncio
from typing import Callable, Any

from config import llm_health_gate
from core.guards import LLMUnavailableError

logger = logging.getLogger("llm_wrapper")

def with_retry(max_attempts: int = 3, initial_backoff: float = 2.0):
    """
    Декоратор для оборачивания вызовов LLM.
    Проверяет HealthGate перед вызовом.
    Делает exponential backoff при ошибках.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            agent_name = "UNKNOWN"
            if args:
                self_obj = args[0]
                agent_name = getattr(self_obj, "name", self_obj.__class__.__name__)

            # Сначала проверяем, не DEAD или DEGRADED ли шлюз (в режиме backoff)
            try:
                if not llm_health_gate.check_availability():
                    raise LLMUnavailableError(
                        f"LLM API is DEGRADED. Retry after {llm_health_gate.retry_after_safe}",
                        agent_name=agent_name
                    )
            except LLMUnavailableError as e:
                if getattr(e, "agent_name", "UNKNOWN") == "UNKNOWN":
                    raise LLMUnavailableError(str(e), agent_name=agent_name) from e
                raise
            backoff = initial_backoff
            last_error = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    result = func(*args, **kwargs)
                    # Если функция возвращает кортеж (result, model_name) и result is None,
                    # это означает исчерпание лимитов внутри fallback.
                    if isinstance(result, tuple) and len(result) == 2 and result[0] is None:
                        raise ValueError("LLM returned None (all providers failed)")
                        
                    llm_health_gate.record_success()
                    return result
                except Exception as e:
                    last_error = e
                    error_str = str(e).lower()
                    
                    status_code = None
                    if "429" in error_str:
                        status_code = 429
                    elif "503" in error_str or "502" in error_str or "500" in error_str:
                        status_code = 503
                        
                    if status_code:
                        llm_health_gate.record_error(status_code)
                        
                    if attempt == max_attempts:
                        break
                        
                    logger.warning(f"[{agent_name}] LLM call failed (attempt {attempt}/{max_attempts}): {e}. Retrying in {backoff}s...")
                    time.sleep(backoff)
                    backoff *= 2.0
                    
            # Если мы дошли до сюда, все попытки исчерпаны
            llm_health_gate.record_error(429) # Принудительно регистрируем сбой
            raise LLMUnavailableError(f"LLM API is unavailable after {max_attempts} attempts. Last error: {last_error}", agent_name=agent_name)
            
        return wrapper
    return decorator
