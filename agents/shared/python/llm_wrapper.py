import time
import functools
import logging
import asyncio
from typing import Callable, Any

from config import llm_health_gate
from core.guards import LLMUnavailableError

logger = logging.getLogger("NexusPolyBot.llm_wrapper")

def with_retry(max_attempts: int = 3, initial_backoff: float = 2.0):
    """
    Декоратор для оборачивания вызовов LLM.
    Проверяет HealthGate перед вызовом.
    Делает exponential backoff при ошибках.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
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
            NON_RETRYABLE = (NameError, AttributeError, TypeError, ValueError, KeyError)
            backoff = initial_backoff
            last_error = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    result = await func(*args, **kwargs)
                    if isinstance(result, tuple) and len(result) == 2 and result[0] is None:
                        raise ValueError("LLM returned None (all providers failed)")
                        
                    llm_health_gate.record_success()
                    return result
                except NON_RETRYABLE as e:
                    logger.error(f"[{agent_name}] Non-retryable error: {e}", exc_info=True)
                    raise
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
                        
                    logger.warning(f"[{agent_name}] LLM async call failed (attempt {attempt}/{max_attempts}): {e}. Retrying in {backoff}s...")
                    await asyncio.sleep(backoff)
                    backoff *= 2.0
                    
            llm_health_gate.record_error(429)
            raise LLMUnavailableError(f"LLM API is unavailable after {max_attempts} attempts. Last error: {last_error}", agent_name=agent_name)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
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
            NON_RETRYABLE = (NameError, AttributeError, TypeError, ValueError, KeyError)
            backoff = initial_backoff
            last_error = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    result = func(*args, **kwargs)
                    if isinstance(result, tuple) and len(result) == 2 and result[0] is None:
                        raise ValueError("LLM returned None (all providers failed)")
                        
                    llm_health_gate.record_success()
                    return result
                except NON_RETRYABLE as e:
                    logger.error(f"[{agent_name}] Non-retryable error: {e}", exc_info=True)
                    raise
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
                    
            llm_health_gate.record_error(429)
            raise LLMUnavailableError(f"LLM API is unavailable after {max_attempts} attempts. Last error: {last_error}", agent_name=agent_name)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    return decorator
