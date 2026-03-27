"""미매칭 질문 기반 FAQ 자동 추천 모듈.

미매칭된 사용자 질문을 클러스터링하여 FAQ 추가 후보를 추천한다.
"""

import math
from collections import Counter

from src.classifier import classify_query


class FAQRecommender:
    """미매칭 질문을 분석하여 FAQ 추가 후보를 추천하는 클래스."""

    def __init__(self, logger_db):
        """ChatLogger 인스턴스를 받아 초기화한다.

        Args:
            logger_db: ChatLogger 인스턴스 (미매칭 질문 조회용).
        """
        self.logger_db = logger_db

    def get_recommendations(self, top_k: int = 10) -> list[dict]:
        """미매칭 질문을 클러스터링하여 FAQ 추가 후보를 추천한다.

        미매칭 질문들을 TF-IDF 유사도로 그룹핑한 뒤,
        빈도순으로 정렬하여 상위 k개를 반환한다.

        Args:
            top_k: 반환할 최대 추천 수.

        Returns:
            추천 리스트. 각 항목 형식:
            {
                "suggested_question": "대표 질문",
                "frequency": 빈도,
                "similar_queries": [유사 질문 리스트],
                "suggested_category": "추천 카테고리"
            }
        """
        # 미매칭 질문 조회 (넉넉하게)
        unmatched = self.logger_db.get_unmatched_queries(limit=200)
        if not unmatched:
            return []

        queries = [item["query"] for item in unmatched]

        # 중복 질문 빈도 계산
        query_counts = Counter(queries)

        # 유니크 질문만 추출
        unique_queries = list(query_counts.keys())
        if not unique_queries:
            return []

        # TF-IDF 기반 클러스터링
        clusters = self._cluster_queries(unique_queries, threshold=0.3)

        # 클러스터별 추천 생성
        recommendations = []
        for cluster in clusters:
            # 클러스터 내 총 빈도 계산
            total_freq = sum(query_counts[q] for q in cluster)

            # 대표 질문: 가장 빈도 높은 질문
            representative = max(cluster, key=lambda q: query_counts[q])

            # 카테고리 추천
            suggested_category = classify_query(representative)[0]

            recommendations.append({
                "suggested_question": representative,
                "frequency": total_freq,
                "similar_queries": sorted(cluster, key=lambda q: -query_counts[q]),
                "suggested_category": suggested_category,
            })

        # 빈도순 정렬
        recommendations.sort(key=lambda r: -r["frequency"])
        return recommendations[:top_k]

    def generate_faq_draft(self, query_cluster: list[str]) -> dict:
        """질문 클러스터로부터 FAQ JSON 초안을 자동 생성한다.

        Args:
            query_cluster: 유사 질문 리스트.

        Returns:
            FAQ 초안 딕셔너리:
            {
                "id": "AUTO_NNN",
                "category": "추천 카테고리",
                "question": "대표 질문",
                "keywords": [추출된 키워드],
                "answer": "(답변 초안을 작성해 주세요.)",
                "legal_basis": [],
                "source_queries": [원본 질문 리스트]
            }
        """
        if not query_cluster:
            return {}

        # 대표 질문 선택 (가장 긴 질문이 보통 가장 구체적)
        representative = max(query_cluster, key=len)

        # 카테고리 추천
        suggested_category = classify_query(representative)[0]

        # 키워드 추출: 모든 질문에서 토큰화하여 빈도 상위 추출
        keywords = self._extract_keywords(query_cluster)

        return {
            "id": f"AUTO_{hash(representative) % 10000:04d}",
            "category": suggested_category,
            "question": representative,
            "keywords": keywords,
            "answer": "(답변 초안을 작성해 주세요.)",
            "legal_basis": [],
            "source_queries": query_cluster,
        }

    def _tokenize(self, text: str) -> list[str]:
        """간단한 공백 기반 토크나이즈."""
        tokens = []
        for token in text.strip().lower().split():
            token = token.strip("?.,!·()\"'")
            if token and len(token) > 1:
                tokens.append(token)
        return tokens

    def _compute_tfidf_vectors(
        self, documents: list[list[str]]
    ) -> tuple[list[dict], dict]:
        """문서 리스트에 대한 TF-IDF 벡터를 계산한다."""
        num_docs = len(documents)
        if num_docs == 0:
            return [], {}

        # DF 계산
        df: dict[str, int] = {}
        for doc in documents:
            for token in set(doc):
                df[token] = df.get(token, 0) + 1

        # IDF 계산
        idf: dict[str, float] = {}
        for token, doc_freq in df.items():
            idf[token] = math.log((num_docs + 1) / (doc_freq + 1)) + 1

        # TF-IDF 벡터
        vectors = []
        for doc in documents:
            tf = Counter(doc)
            doc_len = len(doc) if doc else 1
            vector: dict[str, float] = {}
            for token, count in tf.items():
                tf_val = count / doc_len
                idf_val = idf.get(token, 1.0)
                vector[token] = tf_val * idf_val
            vectors.append(vector)

        return vectors, idf

    def _cosine_similarity(
        self, vec1: dict[str, float], vec2: dict[str, float]
    ) -> float:
        """두 벡터의 코사인 유사도를 계산한다."""
        if not vec1 or not vec2:
            return 0.0

        dot = sum(vec1[t] * vec2[t] for t in vec1 if t in vec2)
        if dot == 0.0:
            return 0.0

        norm1 = math.sqrt(sum(v * v for v in vec1.values()))
        norm2 = math.sqrt(sum(v * v for v in vec2.values()))
        if norm1 == 0.0 or norm2 == 0.0:
            return 0.0

        return dot / (norm1 * norm2)

    def _cluster_queries(
        self, queries: list[str], threshold: float = 0.3
    ) -> list[list[str]]:
        """질문들을 TF-IDF 유사도 기반으로 클러스터링한다.

        단순 탐욕 클러스터링: 각 질문을 기존 클러스터와 비교하여
        threshold 이상이면 해당 클러스터에 추가, 아니면 새 클러스터 생성.

        Args:
            queries: 질문 리스트.
            threshold: 유사도 임계값.

        Returns:
            클러스터 리스트 (각 클러스터는 질문 리스트).
        """
        if not queries:
            return []

        documents = [self._tokenize(q) for q in queries]
        vectors, _ = self._compute_tfidf_vectors(documents)

        clusters: list[list[int]] = []  # 인덱스 기반
        cluster_centers: list[dict[str, float]] = []

        for i, vec in enumerate(vectors):
            best_cluster = -1
            best_sim = 0.0

            for ci, center in enumerate(cluster_centers):
                sim = self._cosine_similarity(vec, center)
                if sim > best_sim:
                    best_sim = sim
                    best_cluster = ci

            if best_sim >= threshold and best_cluster >= 0:
                clusters[best_cluster].append(i)
            else:
                clusters.append([i])
                cluster_centers.append(vec)

        return [[queries[i] for i in cluster] for cluster in clusters]

    def _extract_keywords(self, queries: list[str], top_k: int = 5) -> list[str]:
        """질문 리스트에서 빈도 기반 키워드를 추출한다."""
        # 불용어
        stopwords = {
            "있나요", "어떻게", "무엇", "하나요", "인가요", "되나요",
            "할까요", "수", "있습니까", "합니까", "대해",
            "것은", "경우", "때문", "관련", "질문", "궁금",
        }

        all_tokens: list[str] = []
        for q in queries:
            tokens = self._tokenize(q)
            all_tokens.extend(t for t in tokens if t not in stopwords)

        counts = Counter(all_tokens)
        return [token for token, _ in counts.most_common(top_k)]
