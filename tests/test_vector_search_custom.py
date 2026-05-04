import pytest
import numpy as np
from src.vector_search import VectorSearchEngine, DummyModel

def test_vector_search_dummy_model_output_type():
    """DummyModel이 numpy array를 올바르게 반환하는지 확인한다."""
    model = DummyModel()
    # 단일 문장
    res = model.encode("test", convert_to_numpy=True)
    assert isinstance(res, np.ndarray)
    assert res.shape == (384,)
    
    # 여러 문장
    res_list = model.encode(["test1", "test2"], convert_to_numpy=True)
    assert isinstance(res_list, np.ndarray)
    assert res_list.shape == (2, 384)

def test_vector_search_engine_initialization_with_empty_faq():
    """FAQ가 비어있을 때 엔진이 정상적으로 초기화되는지 확인한다."""
    engine = VectorSearchEngine([])
    assert engine.faq_items == []
    assert isinstance(engine.embeddings, np.ndarray)
    assert len(engine.embeddings) == 0

def test_vector_search_engine_find_best_match_empty_query():
    """쿼리가 비어있을 때 빈 리스트를 반환하는지 확인한다."""
    engine = VectorSearchEngine([{"question": "Q1", "answer": "A1"}])
    assert engine.find_best_match("") == []
    assert engine.find_best_match("   ") == []
