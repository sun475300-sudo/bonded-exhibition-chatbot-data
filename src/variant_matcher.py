"""FAQ 질문 변형 매칭 모듈 - 챗봇 매칭 정확도 향상용."""

import json
import math
import re
from collections import Counter
from typing import Optional


class VariantMatcher:
    """FAQ 질문 변형 데이터를 활용한 매칭 엔진.

    TF-IDF 유사도를 사용하여 사용자 쿼리를 FAQ 원본 질문 및
    변형 질문과 비교하여 가장 적합한 FAQ를 찾습니다.
    """

    def __init__(self):
        self.variants_data: Optional[dict] = None
        self._documents: list[str] = []
        self._doc_faq_map: list[str] = []
        self._tfidf_matrix: list[dict[str, float]] = []
        self._idf: dict[str, float] = {}
        self._is_indexed = False

    def load_variants(self, path: str) -> dict:
        """질문 변형 데이터를 JSON 파일에서 로드합니다.

        Args:
            path: question_variants.json 파일 경로.

        Returns:
            로드된 변형 데이터 딕셔너리.

        Raises:
            FileNotFoundError: 파일이 존재하지 않는 경우.
            json.JSONDecodeError: JSON 파싱 실패 시.
        """
        with open(path, "r", encoding="utf-8") as f:
            self.variants_data = json.load(f)

        self._build_index()
        return self.variants_data

    def _tokenize(self, text: str) -> list[str]:
        """한국어 텍스트를 간단한 토큰으로 분리합니다.

        공백, 조사, 어미 등을 기준으로 분리하고 불용어를 제거합니다.
        """
        text = text.strip().lower()
        # 물음표, 느낌표 등 구두점 제거
        text = re.sub(r"[?!.,;:~·…\"'(){}[\]<>]", " ", text)
        # 공백 기준 분리
        tokens = text.split()
        # 빈 토큰 제거
        tokens = [t for t in tokens if len(t) > 0]
        return tokens

    def _compute_tf(self, tokens: list[str]) -> dict[str, float]:
        """토큰 빈도(Term Frequency)를 계산합니다."""
        counter = Counter(tokens)
        total = len(tokens)
        if total == 0:
            return {}
        return {term: count / total for term, count in counter.items()}

    def _build_index(self):
        """TF-IDF 인덱스를 구축합니다."""
        if not self.variants_data:
            return

        self._documents = []
        self._doc_faq_map = []

        for item in self.variants_data.get("variants", []):
            faq_id = item["faq_id"]
            # 원본 질문 추가
            self._documents.append(item["original_question"])
            self._doc_faq_map.append(faq_id)
            # 변형 질문들 추가
            for variant in item.get("variants", []):
                self._documents.append(variant)
                self._doc_faq_map.append(faq_id)

        # IDF 계산
        n_docs = len(self._documents)
        doc_freq: dict[str, int] = {}

        tokenized_docs = []
        for doc in self._documents:
            tokens = self._tokenize(doc)
            tokenized_docs.append(tokens)
            unique_tokens = set(tokens)
            for token in unique_tokens:
                doc_freq[token] = doc_freq.get(token, 0) + 1

        self._idf = {}
        for term, df in doc_freq.items():
            self._idf[term] = math.log((n_docs + 1) / (df + 1)) + 1

        # TF-IDF 벡터 계산
        self._tfidf_matrix = []
        for tokens in tokenized_docs:
            tf = self._compute_tf(tokens)
            tfidf = {}
            for term, tf_val in tf.items():
                tfidf[term] = tf_val * self._idf.get(term, 1.0)
            self._tfidf_matrix.append(tfidf)

        self._is_indexed = True

    def _cosine_similarity(self, vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
        """두 TF-IDF 벡터 간 코사인 유사도를 계산합니다."""
        if not vec_a or not vec_b:
            return 0.0

        common_terms = set(vec_a.keys()) & set(vec_b.keys())
        dot_product = sum(vec_a[t] * vec_b[t] for t in common_terms)

        norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
        norm_b = math.sqrt(sum(v * v for v in vec_b.values()))

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot_product / (norm_a * norm_b)

    def _query_tfidf(self, query: str) -> dict[str, float]:
        """쿼리에 대한 TF-IDF 벡터를 계산합니다."""
        tokens = self._tokenize(query)
        tf = self._compute_tf(tokens)
        tfidf = {}
        for term, tf_val in tf.items():
            tfidf[term] = tf_val * self._idf.get(term, 1.0)
        return tfidf

    def find_match(self, query: str, threshold: float = 0.6) -> Optional[dict]:
        """사용자 쿼리에 가장 적합한 FAQ를 찾습니다.

        TF-IDF 코사인 유사도를 사용하여 모든 원본 질문과 변형 질문을
        대상으로 매칭합니다.

        Args:
            query: 사용자 입력 질문.
            threshold: 최소 유사도 임계값 (기본값: 0.6).

        Returns:
            매칭된 FAQ 정보 딕셔너리 또는 None.
            반환 형식: {"faq_id": str, "matched_question": str, "score": float}
        """
        if not self._is_indexed:
            return None

        query_vec = self._query_tfidf(query)
        if not query_vec:
            return None

        best_score = 0.0
        best_idx = -1

        for i, doc_vec in enumerate(self._tfidf_matrix):
            score = self._cosine_similarity(query_vec, doc_vec)
            if score > best_score:
                best_score = score
                best_idx = i

        if best_score < threshold:
            return None

        return {
            "faq_id": self._doc_faq_map[best_idx],
            "matched_question": self._documents[best_idx],
            "score": best_score,
        }

    def get_all_variants(self, faq_id: str) -> list[str]:
        """특정 FAQ ID의 모든 변형 질문을 반환합니다.

        Args:
            faq_id: FAQ 식별자 (예: "A", "B").

        Returns:
            변형 질문 리스트. FAQ ID가 없으면 빈 리스트.
        """
        if not self.variants_data:
            return []

        for item in self.variants_data.get("variants", []):
            if item["faq_id"] == faq_id:
                return list(item.get("variants", []))

        return []

    def add_variant(self, faq_id: str, variant: str) -> bool:
        """특정 FAQ에 새로운 변형 질문을 추가합니다.

        추가 후 TF-IDF 인덱스를 재구축합니다.

        Args:
            faq_id: FAQ 식별자.
            variant: 추가할 변형 질문.

        Returns:
            추가 성공 여부.
        """
        if not self.variants_data:
            return False

        for item in self.variants_data.get("variants", []):
            if item["faq_id"] == faq_id:
                if variant not in item.get("variants", []):
                    item.setdefault("variants", []).append(variant)
                    self._build_index()
                return True

        return False
