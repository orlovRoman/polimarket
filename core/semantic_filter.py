import logging
import numpy as np
from typing import List, Tuple, Optional

logger = logging.getLogger("SemanticFilter")

_model = None
_model_failed = False

def _get_model():
    """Ленивая загрузка sentence-transformers модели."""
    global _model, _model_failed
    if _model_failed:
        return None
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
        return None
    except Exception as e:
        logger.warning(
            f"[SemanticFilter] Не удалось загрузить модель: {e}. "
            f"Используется regex-fallback."
        )
        _model_failed = True
        return None

def is_model_available() -> bool:
    """Возвращает True, если модель успешно загружена."""
    return _get_model() is not None

def semantic_similarity(title_a: str, title_b: str) -> Optional[float]:
    """
    Вычисляет косинусное сходство между двумя заголовками [0.0, 1.0].
    Возвращает None, если модель недоступна.
    """
    model = _get_model()
    if model is None:
        return None
        
    try:
        embeddings = model.encode([title_a, title_b], convert_to_numpy=True)
        # Нормализуем векторы
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings_norm = embeddings / norms
        similarity = np.dot(embeddings_norm[0], embeddings_norm[1])
        # Ограничиваем в диапазон [-1, 1] и смещаем в [0, 1]
        similarity = max(-1.0, min(1.0, float(similarity)))
        return (similarity + 1.0) / 2.0  # нормализуем косинус [-1, 1] -> [0, 1]
    except Exception as e:
        logger.warning(f"[SemanticFilter] Ошибка вычисления сходства: {e}")
        return None

def semantic_same_event(title_a: str, title_b: str) -> Optional[bool]:
    """
    True  — 100% одно событие (cosine_similarity >= 0.75)
    False — 100% разные события (cosine_similarity < 0.65)
    None  — серая зона (0.65 <= similarity < 0.75) или модель недоступна
    """
    sim = semantic_similarity(title_a, title_b)
    if sim is None:
        return None
        
    if sim >= 0.75:
        return True
    if sim < 0.65:
        return False
    return None

def batch_semantic_same_event(pairs: List[Tuple[str, str]]) -> List[Optional[bool]]:
    """
    Пакетная обработка N пар заголовков за один проход модели.
    Возвращает список результатов той же длины и в том же порядке.
    """
    model = _get_model()
    if model is None or not pairs:
        return [None] * len(pairs)
        
    try:
        # Извлекаем уникальные тексты для кодирования, чтобы избежать повторных вычислений
        texts = []
        for a, b in pairs:
            texts.extend([a, b])
            
        embeddings = model.encode(texts, convert_to_numpy=True)
        # Нормализуем
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings_norm = embeddings / norms
        
        results = []
        for i in range(len(pairs)):
            idx_a = i * 2
            idx_b = idx_a + 1
            similarity = np.dot(embeddings_norm[idx_a], embeddings_norm[idx_b])
            similarity = max(-1.0, min(1.0, float(similarity)))
            sim_norm = (similarity + 1.0) / 2.0
            
            if sim_norm >= 0.75:
                results.append(True)
            elif sim_norm < 0.65:
                results.append(False)
            else:
                results.append(None)
                
        return results
    except Exception as e:
        logger.warning(f"[SemanticFilter] Ошибка в пакетном вычислении: {e}")
        return [None] * len(pairs)
