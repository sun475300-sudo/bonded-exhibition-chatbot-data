"""BM25 랭킹 엔진 모듈.

TF-IDF 대안/보완으로 사용할 수 있는 BM25 기반 FAQ 랭킹 엔진.
외부 라이브러리 없이 순수 Python으로 구현한다.
"""

import math
from collections import Counter


class BM25Ranker:
    """BM25 기반 FAQ 랭킹 클래스.

    FAQ 항목의 질문·키워드·답변 첫 문장으로 문서를 구성하고,
    BM25 스코어를 기반으로 사용자 질문에 대한 관련 FAQ를 랭킹한다.
    """

    def __init__(self, faq_items: list[dict], tokenizer=None) -> None:
        """FAQ 데이터로 BM25 인덱스를 구축한다.

        Args:
            faq_items: FAQ 항목 리스트. 각 항목에 question, keywords, answer 필드 필요.
            tokenizer: 토크나이저 객체. tokenize(text) 메서드를 가져야 한다.
                       None이면 공백 기반 분리를 사용한다.
        """
        self.faq_items = faq_items
        self.tokenizer = tokenizer
        self.k1: float = 1.5
        self.b: float = 0.75

        self.documents: list[list[str]] = []
        self.doc_lengths: list[int] = []
        self.avgdl: float = 0.0
        self.doc_freqs: dict[str, int] = {}
        self.idf: dict[str, float] = {}
        self.doc_term_freqs: list[Counter] = []

        self._build_index()

    def _tokenize(self, text: str) -> list[str]:
        """텍스트를 토큰으로 분리한다.

        외부 토크나이저가 지정되어 있으면 해당 토크나이저를 사용하고,
        아니면 공백 기반으로 분리한다.

        Args:
            text: 토크나이즈할 텍스트.

        Returns:
            토큰 리스트.
        """
        if self.tokenizer is not None:
            return self.tokenizer.tokenize(text)

        tokens = []
        for token in text.strip().lower().split():
            token = token.strip("?.,!·()\"'~:;")
            if token and len(token) > 1:
                tokens.append(token)
        return tokens

    @staticmethod
    def _first_sentence(text: str) -> str:
        """텍스트에서 첫 번째 문장을 추출한다.

        Args:
            text: 원본 텍스트.

        Returns:
            첫 번째 문장 문자열.
        """
        for sep in (".", "。", "\n"):
            idx = text.find(sep)
            if idx != -1:
                return text[: idx + 1]
        return text

    def _build_document(self, item: dict) -> list[str]:
        """FAQ 항목에서 문서(토큰 리스트)를 생성한다.

        문서 = 질문 + 키워드 + 답변 첫 문장

        Args:
            item: FAQ 항목 딕셔너리.

        Returns:
            토큰 리스트.
        """
        question = item.get("question", "")
        keywords = item.get("keywords", [])
        answer = item.get("answer", "")

        text_parts = [question]
        if isinstance(keywords, list):
            text_parts.extend(keywords)
        elif isinstance(keywords, str):
            text_parts.append(keywords)

        if answer:
            text_parts.append(self._first_sentence(answer))

        combined = " ".join(text_parts)
        return self._tokenize(combined)

    def _build_index(self) -> None:
        """전체 FAQ로부터 BM25 인덱스를 구축한다."""
        # 문서 구축
        for item in self.faq_items:
            tokens = self._build_document(item)
            self.documents.append(tokens)
            self.doc_lengths.append(len(tokens))
            self.doc_term_freqs.append(Counter(tokens))

        # 평균 문서 길이
        total = sum(self.doc_lengths)
        self.avgdl = total / len(self.documents) if self.documents else 1.0

        # 문서 빈도 계산
        for tokens in self.documents:
            seen: set[str] = set()
            for token in tokens:
                if token not in seen:
                    self.doc_freqs[token] = self.doc_freqs.get(token, 0) + 1
                    seen.add(token)

        # IDF 계산
        self._compute_idf()

    def _compute_idf(self) -> None:
        """BM25 IDF를 계산한다.

        공식: log((N - n + 0.5) / (n + 0.5) + 1)
        여기서 N = 전체 문서 수, n = 해당 용어가 등장한 문서 수.
        """
        n_docs = len(self.documents)
        for term, df in self.doc_freqs.items():
            numerator = n_docs - df + 0.5
            denominator = df + 0.5
            self.idf[term] = math.log(numerator / denominator + 1.0)

    def _score_document(self, query_tokens: list[str], doc_idx: int) -> float:
        """쿼리 토큰에 대한 특정 문서의 BM25 스코어를 계산한다.

        Args:
            query_tokens: 쿼리 토큰 리스트.
            doc_idx: 문서 인덱스.

        Returns:
            BM25 스코어.
        """
        score = 0.0
        doc_len = self.doc_lengths[doc_idx]
        term_freqs = self.doc_term_freqs[doc_idx]

        for token in query_tokens:
            if token not in self.idf:
                continue

            tf = term_freqs.get(token, 0)
            if tf == 0:
                continue

            idf = self.idf[token]
            # BM25 TF 정규화
            numerator = tf * (self.k1 + 1.0)
            denominator = tf + self.k1 * (1.0 - self.b + self.b * doc_len / self.avgdl)
            score += idf * (numerator / denominator)

        return score

    def rank(
        self,
        query: str,
        category: str | None = None,
        top_k: int = 5,
    ) -> list[dict]:
        """사용자 질문에 대해 FAQ를 BM25 스코어 기준으로 랭킹한다.

        Args:
            query: 사용자 질문 텍스트.
            category: 카테고리 필터. 지정 시 해당 카테고리 FAQ만 대상으로 한다.
            top_k: 반환할 최대 결과 수 (기본값 5).

        Returns:
            [{"item": faq_item, "score": float}] 형태의 리스트.
            스코어 내림차순 정렬.
        """
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scores: list[tuple[int, float]] = []

        for idx, item in enumerate(self.faq_items):
            # 카테고리 필터
            if category is not None:
                item_category = item.get("category", "")
                if item_category != category:
                    continue

            score = self._score_document(query_tokens, idx)
            if score > 0.0:
                scores.append((idx, score))

        # 스코어 내림차순 정렬
        scores.sort(key=lambda x: x[1], reverse=True)

        results: list[dict] = []
        for idx, score in scores[:top_k]:
            results.append({"item": self.faq_items[idx], "score": score})

        return results
