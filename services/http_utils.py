import time
import logging
import functools
import requests
import asyncio

logger = logging.getLogger("NexusPolyBot.HttpUtils")

def make_session_with_timeout(connect: float = 5.0, read: float = 15.0) -> requests.Session:
    """Создаёт requests.Session с предустановленным timeout."""
    session = requests.Session()
    session.request = functools.partial(session.request, timeout=(connect, read))
    return session

def fetch_with_retry(fetch_fn, *args, max_attempts: int = 3, base_delay: float = 1.0):
    """Повторяет fetch_fn(*args) до max_attempts раз при сетевых ошибках."""
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return fetch_fn(*args)
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.RequestException) as e:
            last_exc = e
            logger.warning(f"[HttpUtils] Attempt {attempt+1}/{max_attempts} failed: {e}")
            if attempt < max_attempts - 1:
                time.sleep(base_delay * (2 ** attempt))
    logger.error(f"[HttpUtils] All {max_attempts} attempts exhausted. Last: {last_exc}")
    return None

async def fetch_with_retry_async(fetch_fn, *args, max_attempts: int = 3, base_delay: float = 1.0):
    """Повторяет асинхронный fetch_fn(*args) до max_attempts раз при исключениях."""
    last_exc = None
    for attempt in range(max_attempts):
        try:
            import inspect
            if inspect.iscoroutinefunction(fetch_fn):
                return await fetch_fn(*args)
            else:
                return fetch_fn(*args)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_exc = e
            logger.warning(f"[HttpUtils] Async attempt {attempt+1}/{max_attempts} failed: {e}")
            if attempt < max_attempts - 1:
                await asyncio.sleep(base_delay * (2 ** attempt))
    logger.error(f"[HttpUtils] All {max_attempts} async attempts exhausted. Last: {last_exc}")
    return None

