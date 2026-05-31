import pytest
import numpy as np
from unittest.mock import patch, MagicMock
from core.semantic_filter import (
    semantic_similarity,
    semantic_same_event,
    batch_semantic_same_event,
    is_model_available
)

class MockModel:
    def encode(self, texts, convert_to_numpy=True):
        res = []
        for t in texts:
            t_low = t.lower()
            if "ohio" in t_low:
                if "democrat" in t_low:
                    res.append([1.0, 0.0, 0.1])
                else:
                    res.append([1.0, 0.0, 0.2])
            elif "bores" in t_low:
                res.append([0.1, 0.9, 0.0])
            elif "lahmeyer" in t_low:
                res.append([0.8, 0.1, 0.5])
            elif "btc" in t_low:
                if "100k" in t_low:
                    res.append([0.0, 1.0, 0.1])
                else:
                    res.append([0.0, 1.0, 0.15])
            elif "fed" in t_low:
                if "raise" in t_low:
                    res.append([1.0, 0.0, 0.0])
                else:
                    res.append([0.4, 0.9, 0.0])
            else:
                res.append([1.0, 0.0, 0.0])
        return np.array(res, dtype=np.float32)

@pytest.fixture(autouse=True)
def clean_globals():
    """Сбрасываем глобальное состояние модуля перед каждым тестом."""
    import core.semantic_filter as sf
    sf._model = None
    sf._model_failed = False
    yield

@patch("core.semantic_filter._get_model")
def test_semantic_similarity_success(mock_get):
    mock_get.return_value = MockModel()
    
    sim = semantic_similarity("Democrat wins Ohio", "Republican wins Ohio")
    assert sim is not None
    assert sim >= 0.75

@patch("core.semantic_filter._get_model")
def test_semantic_same_event_different(mock_get):
    mock_get.return_value = MockModel()
    
    res = semantic_same_event("Alex Bores wins NY-12", "Jackson Lahmeyer wins OK-01")
    assert res is False

@patch("core.semantic_filter._get_model")
def test_semantic_same_event_same(mock_get):
    mock_get.return_value = MockModel()
    
    res = semantic_same_event("BTC above 100K", "BTC above 90K")
    assert res is True

@patch("core.semantic_filter._get_model")
def test_semantic_same_event_gray_zone(mock_get):
    mock_get.return_value = MockModel()
    
    res = semantic_same_event("Fed raise rates", "Fed pause hikes")
    assert res is None  # Серая зона

@patch("core.semantic_filter._get_model", return_value=None)
def test_semantic_filter_no_sentence_transformers(mock_get):
    # Тест graceful fallback, когда библиотека или модель недоступны
    assert is_model_available() is False
    assert semantic_similarity("A", "B") is None
    assert semantic_same_event("A", "B") is None
    assert batch_semantic_same_event([("A", "B")]) == [None]

@patch("core.semantic_filter._get_model")
def test_batch_semantic_same_event(mock_get):
    mock_get.return_value = MockModel()
    
    pairs = [
        ("Democrat wins Ohio", "Republican wins Ohio"),
        ("Alex Bores wins NY-12", "Jackson Lahmeyer wins OK-01"),
        ("Fed raise rates", "Fed pause hikes")
    ]
    
    results = batch_semantic_same_event(pairs)
    assert len(results) == 3
    assert results[0] is True
    assert results[1] is False
    assert results[2] is None
