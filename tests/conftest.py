"""Global test fixtures - ensures data files are restored after tests."""

import os
import shutil

import pytest

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
FAQ_PATH = os.path.join(DATA_DIR, "faq.json")
_faq_backup_content = None


def pytest_configure(config):
    """Backup faq.json at test session start."""
    global _faq_backup_content
    if os.path.exists(FAQ_PATH):
        with open(FAQ_PATH, "r", encoding="utf-8") as f:
            _faq_backup_content = f.read()


def _clear_rate_limiter():
    """Clear rate limiter state to prevent 429s across test modules."""
    try:
        from web_server import advanced_rate_limiter
        if hasattr(advanced_rate_limiter, 'reset'):
            advanced_rate_limiter.reset()
        else:
            for attr in ('_requests', '_endpoint_hits', '_user_hits', '_windows', '_quotas_used'):
                d = getattr(advanced_rate_limiter, attr, None)
                if d is not None and hasattr(d, 'clear'):
                    d.clear()
    except Exception:
        pass


def pytest_runtest_setup(item):
    """Reset rate limiter before tests that use the Flask client."""
    _clear_rate_limiter()


def pytest_runtest_teardown(item, nextitem):
    """Restore faq.json after every test to prevent pollution."""
    if _faq_backup_content is not None:
        with open(FAQ_PATH, "r", encoding="utf-8") as f:
            current = f.read()
        if current != _faq_backup_content:
            with open(FAQ_PATH, "w", encoding="utf-8") as f:
                f.write(_faq_backup_content)


# ─────────────────────────────────────────────────────────────────────
# H4: sentence-transformers MagicMock fixture
# 실제 모델 로드(~3-4s) + HuggingFace fetch(~2-3s) 회피.
# encode()는 deterministic 비-zero 벡터 반환 (FAQ별 고유, 코사인 유사도 비교 가능).
# 사용법:
#   def test_xxx(mock_sentence_transformer):
#       # src.vector_search.SentenceTransformer가 mock으로 대체된 상태
#       engine = VectorSearchEngine(faqs)
#       results = engine.find_best_match("query")
#
# 점진적 적용: 기존 @pytest.mark.skipif(not HAS_EMBEDDINGS, ...) 테스트를
# fixture 인자 추가 + 마커 제거하면 sentence-transformers 미설치 환경에서도 동작.
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture
def mock_sentence_transformer(monkeypatch):
    """sentence-transformers SentenceTransformer를 in-memory MagicMock으로 대체."""
    import hashlib
    import numpy as np
    from unittest.mock import MagicMock

    def _embed_one(text: str) -> np.ndarray:
        # deterministic seed from text hash → 384-dim 단위 벡터
        seed = int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(384).astype(np.float32)
        n = np.linalg.norm(v)
        return v / n if n > 0 else v

    def fake_encode(sentences, **kwargs):
        convert_to_numpy = kwargs.get("convert_to_numpy", True)
        if isinstance(sentences, str):
            v = _embed_one(sentences)
            return v if convert_to_numpy else v.tolist()
        arr = np.stack([_embed_one(s) for s in sentences])
        return arr if convert_to_numpy else arr.tolist()

    mock_model = MagicMock()
    mock_model.encode = fake_encode

    def _factory(*args, **kwargs):
        return mock_model

    # 모듈에 SentenceTransformer가 None일 수도 있으므로 raising=False
    monkeypatch.setattr(
        "src.vector_search.SentenceTransformer", _factory, raising=False
    )
    monkeypatch.setattr("src.vector_search.HAS_EMBEDDINGS", True, raising=False)
    return mock_model
