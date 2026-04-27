"""하이브리드 검색 엔진 V3 - BM25 + 키워드 + 변형 매칭 결합.

Signals combined:
    - Keyword match (classifier logic) : weight 0.3
    - BM25 relevance score            : weight 0.4
    - Variant (paraphrase) match      : weight 0.3

Usage:
    hs = HybridSearchV3(faq_items, "data/question_variants.json")
    hits = hs.search("보세전시장이 뭐야", top_k=3)
    explanation = hs.explain_result("보세전시장이 뭐야", "A")
"""

from __future__ import annotations

import logging
import os

from src.bm25_ranker import BM25Ranker
from src.variant_matcher import VariantMatcher
from src.classifier import CATEGORY_KEYWORDS, classify_query

logger = logging.getLogger(__name__)


class HybridSearchV3:
    """3-signal 하이브리드 검색 엔진."""

    DEFAULT_WEIGHTS = {"keyword": 0.3, "bm25": 0.4, "variant": 0.3}

    def __init__(
        self,
        faq_items: list[dict],
        variants_path: str = "data/question_variants.json",
    ) -> None:
        """인덱스를 구축한다.

        Args:
            faq_items: FAQ 항목 리스트 (id, question, answer, keywords, category 필드).
            variants_path: question_variants.json 파일 경로.
        """
        self.faq_items = faq_items or []
        self.variants_path = variants_path

        # 가중치 (tunable)
        self.weights: dict[str, float] = dict(self.DEFAULT_WEIGHTS)

        # BM25 인덱스
        self.bm25 = BM25Ranker(self.faq_items)

        # 변형 매처
        self.variant_matcher = VariantMatcher()
        self._variants_loaded = False
        if variants_path and os.path.exists(variants_path):
            try:
                self.variant_matcher.load_variants(variants_path)
                self._variants_loaded = True
            except Exception as e:
                logger.warning(f"변형 데이터 로드 실패: {e}")

        # faq_id -> item 매핑
        self._item_by_id: dict[str, dict] = {}
        for item in self.faq_items:
            fid = item.get("id")
            if fid is not None:
                self._item_by_id[str(fid)] = item

    # ------------------------------------------------------------------
    # Weight management
    # ------------------------------------------------------------------
    def set_weights(self, kw: float, bm25: float, variant: float) -> None:
        """3개 시그널의 가중치를 설정한다.

        가중치는 자동으로 정규화 되지 않는다; 호출자가 원하는 값을 그대로 사용한다.
        음수는 0으로 클램프된다.

        Args:
            kw: keyword 매칭 가중치.
            bm25: BM25 가중치.
            variant: 변형 매칭 가중치.
        """
        self.weights = {
            "keyword": max(0.0, float(kw)),
            "bm25": max(0.0, float(bm25)),
            "variant": max(0.0, float(variant)),
        }

    def get_weights(self) -> dict[str, float]:
        """현재 가중치를 반환한다."""
        return dict(self.weights)

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------
    def _keyword_score(self, query: str, item: dict) -> tuple[float, list[str]]:
        """키워드 매칭 점수를 계산한다.

        classifier의 카테고리 키워드 + item의 자체 keywords를 기준으로 한다.

        Returns:
            (score in [0, 1], matched_keywords)
        """
        if not query:
            return 0.0, []

        q_lower = query.lower()
        matched: list[str] = []
        total_candidates = 0

        # item keywords (highest priority)
        item_keywords = item.get("keywords", []) or []
        if isinstance(item_keywords, str):
            item_keywords = [item_keywords]
        total_candidates += len(item_keywords)
        for kw in item_keywords:
            if kw and kw.lower() in q_lower:
                matched.append(kw)

        # category keywords
        category = item.get("category", "")
        cat_keywords = CATEGORY_KEYWORDS.get(category, [])
        total_candidates += len(cat_keywords)
        for kw in cat_keywords:
            if kw and kw.lower() in q_lower:
                matched.append(kw)

        if total_candidates == 0:
            return 0.0, matched

        # Normalize: the raw count of matches scaled by log for diminishing returns.
        # Score = min(1, matches / max(1, min(5, total_candidates)))
        # This yields a score in [0, 1].
        denom = max(1, min(5, total_candidates))
        raw = min(1.0, len(matched) / denom)
        return raw, matched

    def _bm25_scores(self, query: str) -> dict[str, tuple[float, float]]:
        """모든 FAQ에 대한 BM25 원점수 + 정규화된 점수를 반환한다.

        Returns:
            {faq_id: (raw_score, normalized_score_in_0_1)}
        """
        raw = self.bm25.rank(query, top_k=len(self.faq_items) or 1)
        scores: dict[str, tuple[float, float]] = {}
        if not raw:
            return scores

        max_score = max((r["score"] for r in raw), default=0.0)
        for r in raw:
            fid = str(r["item"].get("id"))
            if max_score > 0:
                scores[fid] = (r["score"], r["score"] / max_score)
            else:
                scores[fid] = (r["score"], 0.0)
        return scores

    def _variant_scores(self, query: str) -> dict[str, tuple[float, str]]:
        """모든 FAQ에 대한 최고 변형 매칭 점수를 반환한다.

        Returns:
            {faq_id: (score_in_0_1, matched_text)}
        """
        result: dict[str, tuple[float, str]] = {}
        if not self._variants_loaded:
            return result

        query_vec = self.variant_matcher._query_tfidf(query)
        if not query_vec:
            return result

        docs = self.variant_matcher._documents
        doc_map = self.variant_matcher._doc_faq_map
        matrix = self.variant_matcher._tfidf_matrix

        for i, doc_vec in enumerate(matrix):
            score = self.variant_matcher._cosine_similarity(query_vec, doc_vec)
            fid = str(doc_map[i])
            prev = result.get(fid)
            if prev is None or score > prev[0]:
                result[fid] = (score, docs[i])

        return result

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """3개 시그널을 결합하여 상위 FAQ를 반환한다.

        Args:
            query: 사용자 질문.
            top_k: 반환할 최대 결과 수.

        Returns:
            [{faq_id, score, matched_via, matched_text, breakdown, item}] 리스트.
            점수 내림차순.
        """
        if not query or not query.strip() or top_k <= 0:
            return []

        bm25_map = self._bm25_scores(query)
        variant_map = self._variant_scores(query)

        w_kw = self.weights.get("keyword", 0.0)
        w_bm = self.weights.get("bm25", 0.0)
        w_var = self.weights.get("variant", 0.0)

        results: list[dict] = []
        for item in self.faq_items:
            fid = str(item.get("id", ""))
            kw_score, matched_kws = self._keyword_score(query, item)
            bm_raw, bm_norm = bm25_map.get(fid, (0.0, 0.0))
            var_score, var_text = variant_map.get(fid, (0.0, ""))

            combined = (
                w_kw * kw_score + w_bm * bm_norm + w_var * var_score
            )

            # Determine contributing signals
            contributions = {
                "keyword": w_kw * kw_score,
                "bm25": w_bm * bm_norm,
                "variant": w_var * var_score,
            }
            matched_via = max(contributions, key=contributions.get)
            if contributions[matched_via] <= 0:
                # Nothing matched this item
                continue

            # Select matched_text per winning signal
            if matched_via == "variant" and var_text:
                matched_text = var_text
            elif matched_via == "keyword" and matched_kws:
                matched_text = ", ".join(matched_kws[:5])
            else:
                matched_text = item.get("question", "")

            results.append({
                "faq_id": fid,
                "score": round(combined, 6),
                "matched_via": matched_via,
                "matched_text": matched_text,
                "breakdown": {
                    "keyword": round(kw_score, 6),
                    "bm25": round(bm_norm, 6),
                    "variant": round(var_score, 6),
                },
                "item": item,
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    def explain_result(self, query: str, faq_id: str) -> dict:
        """특정 FAQ가 이 쿼리에 매칭된 이유를 상세히 설명한다.

        Args:
            query: 사용자 질문.
            faq_id: FAQ 식별자.

        Returns:
            {faq_id, query, found, score, breakdown, matched_via,
             matched_keywords, matched_variant, query_categories, item}
        """
        fid = str(faq_id)
        item = self._item_by_id.get(fid)
        if item is None:
            return {
                "faq_id": fid,
                "query": query,
                "found": False,
                "reason": "FAQ not found",
            }

        if not query or not query.strip():
            return {
                "faq_id": fid,
                "query": query,
                "found": True,
                "reason": "empty query",
                "score": 0.0,
                "breakdown": {"keyword": 0.0, "bm25": 0.0, "variant": 0.0},
                "item": item,
            }

        kw_score, matched_kws = self._keyword_score(query, item)
        bm25_map = self._bm25_scores(query)
        variant_map = self._variant_scores(query)

        bm_raw, bm_norm = bm25_map.get(fid, (0.0, 0.0))
        var_score, var_text = variant_map.get(fid, (0.0, ""))

        w_kw = self.weights.get("keyword", 0.0)
        w_bm = self.weights.get("bm25", 0.0)
        w_var = self.weights.get("variant", 0.0)

        contributions = {
            "keyword": w_kw * kw_score,
            "bm25": w_bm * bm_norm,
            "variant": w_var * var_score,
        }
        matched_via = (
            max(contributions, key=contributions.get)
            if any(v > 0 for v in contributions.values())
            else "none"
        )

        combined = sum(contributions.values())

        reasons: list[str] = []
        if kw_score > 0:
            reasons.append(
                f"키워드 매칭: {matched_kws[:5]} (점수 {kw_score:.3f})"
            )
        if bm_norm > 0:
            reasons.append(
                f"BM25 매칭: raw={bm_raw:.3f} normalized={bm_norm:.3f}"
            )
        if var_score > 0:
            reasons.append(
                f"변형 매칭: '{var_text}' (유사도 {var_score:.3f})"
            )
        if not reasons:
            reasons.append("매칭된 시그널 없음")

        return {
            "faq_id": fid,
            "query": query,
            "found": True,
            "score": round(combined, 6),
            "matched_via": matched_via,
            "matched_keywords": matched_kws,
            "matched_variant": var_text,
            "bm25_raw": round(bm_raw, 6),
            "bm25_normalized": round(bm_norm, 6),
            "keyword_score": round(kw_score, 6),
            "variant_score": round(var_score, 6),
            "contributions": {k: round(v, 6) for k, v in contributions.items()},
            "breakdown": {
                "keyword": round(kw_score, 6),
                "bm25": round(bm_norm, 6),
                "variant": round(var_score, 6),
            },
            "weights": dict(self.weights),
            "query_categories": classify_query(query),
            "reasons": reasons,
            "item": item,
        }
