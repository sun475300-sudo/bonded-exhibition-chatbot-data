"""Conversation summarization engine for the bonded exhibition chatbot.

Generates session summaries, extracts key points, detects categories,
and provides batch summarization for admin reports.
"""

import math
import re
import time
from collections import Counter
from datetime import datetime

from src.classifier import CATEGORY_KEYWORDS, classify_query
from src.escalation import check_escalation


# Common Korean stop words to exclude from keyword extraction
_STOP_WORDS = frozenset([
    "이", "가", "은", "는", "을", "를", "에", "에서", "의", "로", "으로",
    "와", "과", "도", "만", "까지", "부터", "보다", "같은", "등", "및",
    "또는", "그리고", "하지만", "그러나", "그래서", "때문에", "위해",
    "것", "수", "때", "중", "후", "전", "더", "좀", "잘", "못", "안",
    "네", "예", "아니요", "아니오", "the", "a", "an", "is", "are", "was",
    "were", "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "may", "might", "shall",
    "can", "and", "or", "but", "if", "for", "to", "of", "in", "on",
    "at", "by", "with", "from", "that", "this", "it", "not", "what",
    "how", "when", "where", "who", "which", "there", "here",
])

# Category display names
CATEGORY_NAMES = {
    "GENERAL": "일반/보세전시장 개요",
    "LICENSE": "특허/운영",
    "IMPORT_EXPORT": "반입/반출",
    "EXHIBITION": "전시/장치",
    "SALES": "판매/직매",
    "SAMPLE": "견본품/샘플",
    "FOOD_TASTING": "시식/식품",
    "DOCUMENTS": "서류/신고",
    "PENALTIES": "벌칙/제재",
    "CONTACT": "문의/연락처",
}


class ConversationKeywordExtractor:
    """Keyword extraction using TF-IDF-like scoring without external NLP libs."""

    def __init__(self):
        self._token_pattern = re.compile(r"[가-힣a-zA-Z]{2,}")

    def _tokenize(self, text: str) -> list[str]:
        """Split text into tokens, filtering stop words."""
        tokens = self._token_pattern.findall(text.lower())
        return [t for t in tokens if t not in _STOP_WORDS and len(t) >= 2]

    def extract_keywords(self, text: str, top_n: int = 5) -> list[dict]:
        """Extract top keywords from text using TF-IDF-like scoring.

        Uses term frequency in the document weighted by inverse document
        frequency approximated from the token's length and domain relevance.

        Args:
            text: Input text to extract keywords from.
            top_n: Number of top keywords to return.

        Returns:
            List of dicts with 'keyword' and 'score' keys.
        """
        if not text or not text.strip():
            return []

        tokens = self._tokenize(text)
        if not tokens:
            return []

        total = len(tokens)
        tf = Counter(tokens)

        scored = {}
        for token, count in tf.items():
            term_freq = count / total
            # IDF approximation: longer/rarer tokens score higher
            idf = 1.0 + math.log(1 + len(token))
            # Boost domain-relevant terms
            domain_boost = 1.0
            for _cat, keywords in CATEGORY_KEYWORDS.items():
                if any(token in kw.lower() or kw.lower() in token for kw in keywords):
                    domain_boost = 2.0
                    break
            scored[token] = term_freq * idf * domain_boost

        sorted_keywords = sorted(scored.items(), key=lambda x: x[1], reverse=True)
        return [
            {"keyword": kw, "score": round(score, 4)}
            for kw, score in sorted_keywords[:top_n]
        ]

    def extract_topics(self, messages: list[dict]) -> list[dict]:
        """Extract topics from a list of conversation messages.

        Groups related keywords into topic clusters based on category matching.

        Args:
            messages: List of message dicts with 'query' and/or 'answer' keys.

        Returns:
            List of topic dicts with 'topic', 'category', and 'relevance' keys.
        """
        if not messages:
            return []

        all_text = " ".join(
            m.get("query", "") + " " + m.get("answer", "")
            for m in messages
        )

        keywords = self.extract_keywords(all_text, top_n=20)
        if not keywords:
            return []

        # Cluster keywords by category
        category_scores: dict[str, float] = {}
        category_keywords: dict[str, list[str]] = {}

        for kw_info in keywords:
            kw = kw_info["keyword"]
            score = kw_info["score"]
            matched_cat = None

            for cat, cat_keywords in CATEGORY_KEYWORDS.items():
                if any(kw in ck.lower() or ck.lower() in kw for ck in cat_keywords):
                    matched_cat = cat
                    break

            if matched_cat is None:
                matched_cat = "GENERAL"

            category_scores[matched_cat] = category_scores.get(matched_cat, 0) + score
            if matched_cat not in category_keywords:
                category_keywords[matched_cat] = []
            category_keywords[matched_cat].append(kw)

        topics = []
        for cat, score in sorted(category_scores.items(), key=lambda x: x[1], reverse=True):
            topics.append({
                "topic": CATEGORY_NAMES.get(cat, cat),
                "category": cat,
                "keywords": category_keywords.get(cat, [])[:5],
                "relevance": round(score, 4),
            })

        return topics


class ConversationSummarizer:
    """Generates summaries and reports from conversation session data."""

    def __init__(self, session_manager):
        """Initialize with a SessionManager instance.

        Args:
            session_manager: SessionManager that holds session data.
        """
        self.session_manager = session_manager
        self.keyword_extractor = ConversationKeywordExtractor()

    def summarize_session(self, session_id: str) -> dict | None:
        """Generate a summary from session history.

        Args:
            session_id: The session ID to summarize.

        Returns:
            Summary dict or None if session not found.
        """
        session = self.session_manager.get_session(session_id)
        if session is None:
            return None

        messages = session.history
        if not messages:
            return {
                "session_id": session_id,
                "main_topic": "대화 없음",
                "questions_asked": 0,
                "categories_covered": [],
                "key_answers": [],
                "escalation_status": False,
                "satisfaction_score": 0.0,
                "duration_seconds": 0.0,
                "keywords": [],
            }

        categories = self.get_categories_discussed(messages)
        key_points = self.extract_key_points(messages)
        escalation_points = self.get_escalation_points(messages)
        keywords = self.keyword_extractor.extract_keywords(
            " ".join(m.get("query", "") for m in messages), top_n=5
        )

        main_topic = categories[0] if categories else "GENERAL"
        duration = session.last_active - session.created_at

        # Simple satisfaction heuristic: based on answer coverage
        satisfaction = self._estimate_satisfaction(messages, escalation_points)

        return {
            "session_id": session_id,
            "main_topic": CATEGORY_NAMES.get(main_topic, main_topic),
            "questions_asked": len(messages),
            "categories_covered": [
                CATEGORY_NAMES.get(c, c) for c in categories
            ],
            "key_answers": key_points,
            "escalation_status": len(escalation_points) > 0,
            "escalation_count": len(escalation_points),
            "satisfaction_score": satisfaction,
            "duration_seconds": round(duration, 1),
            "keywords": keywords,
        }

    def extract_key_points(self, messages: list[dict]) -> list[dict]:
        """Extract main topics and decisions from conversation.

        Args:
            messages: List of message dicts with 'query' and 'answer' keys.

        Returns:
            List of key point dicts with 'query', 'category', and 'summary'.
        """
        if not messages:
            return []

        key_points = []
        for msg in messages:
            query = msg.get("query", "")
            answer = msg.get("answer", "")
            if not query:
                continue

            categories = classify_query(query)
            primary_cat = categories[0] if categories else "GENERAL"

            # Truncate answer for summary
            summary = answer[:100] + "..." if len(answer) > 100 else answer

            key_points.append({
                "query": query,
                "category": primary_cat,
                "category_name": CATEGORY_NAMES.get(primary_cat, primary_cat),
                "summary": summary,
            })

        return key_points

    def get_categories_discussed(self, messages: list[dict]) -> list[str]:
        """Return unique categories discussed in the conversation.

        Args:
            messages: List of message dicts with 'query' key.

        Returns:
            List of unique category codes, ordered by frequency (most common first).
        """
        if not messages:
            return []

        category_counts: Counter = Counter()
        for msg in messages:
            query = msg.get("query", "")
            if not query:
                continue
            categories = classify_query(query)
            for cat in categories:
                category_counts[cat] += 1

        return [cat for cat, _count in category_counts.most_common()]

    def get_escalation_points(self, messages: list[dict]) -> list[dict]:
        """Return escalation events in the conversation.

        Args:
            messages: List of message dicts with 'query' key.

        Returns:
            List of escalation event dicts with 'turn', 'query', and 'rule'.
        """
        if not messages:
            return []

        escalations = []
        for i, msg in enumerate(messages):
            query = msg.get("query", "")
            if not query:
                continue
            rule = check_escalation(query)
            if rule is not None:
                escalations.append({
                    "turn": i + 1,
                    "query": query,
                    "rule": rule,
                })

        return escalations

    def generate_session_report(self, session_id: str) -> dict | None:
        """Generate a comprehensive session report.

        Args:
            session_id: The session ID to report on.

        Returns:
            Comprehensive report dict or None if session not found.
        """
        session = self.session_manager.get_session(session_id)
        if session is None:
            return None

        summary = self.summarize_session(session_id)
        if summary is None:
            return None

        messages = session.history
        topics = self.keyword_extractor.extract_topics(messages)
        escalation_points = self.get_escalation_points(messages)

        return {
            "session_id": session_id,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": summary,
            "topics": topics,
            "escalation_details": escalation_points,
            "turn_count": len(messages),
            "created_at": session.created_at,
            "last_active": session.last_active,
            "has_pending": session.has_pending(),
            "confirmed_items": session.confirmed,
        }

    def summarize_batch(self, session_ids: list[str]) -> list[dict]:
        """Batch summarization for multiple sessions.

        Args:
            session_ids: List of session IDs to summarize.

        Returns:
            List of summary dicts (skips sessions not found).
        """
        results = []
        for sid in session_ids:
            summary = self.summarize_session(sid)
            if summary is not None:
                results.append(summary)
        return results

    def _estimate_satisfaction(
        self, messages: list[dict], escalation_points: list[dict]
    ) -> float:
        """Estimate session satisfaction score (0.0 - 1.0).

        Heuristic based on:
        - Number of turns (more turns without escalation = lower satisfaction)
        - Escalation presence (reduces score)
        - Answer length (longer answers suggest better coverage)
        """
        if not messages:
            return 0.0

        num_turns = len(messages)
        num_escalations = len(escalation_points)

        # Base score starts at 0.8
        score = 0.8

        # Penalize escalations
        score -= num_escalations * 0.2

        # Penalize very long sessions (potential frustration)
        if num_turns > 10:
            score -= 0.1
        elif num_turns > 5:
            score -= 0.05

        # Boost for answer quality (non-empty answers)
        answers_with_content = sum(
            1 for m in messages if len(m.get("answer", "")) > 20
        )
        if num_turns > 0:
            answer_ratio = answers_with_content / num_turns
            score += answer_ratio * 0.2

        return round(max(0.0, min(1.0, score)), 2)
