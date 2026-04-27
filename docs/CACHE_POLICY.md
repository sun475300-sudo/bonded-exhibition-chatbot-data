# 캐시 정책 분석 (K2)

> bonded-exhibition-chatbot-data 의 다층 캐시 구조 정리. PR #37의 lazy load + warmup 패턴과 함께 운영 가이드.

## 1. 캐시 계층 (3개)

| 계층 | 위치 | 키 | TTL | 용량 | 무효화 |
|---|---|---|---|---|---|
| **임베딩** | `src/vector_search.py:VectorSearchEngine.embedding_cache` | query MD5 | 무제한 | **1000건** (FIFO 축출) | 프로세스 재시작 |
| **LLM 응답** | `src/llm_fallback.py:ResponseCache` | (query, faq_context) tuple | **1시간** | 256건 | TTL 만료 + LRU 축출 |
| **HuggingFace 모델 메타** | `huggingface_hub` 기본 캐시 | 모델 이름 | 7일 (HF 기본) | 디스크 | `HF_HOME` 정리 |

## 2. 정책 요약

### 임베딩 캐시 (`VectorSearchEngine.embedding_cache`)
- **정책**: Cold start 시 비어있음 → 첫 query 인코딩 결과 누적 → 1000건 초과 시 가장 오래된 것 1개씩 축출 (FIFO, LRU 아님)
- **핵심 결함 후보**: 1000 vs `max_cache_size: 1000`은 일치하지만 `get_cache_stats` 의 `max_cache_size` 키가 *축출 임계*라는 의미는 어색. 진짜 LRU로 가려면 `collections.OrderedDict` + `move_to_end` 필요. 현재는 단순 dict + `next(iter(...))` pop이라 **삽입 순서 의존** — 일반 dict는 Python 3.7+에서 보장하지만 의미상 LRU 아님.
- **권장 개선** (별 PR):
  - `OrderedDict.move_to_end(key)`로 진짜 LRU
  - 또는 `functools.lru_cache(maxsize=1000)` 데코레이터 (단 단순 함수에만)

### LLM 응답 캐시 (`ResponseCache`)
- **정책**: TTL 1시간 + max_size 256. TTL 만료 시 자동 무효화. 같은 query+faq_context 조합 재사용.
- **rate-limit 보완**: rate_limiter (분당 10건) + cache로 cold-start 외엔 안전.
- **잠재 이슈**: faq_context가 list of dict 라 순서 변동 시 cache miss. 정렬된 키 권장.

### HF 모델 캐시
- HF Hub의 기본 동작 (`~/.cache/huggingface/`). E1 lazy load와 결합하면 첫 query 시 캐시 hit이라면 ~3-4s, miss면 ~10s+.
- **권장**: Docker 이미지 빌드 시 모델 사전 다운로드 (`pip install` 후 `python -c "SentenceTransformer(...)"` 단계).

## 3. 캐시 워밍업 옵션 (E2 후속)

PR #37에서 보류된 E2(임베딩 캐시 워밍업)의 구현 옵션:

### 옵션 A: startup hook
```python
# web_server.py 시작 시 비동기로
def _warmup_embeddings():
    if not chatbot.vector_search_enabled:
        return
    sample_queries = ["보세전시장", "반입", "수입신고", "관세", "전시"]
    for q in sample_queries:
        chatbot.vector_search.find_best_match(q, top_k=1)
```
- 장점: 첫 사용자 응답 빠름
- 단점: 시작 시간 길어짐 (`--preload`와 시너지)

### 옵션 B: 환경변수 토글
```python
if os.environ.get("EMBEDDING_WARMUP_ENABLED", "false").lower() == "true":
    _warmup_embeddings()
```
- 운영 환경에서만 활성화

### 옵션 C: 정기 background warmup
- TTL 만료 직전 자동 재인코딩
- 현재는 임베딩 캐시 TTL 없으니 불필요

**권장**: 옵션 A + B 조합. RUNBOOK에 환경변수 추가.

## 4. 캐시 정합성 (FAQ 변경 시)

- FAQ가 변경되면 `_precompute_embeddings`가 재계산되어야 정확. 현재는 init 시점 1회만.
- **결함**: 운영 중 FAQ hot-reload 시 임베딩 stale. PR #32(merged 미정) 의 hot-reload 변경과 연계 검토 필요.
- **권장**: `VectorSearchEngine.refresh(new_faq_items)` 메서드 추가 → embedding_cache + embeddings 모두 invalidate

## 5. 메트릭 / 관찰성

`/api/health` (PR #37 F2)의 `vector_index_loaded` + `get_cache_stats()` 노출 예:
```json
{
  "cached_queries": 142,
  "max_cache_size": 1000,
  "model": "sentence-transformers/...",
  "model_loaded": true,
  "embeddings_computed": true
}
```

운영 모니터링 권장 메트릭:
- `embedding_cache.size / max_size` (포화도)
- `llm_cache.hit_rate` (60% 이상 권장)
- `rate_limiter.calls_in_window / max_calls`

## 6. 결론

- 현재 캐시 구조 **기능적으로 정상**. lazy load + warmup으로 first-byte latency 개선 가능.
- **주요 액션 (별 PR 권장)**:
  - 임베딩 캐시 진짜 LRU (`OrderedDict`)
  - FAQ hot-reload 시 invalidate
  - startup warmup 환경변수 토글 (E2)
  - Docker 이미지에 모델 사전 다운로드
