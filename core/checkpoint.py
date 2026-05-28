import json
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path
from config import PROJECT_ROOT

CHECKPOINTS_FILE = PROJECT_ROOT / "vault" / "checkpoints.json"

# Храним чекпоинты в памяти для скорости, сбрасываем в файл для персистентности (опционально)
_checkpoints_cache: Dict[str, Any] = {}

def _load():
    global _checkpoints_cache
    if CHECKPOINTS_FILE.exists():
        try:
            with open(CHECKPOINTS_FILE, "r", encoding="utf-8") as f:
                _checkpoints_cache = json.load(f)
        except Exception:
            _checkpoints_cache = {}

def _save():
    try:
        CHECKPOINTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CHECKPOINTS_FILE, "w", encoding="utf-8") as f:
            json.dump(_checkpoints_cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# Инициализация
_load()

def save_checkpoint(phase: str, **kwargs) -> None:
    """Сохраняет состояние (чекпоинт) конкретной фазы пайплайна."""
    global _checkpoints_cache
    
    _checkpoints_cache[phase] = {
        "timestamp": datetime.now().isoformat(),
        **kwargs
    }
    _save()

def get_checkpoint(phase: str) -> Optional[Dict[str, Any]]:
    """Возвращает сохраненный чекпоинт для фазы."""
    return _checkpoints_cache.get(phase)

def verify_checkpoint(phase: str) -> bool:
    """Проверяет, был ли успешно сохранён чекпоинт (и статус ok)."""
    cp = get_checkpoint(phase)
    if not cp:
        return False
    return cp.get("status") in ("ok", "success")
