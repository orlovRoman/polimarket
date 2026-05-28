import time
from datetime import datetime, timedelta
import threading

class LLMUnavailableError(Exception):
    """Исключение, выбрасываемое когда LLM недоступна (DEAD state)."""
    pass

class LLMHealthGate:
    def __init__(self):
        self.state = "HEALTHY"  # HEALTHY, DEGRADED, DEAD
        self.error_timestamps = []
        self.retry_after = datetime.min
        self.lock = threading.Lock()
        
        self.degraded_threshold = 3
        self.dead_threshold = 5
        self.window_sec = 60
        self.degraded_pause_sec = 60
        self.dead_pause_sec = 300  # 5 мин

    def record_error(self, status_code: int):
        if status_code not in (429, 503):
            return
            
        with self.lock:
            now = datetime.now()
            # Убираем старые ошибки (вне окна 60 сек)
            self.error_timestamps = [t for t in self.error_timestamps if (now - t).total_seconds() <= self.window_sec]
            
            self.error_timestamps.append(now)
            count = len(self.error_timestamps)
            
            if count >= self.dead_threshold:
                self.state = "DEAD"
                self.retry_after = now + timedelta(seconds=self.dead_pause_sec)
            elif count >= self.degraded_threshold:
                # Переходим в DEGRADED только если еще не DEAD
                if self.state != "DEAD":
                    self.state = "DEGRADED"
                    self.retry_after = now + timedelta(seconds=self.degraded_pause_sec)

    def record_success(self):
        with self.lock:
            if self.state != "HEALTHY":
                self.state = "HEALTHY"
                self.error_timestamps.clear()
                self.retry_after = datetime.min

    def check_availability(self):
        with self.lock:
            now = datetime.now()
            if self.retry_after > now:
                if self.state == "DEAD":
                    raise LLMUnavailableError(f"LLM API is DEAD. Retry after {self.retry_after}")
                return False  # DEGRADED, можно пропустить или ждать
            elif self.state in ("DEGRADED", "DEAD"):
                # Время паузы вышло, даем шанс (по сути, partial open)
                self.state = "HEALTHY"
                self.error_timestamps.clear()
            return True

    @property
    def retry_after_safe(self):
        with self.lock:
            return self.retry_after

    def _force_dead(self):
        """Для тестов."""
        with self.lock:
            self.state = "DEAD"
            self.retry_after = datetime.now() + timedelta(seconds=self.dead_pause_sec)

    @property
    def backoff_sec(self):
        return self.dead_pause_sec
