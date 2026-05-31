import logging
import time
import numpy as np
from typing import List, Tuple, Optional

logger = logging.getLogger("SemanticFilter")

# Константы для порогов raw cosine similarity
SAME_EVENT_THRESHOLD = 0.75
DIFFERENT_EVENT_THRESHOLD = 0.65

_model = None
_model_failed = False
_model_failed_at = 0.0
_MODEL_RETRY_SEC = 300  # Повторная попытка инициализации через 5 минут

def _get_model():
    """Ленивая загрузка sentence-transformers модели с поддержкой повторных попыток при ошибках."""
    global _model, _model_failed, _model_failed_at
    if _model_failed:
        if time.monotonic() - _model_failed_at < _MODEL_RETRY_SEC:
            return None
        else:
            # Сбрасываем флаг ошибки для новой попытки
            _model_failed = False

    if _model is not None:
        return _model
        
    try:
        logger.info("[SemanticFilter] Загружаем all-MiniLM-L6-v2...")
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("[SemanticFilter] Модель успешно загружена.")
        return _model
    except ImportError:
        logger.warning(
            "[SemanticFilter] Библиотека sentence-transformers не установлена. "
            "Используется regex-fallback."
        )
        _model_failed = True
        _model_failed_at = time.monotonic()
        return None
    except Exception as e:
        logger.warning(
            f"[SemanticFilter] Не удалось загрузить модель: {e}. "
            f"Используется regex-fallback."
        )
        _model_failed = True
        _model_failed_at = time.monotonic()
        return None

def is_model_available() -> bool:
    """Возвращает True, если модель успешно загружена."""
    return _get_model() is not None

def semantic_similarity(title_a: str, title_b: str) -> Optional[float]:
    """
    Вычисляет raw косинусное сходство между двумя заголовками [-1.0, 1.0].
    Возвращает None, если модель недоступна.
    """
    model = _get_model()
    if model is None:
        return None
        
    try:
        # Используем normalize_embeddings=True для автоматической L2-нормализации векторов
        embeddings = model.encode(
            [title_a, title_b],
            normalize_embeddings=True,
            convert_to_numpy=True
        )
        # Косинусное сходство нормализованных векторов — это просто их скалярное произведение
        similarity = np.dot(embeddings[0], embeddings[1])
        return max(-1.0, min(1.0, float(similarity)))
    except Exception as e:
        logger.warning(f"[SemanticFilter] Ошибка вычисления сходства: {e}")
        return None

def semantic_same_event(title_a: str, title_b: str) -> Optional[bool]:
    """
    Определяет, описывают ли два заголовка одно событие.
    True  — 100% одно событие (raw similarity >= SAME_EVENT_THRESHOLD)
    False — 100% разные события (raw similarity < DIFFERENT_EVENT_THRESHOLD)
    None  — серая зона или модель недоступна
    """
    sim = semantic_similarity(title_a, title_b)
    if sim is None:
        return None
        
    if sim >= SAME_EVENT_THRESHOLD:
        return True
    if sim < DIFFERENT_EVENT_THRESHOLD:
        return False
    return None

def batch_semantic_same_event(pairs: List[Tuple[str, str]]) -> List[Optional[bool]]:
    """
    Пакетная обработка N пар заголовков за один проход модели с использованием raw cosine.
    Возвращает список результатов той же длины и в том же порядке.
    """
    model = _get_model()
    if model is None or not pairs:
        return [None] * len(pairs)
        
    try:
        texts = []
        for a, b in pairs:
            texts.extend([a, b])
            
        embeddings = model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True
        )
        
        results = []
        for i in range(len(pairs)):
            idx_a = i * 2
            idx_b = idx_a + 1
            similarity = np.dot(embeddings[idx_a], embeddings[idx_b])
            sim = max(-1.0, min(1.0, float(similarity)))
            
            if sim >= SAME_EVENT_THRESHOLD:
                results.append(True)
            elif sim < DIFFERENT_EVENT_THRESHOLD:
                results.append(False)
            else:
                results.append(None)
                
        return results
    except Exception as e:
        logger.warning(f"[SemanticFilter] Ошибка в пакетном вычислении: {e}")
        return [None] * len(pairs)
