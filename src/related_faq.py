"""관련 FAQ 추천 모듈.

보세전시장 챗봇에서 현재 FAQ와 관련된 질문을 추천한다.
토큰 오버랩(Jaccard 유사도)을 기반으로 FAQ 간 유사도를 계산한다.
"""

import string


def _tokenize(text: str) -> set[str]:
    """공백 기반 토크나이저. 소문자 변환 및 구두점 제거.

    Args:
        text: 토크나이즈할 텍스트.

    Returns:
        고유 토큰의 집합.
    """
    tokens: set[str] = set()
    for token in text.lower().split():
        stripped = token.strip(string.punctuation)
        if stripped:
            tokens.add(stripped)
    return tokens


def _jaccard_similarity(set1: set[str], set2: set[str]) -> float:
    """두 집합 간의 Jaccard 유사도 계수를 계산한다.

    Args:
        set1: 첫 번째 토큰 집합.
        set2: 두 번째 토큰 집합.

    Returns:
        Jaccard 유사도 (0.0 ~ 1.0). 두 집합이 모두 비어 있으면 0.0.
    """
    if not set1 and not set2:
        return 0.0
    intersection = set1 & set2
    union = set1 | set2
    return len(intersection) / len(union)


class RelatedFAQFinder:
    """FAQ 항목 간 유사도를 기반으로 관련 질문을 추천하는 클래스."""

    def __init__(self, faq_items: list[dict]):
        """FAQ 데이터로 유사도 행렬을 사전 계산한다.

        각 FAQ 항목의 keywords와 question 토큰을 결합하여
        Jaccard 유사도 행렬을 구성한다.

        Args:
            faq_items: FAQ 항목 리스트. 각 항목에 id, question, category 필드 필요.
                       keywords 필드는 선택사항 (리스트[str] 또는 쉼표 구분 문자열).
        """
        self.faq_items = faq_items
        self._id_to_index: dict[str, int] = {}
        self._token_sets: list[set[str]] = []
        self._similarity_matrix: list[list[float]] = []

        self._build_token_sets()
        self._build_similarity_matrix()

    def _build_token_sets(self) -> None:
        """각 FAQ 항목에 대한 토큰 집합을 구성한다."""
        for i, item in enumerate(self.faq_items):
            faq_id = item.get("id", "")
            self._id_to_index[faq_id] = i

            tokens: set[str] = set()

            # question 토큰 추가
            question = item.get("question", "")
            tokens |= _tokenize(question)

            # keywords 토큰 추가
            keywords = item.get("keywords", [])
            if isinstance(keywords, str):
                for kw in keywords.split(","):
                    tokens |= _tokenize(kw.strip())
            elif isinstance(keywords, list):
                for kw in keywords:
                    tokens |= _tokenize(str(kw))

            self._token_sets.append(tokens)

    def _build_similarity_matrix(self) -> None:
        """모든 FAQ 쌍 간의 Jaccard 유사도 행렬을 구성한다."""
        n = len(self.faq_items)
        self._similarity_matrix = [[0.0] * n for _ in range(n)]

        for i in range(n):
            for j in range(i + 1, n):
                sim = _jaccard_similarity(self._token_sets[i], self._token_sets[j])
                self._similarity_matrix[i][j] = sim
                self._similarity_matrix[j][i] = sim

    def _format_result(self, index: int, similarity: float) -> dict:
        """FAQ 항목을 결과 딕셔너리로 변환한다.

        Args:
            index: FAQ 항목의 인덱스.
            similarity: 유사도 점수.

        Returns:
            결과 딕셔너리.
        """
        item = self.faq_items[index]
        return {
            "id": item.get("id", ""),
            "question": item.get("question", ""),
            "category": item.get("category", ""),
            "similarity": round(similarity, 4),
        }

    def find_related(self, faq_id: str, top_k: int = 3) -> list[dict]:
        """주어진 FAQ ID와 가장 관련된 FAQ 항목을 반환한다.

        사전 계산된 유사도 행렬을 사용하여 가장 유사한 항목을 찾는다.

        Args:
            faq_id: 기준 FAQ 항목의 ID.
            top_k: 반환할 최대 결과 수.

        Returns:
            관련 FAQ 리스트. 각 항목:
            {"id": "...", "question": "...", "category": "...", "similarity": float}
            유사도 내림차순 정렬. faq_id를 찾을 수 없으면 빈 리스트 반환.
        """
        if faq_id not in self._id_to_index:
            return []

        idx = self._id_to_index[faq_id]
        similarities = self._similarity_matrix[idx]

        scored: list[tuple[int, float]] = []
        for i, sim in enumerate(similarities):
            if i != idx and sim > 0.0:
                scored.append((i, sim))

        scored.sort(key=lambda x: x[1], reverse=True)

        results: list[dict] = []
        for i, sim in scored[:top_k]:
            results.append(self._format_result(i, sim))
        return results

    def find_related_by_query(
        self, query: str, current_faq_id: str | None = None, top_k: int = 3
    ) -> list[dict]:
        """쿼리 텍스트를 기반으로 관련 FAQ를 찾는다.

        매칭된 FAQ가 아닌 원본 쿼리 텍스트와의 유사도로 추천한다.
        current_faq_id가 주어지면 해당 항목은 결과에서 제외한다.

        Args:
            query: 사용자 쿼리 텍스트.
            current_faq_id: 제외할 현재 FAQ ID (선택사항).
            top_k: 반환할 최대 결과 수.

        Returns:
            관련 FAQ 리스트. 유사도 내림차순 정렬.
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        exclude_idx: int | None = None
        if current_faq_id is not None and current_faq_id in self._id_to_index:
            exclude_idx = self._id_to_index[current_faq_id]

        scored: list[tuple[int, float]] = []
        for i, token_set in enumerate(self._token_sets):
            if i == exclude_idx:
                continue
            sim = _jaccard_similarity(query_tokens, token_set)
            if sim > 0.0:
                scored.append((i, sim))

        scored.sort(key=lambda x: x[1], reverse=True)

        results: list[dict] = []
        for i, sim in scored[:top_k]:
            results.append(self._format_result(i, sim))
        return results

    def get_category_neighbors(self, faq_id: str, top_k: int = 3) -> list[dict]:
        """동일 카테고리의 다른 FAQ 항목을 반환한다.

        같은 카테고리 내에서 유사도가 높은 순으로 반환한다.

        Args:
            faq_id: 기준 FAQ 항목의 ID.
            top_k: 반환할 최대 결과 수.

        Returns:
            같은 카테고리의 FAQ 리스트. 유사도 내림차순 정렬.
            faq_id를 찾을 수 없으면 빈 리스트 반환.
        """
        if faq_id not in self._id_to_index:
            return []

        idx = self._id_to_index[faq_id]
        current_item = self.faq_items[idx]
        category = current_item.get("category", "")

        similarities = self._similarity_matrix[idx]

        scored: list[tuple[int, float]] = []
        for i, item in enumerate(self.faq_items):
            if i == idx:
                continue
            if item.get("category", "") == category:
                scored.append((i, similarities[i]))

        scored.sort(key=lambda x: x[1], reverse=True)

        results: list[dict] = []
        for i, sim in scored[:top_k]:
            results.append(self._format_result(i, sim))
        return results
