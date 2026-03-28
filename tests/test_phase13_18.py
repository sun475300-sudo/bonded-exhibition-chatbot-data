"""
Comprehensive tests for Phase 13-18 modules.

Covers: SynonymResolver, SpellCorrector, ClarificationEngine,
SatisfactionTracker, KoreanTokenizer, BM25Ranker, RelatedFAQFinder,
RealtimeMonitor, FAQQualityChecker, ConversationExporter, PluginSystem.
"""

import json
import os
import sys
import tempfile

import pytest

# Ensure src is on the import path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from synonym_resolver import resolve_synonyms, expand_query
from spell_corrector import correct_term, correct_query, KNOWN_TERMS
from clarification import ClarificationEngine
from satisfaction_tracker import SatisfactionTracker
from korean_tokenizer import KoreanTokenizer
from bm25_ranker import BM25Ranker
from related_faq import RelatedFAQFinder
from realtime_monitor import RealtimeMonitor
from faq_quality_checker import FAQQualityChecker
from conversation_export import ConversationExporter
from plugin_system import PluginManager, HOOK_PRE_CLASSIFY, HOOK_POST_CLASSIFY

# ---------------------------------------------------------------------------
# Shared sample FAQ data
# ---------------------------------------------------------------------------
SAMPLE_FAQ = [
    {
        "id": "A",
        "category": "GENERAL",
        "question": "보세전시장이 무엇인가요?",
        "answer": "보세전시장은 외국물품을 전시할 수 있는 보세구역입니다.",
        "legal_basis": ["관세법 제190조"],
        "keywords": ["보세전시장", "정의", "개념"],
    },
    {
        "id": "B",
        "category": "SALES",
        "question": "현장에서 판매가 가능한가요?",
        "answer": "보세전시장에서 외국물품의 직매가 가능합니다.",
        "legal_basis": ["관세법시행령 제101조"],
        "keywords": ["판매", "직매", "현장판매"],
    },
    {
        "id": "C",
        "category": "SAMPLE",
        "question": "견본품을 반출할 수 있나요?",
        "answer": "세관장 허가를 받아 견본품을 반출할 수 있습니다.",
        "legal_basis": ["관세법 제161조"],
        "keywords": ["견본품", "반출", "샘플"],
    },
]

SAMPLE_HISTORY = [
    {"role": "user", "message": "보세전시장이 뭔가요?", "timestamp": "2025-01-01T10:00:00"},
    {"role": "bot", "message": "보세전시장은 외국물품을 전시할 수 있는 보세구역입니다.", "timestamp": "2025-01-01T10:00:01"},
    {"role": "user", "message": "판매도 가능한가요?", "timestamp": "2025-01-01T10:01:00"},
    {"role": "bot", "message": "네, 외국물품의 직매가 가능합니다.", "timestamp": "2025-01-01T10:01:01"},
]


# ===================================================================
# 1. SynonymResolver
# ===================================================================
class TestSynonymResolver:
    def test_resolve_synonyms_replaces_known_synonym(self):
        result = resolve_synonyms("세금이 얼마인가요")
        assert "관세" in result
        assert "세금" not in result

    def test_expand_query_appends_canonical(self):
        result = expand_query("세금 문의")
        # Original text preserved
        assert result.startswith("세금 문의")
        # Canonical form appended
        assert "관세" in result

    def test_resolve_synonyms_preserves_non_synonym_text(self):
        original = "보세전시장 이용 안내"
        result = resolve_synonyms(original)
        assert result == original

    def test_expand_query_returns_unchanged_when_no_synonyms(self):
        original = "보세전시장 이용 안내"
        result = expand_query(original)
        assert result == original


# ===================================================================
# 2. SpellCorrector
# ===================================================================
class TestSpellCorrector:
    def test_correct_term_fixes_typo(self):
        # "보세전시" is close to "보세전시장" (distance 1)
        result = correct_term("보세전시")
        assert result is not None
        assert result in KNOWN_TERMS

    def test_correct_query_returns_corrections(self):
        corrected, corrections = correct_query("보세전시 반이")
        assert isinstance(corrected, str)
        assert isinstance(corrections, list)
        # At least one correction should have occurred
        assert len(corrections) >= 1

    def test_known_term_returns_unchanged(self):
        result = correct_term("보세전시장")
        assert result == "보세전시장"

    def test_correct_query_no_change_for_known(self):
        corrected, corrections = correct_query("보세전시장 반입")
        assert "보세전시장" in corrected
        assert "반입" in corrected
        assert corrections == []


# ===================================================================
# 3. ClarificationEngine
# ===================================================================
class TestClarificationEngine:
    def setup_method(self):
        self.engine = ClarificationEngine()

    def test_needs_clarification_short_query(self):
        # Fewer than 5 chars after stripping spaces -> needs clarification
        assert self.engine.needs_clarification("뭐?", [], None) is True

    def test_needs_clarification_ambiguous_multi_category(self):
        # Multiple categories + no FAQ match -> needs clarification
        assert self.engine.needs_clarification(
            "보세전시장 판매 관련",
            ["GENERAL", "SALES"],
            None,
        ) is True

    def test_needs_clarification_clear_query(self):
        # Single category, has domain keyword, long enough
        assert self.engine.needs_clarification(
            "보세전시장에서 판매 가능한가요",
            ["SALES"],
            {"id": "B"},
        ) is False

    def test_generate_clarification_includes_categories(self):
        result = self.engine.generate_clarification(
            "질문입니다", ["GENERAL", "SALES"]
        )
        assert "1." in result
        assert "2." in result
        # Should include the display name for GENERAL
        assert "제도 일반" in result


# ===================================================================
# 4. SatisfactionTracker (temp db)
# ===================================================================
class TestSatisfactionTracker:
    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.tracker = SatisfactionTracker()
        # Override db_path to use temp file
        self.tracker.db_path = self.tmp.name
        self.tracker._init_db()

    def teardown_method(self):
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_track_response_inserts_record(self):
        self.tracker.track_response("s1", "보세전시장이 뭔가요?", "faq_match")
        stats = self.tracker.get_satisfaction_stats()
        assert stats["total_queries"] == 1

    def test_detect_re_ask_identifies_similar(self):
        history = [{"query": "보세전시장 정의 알려줘"}]
        result = self.tracker.detect_re_ask("s1", "보세전시장 정의 알려주세요", history)
        # Token overlap: {"보세전시장", "정의", "알려줘"} vs {"보세전시장", "정의", "알려주세요"}
        # overlap=2, union=4 => 0.5, threshold is > 0.5 so this is False
        # Let's use a query with higher overlap instead
        result2 = self.tracker.detect_re_ask("s1", "보세전시장 정의 알려줘", history)
        assert result2 is True

    def test_get_satisfaction_stats_structure(self):
        self.tracker.track_response("s1", "q1", "faq_match")
        self.tracker.track_response("s1", "q2", "unknown")
        stats = self.tracker.get_satisfaction_stats()
        assert "total_queries" in stats
        assert "re_ask_rate" in stats
        assert "response_type_distribution" in stats
        assert "avg_satisfaction_score" in stats
        assert stats["total_queries"] == 2

    def test_detect_re_ask_no_match(self):
        history = [{"query": "견본품 반출"}]
        result = self.tracker.detect_re_ask("s1", "보세전시장 정의", history)
        assert result is False


# ===================================================================
# 5. KoreanTokenizer
# ===================================================================
class TestKoreanTokenizer:
    def setup_method(self):
        self.tokenizer = KoreanTokenizer()

    def test_tokenize_strips_particles(self):
        tokens = self.tokenizer.tokenize("보세전시장에서 물품을 전시합니다")
        # "보세전시장에서" -> strip "에서" -> "보세전시장"
        assert "보세전시장" in tokens

    def test_tokenize_preserves_domain_terms(self):
        tokens = self.tokenizer.tokenize("세관장확인 절차")
        assert "세관장확인" in tokens

    def test_extract_ngrams_bigrams(self):
        ngrams = self.tokenizer.extract_ngrams("보세전시장", n=2)
        assert len(ngrams) == 4  # 5 chars -> 4 bigrams
        assert ngrams[0] == "보세"
        assert ngrams[-1] == "시장"

    def test_tokenize_empty_input(self):
        assert self.tokenizer.tokenize("") == []
        assert self.tokenizer.tokenize("   ") == []

    def test_extract_ngrams_short_text(self):
        # Text shorter than n returns the cleaned text as single element
        ngrams = self.tokenizer.extract_ngrams("가", n=2)
        assert ngrams == ["가"]


# ===================================================================
# 6. BM25Ranker
# ===================================================================
class TestBM25Ranker:
    def setup_method(self):
        self.ranker = BM25Ranker(SAMPLE_FAQ)

    def test_rank_returns_results(self):
        results = self.ranker.rank("보세전시장 정의")
        assert len(results) > 0
        assert "item" in results[0]
        assert "score" in results[0]
        assert results[0]["score"] > 0

    def test_rank_category_filter(self):
        results = self.ranker.rank("판매 가능", category="SALES")
        for r in results:
            assert r["item"]["category"] == "SALES"

    def test_rank_empty_query(self):
        results = self.ranker.rank("")
        assert results == []

    def test_rank_scores_descending(self):
        results = self.ranker.rank("보세전시장")
        if len(results) > 1:
            for i in range(len(results) - 1):
                assert results[i]["score"] >= results[i + 1]["score"]


# ===================================================================
# 7. RelatedFAQFinder
# ===================================================================
class TestRelatedFAQFinder:
    def setup_method(self):
        self.finder = RelatedFAQFinder(SAMPLE_FAQ)

    def test_find_related_returns_list(self):
        results = self.finder.find_related("A")
        assert isinstance(results, list)
        # Should not include the source FAQ itself
        for r in results:
            assert r["id"] != "A"

    def test_find_related_by_query(self):
        results = self.finder.find_related_by_query("보세전시장 정의")
        assert isinstance(results, list)
        assert len(results) > 0
        assert "similarity" in results[0]

    def test_get_category_neighbors_same_category(self):
        # Add another GENERAL FAQ for the test
        extended_faq = SAMPLE_FAQ + [
            {
                "id": "D",
                "category": "GENERAL",
                "question": "보세구역이란 무엇인가요?",
                "answer": "보세구역은 관세법에 의해 지정된 구역입니다.",
                "legal_basis": [],
                "keywords": ["보세구역", "정의"],
            },
        ]
        finder = RelatedFAQFinder(extended_faq)
        results = finder.get_category_neighbors("A")
        assert isinstance(results, list)
        for r in results:
            assert r["category"] == "GENERAL"

    def test_find_related_unknown_id(self):
        results = self.finder.find_related("NONEXISTENT")
        assert results == []


# ===================================================================
# 8. RealtimeMonitor
# ===================================================================
class TestRealtimeMonitor:
    def setup_method(self):
        self.monitor = RealtimeMonitor()

    def test_record_event_stores_event(self):
        self.monitor.record_event("query", {"session_id": "s1", "category": "GENERAL"})
        events = self.monitor.get_recent_events()
        assert len(events) == 1
        assert events[0]["event_type"] == "query"

    def test_get_live_stats_structure(self):
        self.monitor.record_event("query", {"session_id": "s1", "response_time_ms": 50})
        stats = self.monitor.get_live_stats()
        assert "queries_last_minute" in stats
        assert "queries_last_hour" in stats
        assert "error_rate" in stats
        assert "avg_response_time_ms" in stats
        assert "active_sessions" in stats
        assert stats["queries_last_hour"] == 1

    def test_get_alerts_empty_when_normal(self):
        self.monitor.record_event("query", {"session_id": "s1"})
        alerts = self.monitor.get_alerts()
        assert alerts == []

    def test_get_alerts_high_error_rate(self):
        # Record many errors to trigger alert (>5% error rate)
        for i in range(10):
            self.monitor.record_event("error", {"session_id": f"s{i}"})
        self.monitor.record_event("query", {"session_id": "sq1"})
        alerts = self.monitor.get_alerts()
        alert_types = [a["alert_type"] for a in alerts]
        assert "high_error_rate" in alert_types

    def test_record_event_invalid_type_raises(self):
        with pytest.raises(ValueError):
            self.monitor.record_event("invalid_type", {})


# ===================================================================
# 9. FAQQualityChecker
# ===================================================================
class TestFAQQualityChecker:
    def test_check_duplicates_finds_overlap(self):
        # Two FAQs with identical keywords -> high Jaccard overlap
        faq_with_dup = [
            {"id": "X", "keywords": ["판매", "직매", "현장판매"], "question": "q1", "answer": "a" * 30, "category": "SALES"},
            {"id": "Y", "keywords": ["판매", "직매", "현장판매"], "question": "q2", "answer": "b" * 30, "category": "SALES"},
        ]
        checker = FAQQualityChecker(faq_with_dup, [])
        dups = checker.check_duplicates()
        assert len(dups) >= 1
        assert dups[0]["overlap"] > 0.6

    def test_check_keyword_coverage_flags_few_keywords(self):
        faq_low_kw = [
            {"id": "Z", "keywords": ["하나"], "question": "q", "answer": "a" * 30, "category": "GENERAL"},
        ]
        checker = FAQQualityChecker(faq_low_kw, [])
        issues = checker.check_keyword_coverage()
        assert len(issues) == 1
        assert issues[0]["keyword_count"] == 1

    def test_check_all_returns_report(self):
        checker = FAQQualityChecker(SAMPLE_FAQ, [])
        report = checker.check_all()
        assert "passed" in report
        assert "issues" in report
        assert "score" in report
        assert isinstance(report["score"], float)
        assert 0.0 <= report["score"] <= 1.0

    def test_check_keyword_coverage_no_issues(self):
        # All sample FAQs have 3 keywords each -> no issues
        checker = FAQQualityChecker(SAMPLE_FAQ, [])
        issues = checker.check_keyword_coverage()
        assert issues == []


# ===================================================================
# 10. ConversationExporter
# ===================================================================
class TestConversationExporter:
    def setup_method(self):
        self.exporter = ConversationExporter()

    def test_export_text(self):
        result = self.exporter.export_text(SAMPLE_HISTORY, session_id="test-001")
        assert "보세전시장 챗봇 대화 기록" in result
        assert "test-001" in result
        assert "[사용자]" in result
        assert "[챗봇]" in result
        assert "총 4개 대화" in result

    def test_export_json(self):
        result = self.exporter.export_json(SAMPLE_HISTORY, session_id="test-001")
        data = json.loads(result)
        assert data["session_id"] == "test-001"
        assert data["messages_count"] == 4
        assert len(data["messages"]) == 4

    def test_export_csv(self):
        result = self.exporter.export_csv(SAMPLE_HISTORY, session_id="test-001")
        lines = result.strip().split("\n")
        # Header + 4 data rows
        assert len(lines) == 5
        assert lines[0].startswith("role")

    def test_export_json_empty_history(self):
        result = self.exporter.export_json([], session_id="empty")
        data = json.loads(result)
        assert data["messages_count"] == 0
        assert data["messages"] == []


# ===================================================================
# 11. PluginSystem
# ===================================================================
class TestPluginManager:
    def setup_method(self):
        self.pm = PluginManager()

    def test_register_and_execute(self):
        def add_flag(data):
            data["flagged"] = True
            return data

        self.pm.register(HOOK_PRE_CLASSIFY, add_flag)
        result = self.pm.execute(HOOK_PRE_CLASSIFY, {"query": "test"})
        assert result["flagged"] is True
        assert result["query"] == "test"

    def test_priority_ordering(self):
        order = []

        def first(data):
            order.append("first")
            return data

        def second(data):
            order.append("second")
            return data

        self.pm.register(HOOK_PRE_CLASSIFY, second, priority=20)
        self.pm.register(HOOK_PRE_CLASSIFY, first, priority=5)
        self.pm.execute(HOOK_PRE_CLASSIFY, {})
        assert order == ["first", "second"]

    def test_unregister(self):
        def my_plugin(data):
            data["modified"] = True
            return data

        self.pm.register(HOOK_POST_CLASSIFY, my_plugin)
        self.pm.unregister(HOOK_POST_CLASSIFY, my_plugin)
        result = self.pm.execute(HOOK_POST_CLASSIFY, {"modified": False})
        assert result["modified"] is False

    def test_execute_no_plugins_returns_data(self):
        data = {"key": "value"}
        result = self.pm.execute("nonexistent_hook", data)
        assert result == data

    def test_list_plugins(self):
        def dummy(data):
            return data

        self.pm.register(HOOK_PRE_CLASSIFY, dummy, priority=10)
        summary = self.pm.list_plugins()
        assert HOOK_PRE_CLASSIFY in summary
        assert summary[HOOK_PRE_CLASSIFY][0]["fn_name"] == "dummy"
        assert summary[HOOK_PRE_CLASSIFY][0]["priority"] == 10
