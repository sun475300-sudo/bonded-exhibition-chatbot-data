"""보세전시장 민원응대 챗봇 웹 서버.

Flask 기반 REST API + 웹 UI를 제공한다.

사용법:
    python web_server.py              # 기본 포트 5000
    python web_server.py --port 8080  # 포트 지정
"""

import argparse
import hashlib
import logging
import os
import sys
import time
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, Response, request, jsonify, send_from_directory, make_response
from src.chatbot import BondedExhibitionChatbot
from src.metrics import metrics as metrics_collector
from src.classifier import classify_query
from src.conversation_export import ConversationExporter
from src.conversation_summary import ConversationSummarizer
from src.escalation import check_escalation, get_escalation_contact
from src.analytics import QueryAnalytics
from src.auto_faq_pipeline import AutoFAQPipeline
from src.faq_quality_checker import FAQQualityChecker
from src.faq_recommender import FAQRecommender
from src.feedback import FeedbackManager
from src.kakao_adapter import (
    build_skill_response,
    format_carousel,
    format_escalation_card,
    format_quick_replies,
    format_simple_text,
    init_kakao_routes,
    parse_kakao_request,
)
from src.naver_adapter import NaverTalkTalkAdapter, EVENT_SEND, EVENT_OPEN
from src.logger_db import ChatLogger
from src.report_generator import ReportGenerator
from src.realtime_monitor import RealtimeMonitor
from src.satisfaction_tracker import SatisfactionTracker
from src.security import APIKeyAuth, RateLimiter, sanitize_input
from src.rate_limiter_v2 import AdvancedRateLimiter
from src.translator import SimpleTranslator
from src.auth import JWTAuth, authenticate_user
from src.law_updater import LawUpdateScheduler, LawVersionTracker, FAQUpdateNotifier
from src.backup_manager import BackupManager
from src.faq_manager import FAQManager
from src.faq_io import FAQImporter, FAQExporter
from src.faq_diff import FAQDiffEngine
from src.tenant_manager import TenantManager
from src.webhook_manager import WebhookManager
from src.audit_logger import AuditLogger
from src.alert_center import AlertCenter, AlertRuleEngine
from src.profiler import RequestProfiler, ComponentBenchmark
from src.health_monitor import HealthMonitor
from src.i18n import I18nManager
from src.db_migration import MigrationManager
from src.ab_testing import ABTestManager
from src.user_recommender import UserRecommender
from src.flow_analyzer import FlowAnalyzer
from src.sentiment_analyzer import SentimentAnalyzer
from src.question_cluster import QuestionClusterer, DuplicateDetector
from src.task_scheduler import create_default_scheduler
from src.knowledge_graph import KnowledgeGraph
from src.template_engine import TemplateEngine, ResponseFormatter
from src.context_memory import ContextMemory, ConversationMemoryManager
from src.conversation_manager_v3 import ConversationManagerV3
from src.user_segment import UserSegmenter
from src.domain_config import DomainConfig, DomainInitializer
from src.utils import load_json
from src.api_gateway import APIGateway, PaginationHelper, SortHelper
from src.quality_scorer import ResponseQualityScorer, QualityReport
from src.conversation_analytics import ConversationAnalytics
from src.error_recovery import ErrorRecovery
from src.smart_suggestions import SmartSuggestionEngine
from src.entity_extractor_v2 import get_entity_extractor_v2
from src.hybrid_search_v3 import HybridSearchV3
from src.policy_engine_v2 import get_policy_engine_v2
from src.response_builder_v2 import get_response_builder_v2
from src.accuracy_benchmark import AccuracyBenchmark

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_QUERY_LENGTH = 500  # Production: reduced from 2000 to 500 chars for better security
APP_VERSION = "4.0.0"

# logs 디렉토리 자동 생성
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "web"), static_url_path="/static")

# Production configuration
app.config['JSON_SORT_KEYS'] = False
app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("chatbot")

# Production request logging setup
_request_log_handler = logging.FileHandler(os.path.join(BASE_DIR, "logs", "requests.log"))
_request_log_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
_request_logger = logging.getLogger("requests")
_request_logger.addHandler(_request_log_handler)
_request_logger.setLevel(logging.INFO)

chatbot = BondedExhibitionChatbot()
chat_logger = ChatLogger(db_path=os.path.join(BASE_DIR, "logs", "chat_logs.db"))
feedback_manager = FeedbackManager(db_path=os.path.join(BASE_DIR, "logs", "feedback.db"))
translator = SimpleTranslator()
i18n_manager = I18nManager()
faq_recommender = FAQRecommender(chat_logger)
query_analytics = QueryAnalytics(chat_logger, feedback_manager)
conversation_analytics = ConversationAnalytics(chat_logger, feedback_manager)
report_generator = ReportGenerator(chat_logger, feedback_manager)
auto_faq_pipeline = AutoFAQPipeline(
    faq_recommender, faq_path=os.path.join(BASE_DIR, "data", "faq.json")
)

# 보안 미들웨어 초기화
api_key_auth = APIKeyAuth(app)
rate_limit_value = int(os.environ.get("CHATBOT_RATE_LIMIT", "60"))
rate_limiter = RateLimiter(max_requests=rate_limit_value)
advanced_rate_limiter = AdvancedRateLimiter()

# 답변 정확도 벤치마크 초기화 (골든 테스트셋 기반)
accuracy_benchmark = AccuracyBenchmark(chatbot=chatbot)

# Phase 13-18 모듈 초기화
realtime_monitor = RealtimeMonitor()
conversation_exporter = ConversationExporter()
conversation_summarizer = ConversationSummarizer(chatbot.session_manager)
legal_refs = load_json("data/legal_references.json")
faq_quality_checker = FAQQualityChecker(chatbot.faq_items, legal_refs)
satisfaction_tracker = SatisfactionTracker()

# 응답 품질 스코어러 초기화
quality_scorer = ResponseQualityScorer(chat_logger)
quality_report = QualityReport(quality_scorer)

# JWT 인증 초기화
jwt_auth = JWTAuth()

# 법령 업데이트 모듈 초기화
law_version_tracker = LawVersionTracker()
faq_update_notifier = FAQUpdateNotifier()
law_update_scheduler = LawUpdateScheduler(law_version_tracker, faq_update_notifier)

# 백업 관리자 초기화
backup_manager = BackupManager()

# 웹훅 관리자 초기화
webhook_manager = WebhookManager()

# 멀티 테넌트 관리자 초기화
tenant_manager = TenantManager()

# FAQ 관리자 초기화
faq_manager = FAQManager()
faq_importer = FAQImporter(faq_manager)
faq_exporter = FAQExporter(faq_manager)
faq_diff_engine = FAQDiffEngine(faq_manager)

# 감사 로거 초기화
audit_logger = AuditLogger()

# 알림 센터 초기화
alert_center = AlertCenter()
alert_rule_engine = AlertRuleEngine(
    alert_center,
    realtime_monitor=realtime_monitor,
    satisfaction_tracker=satisfaction_tracker,
    faq_quality_checker=faq_quality_checker,
)

# 프로파일러 초기화
request_profiler = RequestProfiler()
component_benchmark = ComponentBenchmark()

# 마이그레이션 관리자 초기화
migration_manager = MigrationManager()

# 헬스 모니터 초기화
health_monitor = HealthMonitor(
    base_dir=BASE_DIR,
    faq_items=chatbot.faq_items,
    chat_logger=chat_logger,
)

# A/B 테스트 관리자 초기화
ab_test_manager = ABTestManager()

# 대화 흐름 분석기 초기화
flow_analyzer = FlowAnalyzer(db_path=os.path.join(BASE_DIR, "logs", "flow_analysis.db"))

# 사용자 추천 시스템 초기화
user_recommender = UserRecommender(
    db_path=os.path.join(BASE_DIR, "data", "user_profiles.db")
)

# 감정 분석기 초기화
sentiment_analyzer = SentimentAnalyzer(
    db_path=os.path.join(BASE_DIR, "data", "sentiment.db")
)

# 질문 클러스터링 초기화
question_clusterer = QuestionClusterer(chatbot.faq_items)
duplicate_detector = DuplicateDetector(chatbot.faq_items)

# 작업 스케줄러 초기화
task_scheduler = create_default_scheduler()

# 템플릿 엔진 초기화
template_engine = TemplateEngine()
response_formatter = ResponseFormatter(template_engine)

# 지식 그래프 초기화
knowledge_graph = KnowledgeGraph.build_from_faq(chatbot.faq_items, chatbot.legal_refs)

# 스마트 제안 엔진 초기화
smart_suggestion_engine = SmartSuggestionEngine(
    faq_items=chatbot.faq_items,
    knowledge_graph=knowledge_graph,
    question_clusterer=question_clusterer,
    related_faq_finder=chatbot.related_faq_finder,
)

# 컨텍스트 메모리 초기화
context_memory = ContextMemory(db_path=os.path.join(BASE_DIR, "data", "memory.db"))
conversation_memory_manager = ConversationMemoryManager(context_memory)

# 고급 다중턴 대화 관리자 (v3) 초기화
conversation_manager_v3 = ConversationManagerV3(
    db_path=os.path.join(BASE_DIR, "data", "conversation_v3.db"),
)

# 사용자 세분화 초기화
user_segmenter = UserSegmenter(db_path=os.path.join(BASE_DIR, "data", "segments.db"))

# 도메인 설정 초기화
domain_initializer = DomainInitializer()
_domain_config = DomainConfig()

# API 게이트웨이 초기화
api_gateway = APIGateway()
api_gateway.register_version("v1", status="active")
api_gateway.register_version("v2", status="active")
pagination_helper = PaginationHelper()
sort_helper = SortHelper()

# 엔티티 추출기 V2 초기화
entity_extractor_v2 = get_entity_extractor_v2()

# 하이브리드 검색 엔진 V3 초기화 (BM25 + 키워드 + 변형 매칭)
hybrid_search_v3 = HybridSearchV3(
    faq_items=chatbot.faq_items,
    variants_path=os.path.join(BASE_DIR, "data", "question_variants.json"),
)

# 에러 복구 시스템 초기화
error_recovery = ErrorRecovery(db_path=os.path.join(BASE_DIR, "logs", "error_logs.db"))

# --- Production: Simple in-memory rate limiter (60 requests/min per IP) ---
class ProductionRateLimiter:
    """Simple in-memory rate limiter for production."""
    def __init__(self, max_requests=60, window_seconds=60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = defaultdict(list)  # IP -> list of timestamps

    def is_allowed(self, client_ip):
        """Check if request is allowed for the client IP."""
        now = time.time()
        # Clean old requests
        self.requests[client_ip] = [ts for ts in self.requests[client_ip]
                                   if now - ts < self.window_seconds]

        if len(self.requests[client_ip]) < self.max_requests:
            self.requests[client_ip].append(now)
            return True
        return False

    def get_stats(self, client_ip):
        """Get current request stats for IP."""
        now = time.time()
        self.requests[client_ip] = [ts for ts in self.requests[client_ip]
                                   if now - ts < self.window_seconds]
        return {
            'requests': len(self.requests[client_ip]),
            'limit': self.max_requests,
            'window': self.window_seconds
        }

_production_rate_limiter = ProductionRateLimiter(
    max_requests=int(os.environ.get("PRODUCTION_RATE_LIMIT", "60")),
    window_seconds=60
)

# --- Request usage statistics ---
_usage_stats = {
    'total_queries': 0,
    'start_time': time.time(),
    'errors': defaultdict(int),
}

# --- FAQ in-memory cache ---
_faq_cache: dict = {}


def _refresh_faq_cache():
    """Load FAQ data into memory and pre-build TF-IDF index."""
    _faq_cache["items"] = list(chatbot.faq_items)
    _faq_cache["tfidf_matcher"] = chatbot.tfidf_matcher


_refresh_faq_cache()


def _get_audit_actor():
    """Extract actor username from JWT payload or return default."""
    payload = getattr(request, "jwt_payload", None)
    if payload:
        return payload.get("sub", "unknown")
    return "admin"


def _get_client_ip():
    """Extract client IP from request."""
    return request.remote_addr or "unknown"


# --- Production: CORS Configuration ---
def _get_cors_origins():
    """Get allowed CORS origins from environment variable."""
    default_origins = ["http://localhost:5000", "http://localhost:8080", "http://localhost:3000"]
    cors_origins = os.environ.get("CHATBOT_CORS_ORIGINS", ",".join(default_origins))
    return [origin.strip() for origin in cors_origins.split(",")]

_CORS_ORIGINS = _get_cors_origins()

@app.before_request
def _add_cors_headers():
    """Add CORS headers to request."""
    if request.method == 'OPTIONS':
        response = make_response()
        origin = request.headers.get('Origin')
        if origin in _CORS_ORIGINS:
            response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Tenant-Id'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response, 200

@app.after_request
def _apply_cors_headers(response):
    """Apply CORS headers to response."""
    origin = request.headers.get('Origin')
    if origin in _CORS_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response

# --- Production: Request logging middleware ---
@app.before_request
def _log_request_start():
    """Log request start with timestamp."""
    request._start_time = time.time()
    client_ip = _get_client_ip()
    _request_logger.info(f"[START] {request.method} {request.path} from {client_ip}")

@app.after_request
def _log_request_end(response):
    """Log request completion with response time."""
    if hasattr(request, '_start_time'):
        elapsed = time.time() - request._start_time
        status_code = response.status_code
        client_ip = _get_client_ip()
        _request_logger.info(
            f"[END] {request.method} {request.path} "
            f"status={status_code} time={elapsed:.3f}s from {client_ip}"
        )
    return response

# --- Advanced rate limiting middleware ---
_RATE_LIMIT_EXEMPT_PATHS = {"/", "/api/health", "/static", "/manifest.json", "/sw.js"}


@app.before_request
def _check_advanced_rate_limit():
    """Apply per-endpoint rate limits via AdvancedRateLimiter."""
    if app.config.get("TESTING") and not app.config.get("RATE_LIMIT_TESTING"):
        return None
    path = request.path
    # Skip static / health endpoints
    for exempt in _RATE_LIMIT_EXEMPT_PATHS:
        if path == exempt or path.startswith(exempt + "/"):
            return None

    client_ip = request.remote_addr or "unknown"
    allowed, remaining, reset_time = advanced_rate_limiter.check_rate_limit(
        client_ip, path
    )
    # Store for after_request header injection
    request._rl_remaining = remaining
    request._rl_reset = reset_time

    if not allowed:
        retry_after = max(1, reset_time - int(time.time()))
        response = jsonify({
            "error": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
        })
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        response.headers["X-RateLimit-Remaining"] = "0"
        response.headers["X-RateLimit-Reset"] = str(reset_time)
        return response

    return None


@app.after_request
def _add_rate_limit_headers(response):
    """Inject X-RateLimit-* headers on every response."""
    remaining = getattr(request, "_rl_remaining", None)
    reset_time = getattr(request, "_rl_reset", None)
    if remaining is not None and remaining >= 0:
        response.headers["X-RateLimit-Remaining"] = str(remaining)
    if reset_time:
        response.headers["X-RateLimit-Reset"] = str(reset_time)
    return response


# --- Response time middleware ---
@app.before_request
def _record_start_time():
    request._start_time = time.monotonic()


@app.after_request
def _add_response_time_header(response):
    start = getattr(request, "_start_time", None)
    if start is not None:
        elapsed_ms = (time.monotonic() - start) * 1000
        elapsed_s = elapsed_ms / 1000.0
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        if elapsed_ms > 500:
            logger.warning(
                f"Slow request: {request.method} {request.path} took {elapsed_ms:.1f}ms"
            )
        # --- Prometheus metrics instrumentation ---
        endpoint = request.path
        method = request.method
        status = str(response.status_code)
        metrics_collector.increment(
            "request_count",
            {"endpoint": endpoint, "method": method, "status": status},
        )
        metrics_collector.observe(
            "request_duration_seconds",
            elapsed_s,
            {"endpoint": endpoint},
        )
        # Update gauges
        try:
            metrics_collector.set_gauge("active_sessions", chatbot.session_manager.active_count())
            metrics_collector.set_gauge("faq_count", len(chatbot.faq_items))
        except (AttributeError, TypeError) as e:
            logger.debug(f"Metrics gauge update skipped: {e}")
    return response


# --- Static asset caching ---
@app.after_request
def _add_cache_headers(response):
    """Add Cache-Control and ETag headers for static files."""
    if request.path.startswith("/static/") or request.path in (
        "/manifest.json",
        "/sw.js",
    ):
        # Determine cache duration by file extension
        path_lower = request.path.lower()
        if path_lower.endswith(".html"):
            max_age = 3600  # 1 hour
        elif path_lower.endswith((".css", ".js", ".svg")):
            max_age = 604800  # 1 week
        else:
            max_age = 3600  # default 1 hour

        response.headers["Cache-Control"] = f"public, max-age={max_age}"

        # ETag support based on response data
        if response.data:
            etag = hashlib.md5(response.data).hexdigest()
            response.headers["ETag"] = f'"{etag}"'

            # Handle If-None-Match
            if_none_match = request.headers.get("If-None-Match")
            if if_none_match and if_none_match.strip('"') == etag:
                response.status_code = 304
                response.data = b""

    return response


@app.errorhandler(400)
def bad_request(e):
    return jsonify({"error": "잘못된 요청입니다."}), 400


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "요청한 리소스를 찾을 수 없습니다."}), 404


@app.errorhandler(500)
def internal_error(e):
    logger.error(f"내부 서버 오류: {e}")
    return jsonify({"error": "내부 서버 오류가 발생했습니다."}), 500


@app.route("/")
def index():
    """웹 챗봇 UI 페이지를 반환한다."""
    return send_from_directory(os.path.join(BASE_DIR, "web"), "index.html")


@app.route("/docs")
@app.route("/swagger")
def swagger_ui():
    """Swagger UI 페이지를 반환한다."""
    return send_from_directory(os.path.join(BASE_DIR, "web"), "swagger.html")


@app.route("/api/openapi.yaml")
def openapi_spec():
    """OpenAPI 명세 파일을 반환한다."""
    return send_from_directory(
        os.path.join(BASE_DIR, "docs"), "openapi.yaml", mimetype="text/yaml"
    )


@app.route("/manifest.json")
def manifest():
    """PWA 매니페스트 파일을 반환한다."""
    return send_from_directory(os.path.join(BASE_DIR, "web"), "manifest.json")


@app.route("/sw.js")
def service_worker():
    """Service Worker 파일을 반환한다."""
    response = send_from_directory(os.path.join(BASE_DIR, "web"), "sw.js")
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.route("/api/chat", methods=["POST"])
def chat():
    """Production chat endpoint with comprehensive error handling."""
    try:
        # Production: Simple rate limiting per IP (60 req/min)
        client_ip = request.remote_addr or "unknown"
        if not app.config.get("TESTING") and not os.environ.get("TESTING"):
            # rate_limiter(테스트용 전역 인스턴스)를 먼저 체크하여 테스트에서 제어 가능하게 함
            if not rate_limiter.is_allowed(client_ip):
                logger.warning(f"Rate limit exceeded for IP: {client_ip}")
                return jsonify({
                    "error": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
                    "error_code": "RATE_LIMIT_EXCEEDED"
                }), 429
            if not _production_rate_limiter.is_allowed(client_ip):
                logger.warning(f"Production rate limit exceeded for IP: {client_ip}")
                return jsonify({
                    "error": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
                    "error_code": "RATE_LIMIT_EXCEEDED"
                }), 429

        # Validate request JSON
        data = request.get_json(silent=True)
        if not data:
            logger.warning(f"Invalid JSON from {client_ip}")
            return jsonify({
                "error": "요청 형식이 올바르지 않습니다.",
                "error_code": "INVALID_JSON"
            }), 400

        if "query" not in data:
            return jsonify({
                "error": "query 필드가 필요합니다.",
                "error_code": "MISSING_FIELD"
            }), 400

        raw_query = data["query"]
        if not isinstance(raw_query, str):
            return jsonify({
                "error": "query는 문자열이어야 합니다.",
                "error_code": "INVALID_TYPE"
            }), 400

        # Production: Input validation (max 500 chars)
        if len(raw_query) > MAX_QUERY_LENGTH:
            return jsonify({
                "error": f"질문은 {MAX_QUERY_LENGTH}자 이내로 입력해 주세요.",
                "error_code": "QUERY_TOO_LONG",
                "max_length": MAX_QUERY_LENGTH
            }), 400

        if not raw_query.strip():
            return jsonify({
                "error": "질문을 입력해 주세요.",
                "error_code": "EMPTY_QUERY"
            }), 400

        # 입력 살균 적용
        query = sanitize_input(raw_query, max_length=MAX_QUERY_LENGTH)

        # 멀티 테넌트 지원: X-Tenant-Id 헤더 (선택, 기본값 "default")
        tenant_id = request.headers.get("X-Tenant-Id", "default")
        tenant = tenant_manager.get_tenant(tenant_id)
        if tenant is None:
            return jsonify({
                "error": f"테넌트 '{tenant_id}'를 찾을 수 없습니다.",
                "error_code": "TENANT_NOT_FOUND"
            }), 404
        if not tenant.get("active", True):
            return jsonify({
                "error": f"테넌트 '{tenant_id}'가 비활성 상태입니다.",
                "error_code": "TENANT_INACTIVE"
            }), 403

        categories = classify_query(query)
        escalation = check_escalation(query)
        # 세션 ID 처리 (선택적)
        session_id = data.get("session_id")

        # 엔티티 추출 V2
        try:
            extracted_entities = entity_extractor_v2.extract(query)
        except Exception as e:
            logger.error(f"엔티티 추출 실패: {e}")
            extracted_entities = []

        # 감정 분석
        sentiment_result = sentiment_analyzer.analyze_and_store(query, session_id=session_id)

        answer = chatbot.process_query(query, session_id=session_id)

        # 감정에 따른 답변 톤 조절
        answer = sentiment_analyzer.adjust_response_tone(answer, sentiment_result)

        # 매우 부정적 감정 시 자동 에스컬레이션
        sentiment_escalation = sentiment_analyzer.should_escalate(sentiment_result)
        if sentiment_escalation and escalation is None:
            escalation = {"target": "customer_support", "reason": "negative_sentiment"}

        logger.info(f"질문: {query[:50]}... | 분류: {categories[0]} | 에스컬레이션: {escalation is not None}")

        primary_category = categories[0] if categories else "GENERAL"
        is_escalation = escalation is not None

        # FAQ 매칭 결과에서 faq_id 추출
        # ?engine=hybrid 파라미터 지원 (기본값: 기존 엔진)
        engine = request.args.get("engine", "").lower()
        hybrid_results = None
        if engine == "hybrid":
            try:
                hybrid_results = hybrid_search_v3.search(query, top_k=3)
                if hybrid_results:
                    faq_match = hybrid_results[0]["item"]
                else:
                    faq_match = chatbot.find_matching_faq(query, primary_category)
            except Exception as e:
                logger.error(f"Hybrid search fallback: {e}")
                faq_match = chatbot.find_matching_faq(query, primary_category)
        else:
            faq_match = chatbot.find_matching_faq(query, primary_category)
        faq_id = faq_match.get("id") if faq_match else None

        # 로그 저장 + 모니터링 이벤트
        try:
            chat_logger.log_query(
                query=query,
                category=primary_category,
                faq_id=faq_id,
                is_escalation=is_escalation,
                response_preview=answer,
            )
            event_type = "escalation" if is_escalation else ("query" if faq_match else "unmatched")
            realtime_monitor.record_event(event_type, {"query": query, "category": primary_category})
            # 사용자 추천 시스템에 질문 기록
            if session_id:
                user_recommender.record_query(session_id, query, primary_category, faq_id)
        except Exception as e:
            logger.error(f"로그 저장 실패: {e}")

        # 사용자 세분화 분류 및 답변 깊이 조절
        user_segment = None
        if session_id:
            try:
                user_segment = user_segmenter.classify_user(session_id, query)
                answer = user_segmenter.adjust_response_depth(answer, user_segment)
            except Exception as e:
                logger.error(f"사용자 세분화 실패: {e}")

        # 다국어 지원: lang 파라미터에 따라 답변 헤더 번역
        lang = data.get("lang", "ko")
        if lang and lang != "ko" and translator.is_supported(lang):
            translated_answer = translator.translate_response(answer, lang)
        else:
            translated_answer = answer
            lang = "ko"

        # 관련 질문 추천
        related = []
        if faq_id:
            try:
                related = chatbot.related_faq_finder.find_related(faq_id, top_k=3)
            except (AttributeError, KeyError, ValueError) as e:
                logger.debug(f"Related FAQ lookup skipped: {e}")

        # 스마트 제안 생성
        suggestions = []
        try:
            session_history = []
            if session_id:
                ctx = chatbot.session_manager.get_context(session_id) if hasattr(chatbot.session_manager, "get_context") else {}
                session_history = ctx.get("queries", []) if isinstance(ctx, dict) else []
            suggestions = smart_suggestion_engine.get_follow_up_suggestions(
                query, translated_answer, primary_category, session_history=session_history,
            )
        except Exception as e:
            logger.error(f"스마트 제안 생성 실패: {e}")

        response = {
            "answer": translated_answer,
            "category": primary_category,
            "categories": categories,
            "is_escalation": is_escalation,
            "escalation_target": escalation.get("target") if escalation else None,
            "lang": lang,
            "related_questions": [{"id": r["id"], "question": r["question"]} for r in related],
            "suggestions": suggestions,
            "tenant_id": tenant_id,
            "sentiment": sentiment_result,
            "user_segment": user_segment,
            "entities": extracted_entities,
        }

        # ResponseBuilder v2 integration (?engine=v2)
        if engine == "v2":
            try:
                policy_engine = get_policy_engine_v2()
                policy_result = policy_engine.evaluate(
                    query=query,
                    intent_id=faq_id,
                    entities=extracted_entities,
                    category=primary_category,
                )
                builder_v2 = get_response_builder_v2()
                related_for_builder = [
                    {"id": r.get("id"), "question": r.get("question", "")}
                    for r in (related or [])
                ]
                structured = builder_v2.build(
                    faq_item=faq_match,
                    policy_result=policy_result,
                    entities=extracted_entities,
                    related=related_for_builder,
                )
                response["engine"] = "v2"
                response["structured_response"] = structured
                response["policy"] = policy_result
                # ?format=markdown|plain
                fmt = (request.args.get("format") or "").lower()
                if fmt == "markdown":
                    response["answer"] = builder_v2.format_markdown(structured)
                    response["format"] = "markdown"
                elif fmt == "plain":
                    response["answer"] = builder_v2.format_plain(structured)
                    response["format"] = "plain"
            except Exception as exc:
                logger.error(f"ResponseBuilderV2 실패: {exc}", exc_info=True)
                response["engine"] = "v2"
                response["structured_response_error"] = str(exc)
        elif (request.args.get("format") or "").lower() == "markdown":
            # Allow ?format=markdown without explicitly selecting engine=v2.
            try:
                builder_v2 = get_response_builder_v2()
                policy_engine = get_policy_engine_v2()
                policy_result = policy_engine.evaluate(
                    query=query,
                    intent_id=faq_id,
                    entities=extracted_entities,
                    category=primary_category,
                )
                related_for_builder = [
                    {"id": r.get("id"), "question": r.get("question", "")}
                    for r in (related or [])
                ]
                structured = builder_v2.build(
                    faq_item=faq_match,
                    policy_result=policy_result,
                    entities=extracted_entities,
                    related=related_for_builder,
                )
                response["answer"] = builder_v2.format_markdown(structured)
                response["format"] = "markdown"
                response["structured_response"] = structured
            except Exception as exc:
                logger.error(f"Markdown 변환 실패: {exc}", exc_info=True)

        if engine == "hybrid":
            response["engine"] = "hybrid"
            if hybrid_results:
                response["hybrid_results"] = [
                    {
                        "faq_id": r["faq_id"],
                        "score": r["score"],
                        "matched_via": r["matched_via"],
                        "matched_text": r["matched_text"],
                        "breakdown": r["breakdown"],
                    }
                    for r in hybrid_results
                ]
            else:
                response["hybrid_results"] = []

        if session_id:
            response["session_id"] = session_id
            # 개인화 추천 추가
            try:
                recommended = user_recommender.get_recommendations(session_id, top_n=3)
                response["recommended"] = recommended
            except Exception:
                response["recommended"] = []

            # 컨텍스트 메모리: 토픽 저장 및 재방문 사용자 resume 제공
            try:
                conversation_memory_manager.remember_topic(session_id, query, primary_category)
                resume = conversation_memory_manager.get_conversation_resume(session_id)
                if resume and conversation_memory_manager.is_returning_user(session_id):
                    response["conversation_resume"] = resume
            except Exception as e:
                logger.error(f"컨텍스트 메모리 저장 실패: {e}")

        _usage_stats['total_queries'] += 1
        return jsonify(response)

    except Exception as e:
        logger.error(f"Chat processing error: {e}", exc_info=True)
        _usage_stats['errors']['processing'] = _usage_stats['errors'].get('processing', 0) + 1
        return jsonify({
            "error": "답변 처리 중 오류가 발생했습니다.",
            "error_code": "PROCESSING_ERROR"
        }), 500


@app.route("/api/search/hybrid", methods=["POST", "GET"])
def api_search_hybrid():
    """하이브리드 검색 엔진 (BM25 + 키워드 + 변형) 엔드포인트.

    POST body: {"query": "...", "top_k": 5, "weights": {...}}
    GET query string: ?query=...&top_k=5
    """
    try:
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            query = data.get("query", "")
            top_k = int(data.get("top_k", 5))
            weights = data.get("weights")
            explain_id = data.get("explain_faq_id")
        else:
            query = request.args.get("query", "")
            top_k = int(request.args.get("top_k", 5))
            weights = None
            explain_id = request.args.get("explain_faq_id")

        if not isinstance(query, str) or not query.strip():
            return jsonify({
                "error": "query 필드가 필요합니다.",
                "error_code": "MISSING_QUERY",
            }), 400

        if top_k <= 0 or top_k > 50:
            return jsonify({
                "error": "top_k는 1에서 50 사이여야 합니다.",
                "error_code": "INVALID_TOP_K",
            }), 400

        query = sanitize_input(query, max_length=MAX_QUERY_LENGTH)

        original_weights = hybrid_search_v3.get_weights()
        if isinstance(weights, dict):
            try:
                hybrid_search_v3.set_weights(
                    kw=float(weights.get("keyword", original_weights["keyword"])),
                    bm25=float(weights.get("bm25", original_weights["bm25"])),
                    variant=float(weights.get("variant", original_weights["variant"])),
                )
            except (TypeError, ValueError):
                hybrid_search_v3.set_weights(
                    kw=original_weights["keyword"],
                    bm25=original_weights["bm25"],
                    variant=original_weights["variant"],
                )

        try:
            results = hybrid_search_v3.search(query, top_k=top_k)
            serializable = []
            for r in results:
                serializable.append({
                    "faq_id": r["faq_id"],
                    "score": r["score"],
                    "matched_via": r["matched_via"],
                    "matched_text": r["matched_text"],
                    "breakdown": r["breakdown"],
                    "question": r["item"].get("question", ""),
                    "category": r["item"].get("category", ""),
                })

            response = {
                "query": query,
                "top_k": top_k,
                "count": len(serializable),
                "results": serializable,
                "weights": hybrid_search_v3.get_weights(),
            }

            if explain_id:
                explanation = hybrid_search_v3.explain_result(query, explain_id)
                response["explanation"] = {
                    k: v for k, v in explanation.items() if k != "item"
                }

            return jsonify(response)
        finally:
            hybrid_search_v3.set_weights(
                kw=original_weights["keyword"],
                bm25=original_weights["bm25"],
                variant=original_weights["variant"],
            )

    except Exception as e:
        logger.error(f"Hybrid search error: {e}", exc_info=True)
        return jsonify({
            "error": "검색 처리 중 오류가 발생했습니다.",
            "error_code": "SEARCH_ERROR",
        }), 500


@app.route("/api/recommendations", methods=["GET"])
def api_recommendations():
    """개인화 FAQ 추천을 반환한다."""
    session_id = request.args.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id 파라미터가 필요합니다."}), 400
    try:
        recommendations = user_recommender.get_recommendations(session_id)
        return jsonify({"session_id": session_id, "recommendations": recommendations})
    except Exception as e:
        logger.error(f"추천 조회 실패: {e}")
        return jsonify({"error": "추천 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/popular", methods=["GET"])
def api_popular():
    """전체 인기 FAQ를 반환한다."""
    try:
        limit = request.args.get("limit", 10, type=int)
        popular = user_recommender.get_popular_faqs(limit=limit)
        return jsonify({"popular": popular})
    except Exception as e:
        logger.error(f"인기 FAQ 조회 실패: {e}")
        return jsonify({"error": "인기 FAQ 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/trending", methods=["GET"])
def api_trending():
    """트렌딩 토픽을 반환한다."""
    try:
        hours = request.args.get("hours", 24, type=int)
        limit = request.args.get("limit", 5, type=int)
        trending = user_recommender.get_trending_topics(hours=hours, limit=limit)
        return jsonify({"trending": trending})
    except Exception as e:
        logger.error(f"트렌딩 조회 실패: {e}")
        return jsonify({"error": "트렌딩 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/session/new", methods=["POST"])
def session_new():
    """새 세션을 생성한다."""
    session = chatbot.session_manager.create_session()
    return jsonify({
        "session_id": session.session_id,
        "created_at": session.created_at,
    }), 201


@app.route("/api/session/<session_id>", methods=["GET"])
def session_status(session_id):
    """세션 상태를 조회한다."""
    session = chatbot.session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "세션을 찾을 수 없거나 만료되었습니다."}), 404
    return jsonify(session.to_dict())


@app.route("/api/session/<session_id>/context", methods=["GET"])
def session_context(session_id):
    """세션의 컨텍스트 메모리를 조회한다."""
    key = request.args.get("key")
    try:
        entries = context_memory.get_context(session_id, key=key)
        return jsonify({"session_id": session_id, "context": entries})
    except Exception as e:
        logger.error(f"컨텍스트 조회 실패: {e}")
        return jsonify({"error": "컨텍스트 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/session/<session_id>/profile", methods=["GET"])
def session_profile(session_id):
    """세션의 사용자 프로필을 조회한다."""
    try:
        profile = context_memory.get_user_profile(session_id)
        return jsonify(profile)
    except Exception as e:
        logger.error(f"프로필 조회 실패: {e}")
        return jsonify({"error": "프로필 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/session/<session_id>/context", methods=["DELETE"])
def session_context_delete(session_id):
    """세션의 컨텍스트를 삭제한다."""
    data = request.get_json(silent=True) or {}
    key = data.get("key")
    try:
        deleted = context_memory.forget(session_id, key=key)
        return jsonify({"session_id": session_id, "deleted": deleted})
    except Exception as e:
        logger.error(f"컨텍스트 삭제 실패: {e}")
        return jsonify({"error": "컨텍스트 삭제 중 오류가 발생했습니다."}), 500


@app.route("/api/faq", methods=["GET"])
def faq_list():
    """FAQ 목록을 반환한다."""
    items = []
    for item in chatbot.faq_items:
        items.append({
            "id": item.get("id", ""),
            "category": item.get("category", ""),
            "question": item.get("question", ""),
        })
    return jsonify({"items": items, "count": len(items)})


@app.route("/api/faq/reload", methods=["POST"])
def faq_reload():
    """FAQ 데이터 및 관련 모듈을 서버 재시작 없이 리로드한다.

    변경된 faq.json 및 Python 모듈(synonym_resolver, similarity)을
    importlib를 이용해 동적으로 다시 로드한다.
    """
    import importlib
    try:
        # 1. 핵심 모듈 리로드
        import src.synonym_resolver as _syn_mod
        import src.similarity as _sim_mod
        import src.spell_corrector as _spell_mod
        importlib.reload(_syn_mod)
        importlib.reload(_sim_mod)
        importlib.reload(_spell_mod)

        # 2. chatbot 인스턴스 리로드 (FAQ + TF-IDF 재계산)
        from src.utils import load_json
        faq_data = load_json("data/faq.json")
        chatbot.faq_data = faq_data
        chatbot.faq_items = chatbot._normalize_faq_items(faq_data.get("items", []))

        # 3. TF-IDF 매처 재구축
        from src.similarity import TFIDFMatcher
        chatbot.tfidf_matcher = TFIDFMatcher(chatbot.faq_items)

        # 4. FAQ 캐시 갱신
        _refresh_faq_cache()

        # 5. RelatedFAQFinder, QuestionClusterer, DuplicateDetector 재구축
        from src.related_faq import RelatedFAQFinder
        chatbot.related_faq_finder = RelatedFAQFinder(chatbot.faq_items)
        from src.question_cluster import QuestionClusterer, DuplicateDetector
        _new_clusterer = QuestionClusterer(chatbot.faq_items)
        _new_detector = DuplicateDetector(chatbot.faq_items)

        # 6. SmartSuggestionEngine 재구축
        from src.knowledge_graph import KnowledgeGraph
        _new_kg = KnowledgeGraph.build_from_faq(chatbot.faq_items, chatbot.legal_refs)
        smart_suggestion_engine.__init__(
            faq_items=chatbot.faq_items,
            knowledge_graph=_new_kg,
            question_clusterer=_new_clusterer,
            related_faq_finder=chatbot.related_faq_finder,
        )

        faq_count = len(chatbot.faq_items)
        logger.info(f"FAQ reload successful: {faq_count} items loaded")
        return jsonify({
            "status": "ok",
            "message": f"FAQ 및 모듈이 재로드되었습니다. ({faq_count}개 항목)",
            "faq_count": faq_count,
        })
    except Exception as e:
        logger.error(f"FAQ reload failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/config", methods=["GET"])
def config():
    """챗봇 설정 정보를 반환한다."""
    return jsonify({
        "persona": chatbot.get_persona(),
        "categories": chatbot.config.get("categories", []),
        "contacts": chatbot.config.get("contacts", {}),
    })


@app.route("/api/autocomplete", methods=["GET"])
def autocomplete():
    """검색 자동완성: FAQ 질문 중 쿼리 문자열을 포함하는 상위 5개를 반환한다."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"suggestions": []})

    q_lower = q.lower()
    suggestions = []
    for item in chatbot.faq_items:
        question = item.get("question", "")
        if q_lower in question.lower():
            suggestions.append({
                "id": item.get("id", ""),
                "question": question,
                "category": item.get("category", ""),
            })
            if len(suggestions) >= 5:
                break

    return jsonify({"suggestions": suggestions})


@app.route("/api/health", methods=["GET"])
def health():
    """Production health check endpoint with version and FAQ count."""
    try:
        faq_count = len(chatbot.faq_items) if hasattr(chatbot, 'faq_items') else 0
        return jsonify({
            "status": "ok",
            "version": APP_VERSION,
            "faq_count": faq_count,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }), 200
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({
            "status": "error",
            "version": APP_VERSION,
            "error": str(e)
        }), 500


@app.route("/api/v1/stats", methods=["GET"])
def api_stats():
    """Production API stats endpoint returning basic usage statistics."""
    try:
        uptime_seconds = time.time() - _usage_stats['start_time']
        uptime_hours = uptime_seconds / 3600

        return jsonify({
            "total_queries": _usage_stats['total_queries'],
            "uptime_seconds": round(uptime_seconds, 2),
            "uptime_hours": round(uptime_hours, 2),
            "version": APP_VERSION,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }), 200
    except Exception as e:
        logger.error(f"Stats endpoint failed: {e}")
        return jsonify({"error": "Failed to retrieve statistics"}), 500


@app.route("/metrics", methods=["GET"])
def prometheus_metrics():
    """Prometheus metrics endpoint (no auth required)."""
    # Update cache hit rate gauge from FAQ cache state
    try:
        cache_items = _faq_cache.get("items")
        if cache_items is not None and len(chatbot.faq_items) > 0:
            metrics_collector.set_gauge("cache_hit_rate", 1.0)
        else:
            metrics_collector.set_gauge("cache_hit_rate", 0.0)
    except Exception:
        pass
    body = metrics_collector.collect()
    return Response(body, mimetype="text/plain; charset=utf-8")


@app.route("/api/admin/cache/clear", methods=["POST"])
@jwt_auth.require_auth()
def admin_cache_clear():
    """FAQ 캐시를 무효화하고 다시 로드한다."""
    try:
        # Reload FAQ data from disk
        chatbot.faq_data = load_json("data/faq.json")
        chatbot.faq_items = chatbot.faq_data.get("items", [])
        chatbot.tfidf_matcher = __import__(
            "src.similarity", fromlist=["TFIDFMatcher"]
        ).TFIDFMatcher(chatbot.faq_items)
        chatbot.related_faq_finder = __import__(
            "src.related_faq", fromlist=["RelatedFAQFinder"]
        ).RelatedFAQFinder(chatbot.faq_items)
        _refresh_faq_cache()
        logger.info("FAQ cache cleared and reloaded")
        return jsonify({"success": True, "faq_count": len(chatbot.faq_items)})
    except Exception as e:
        logger.error(f"캐시 초기화 실패: {e}")
        return jsonify({"error": "캐시 초기화 중 오류가 발생했습니다."}), 500


@app.route("/login")
def login_page():
    """로그인 페이지를 반환한다."""
    return send_from_directory(os.path.join(BASE_DIR, "web"), "login.html")


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    """사용자 로그인 처리."""
    data = request.get_json(silent=True)
    if not data or "username" not in data or "password" not in data:
        return jsonify({"error": "username과 password 필드가 필요합니다."}), 400

    user = authenticate_user(data["username"], data["password"])
    if user is None:
        try:
            audit_logger.log(
                actor=data["username"], action="login", resource_type="session",
                details={"success": False}, ip=_get_client_ip(),
            )
        except Exception:
            pass
        return jsonify({"error": "잘못된 사용자명 또는 비밀번호입니다."}), 401

    token = jwt_auth.generate_token(user["username"], role=user["role"])
    try:
        audit_logger.log(
            actor=user["username"], action="login", resource_type="session",
            details={"success": True}, ip=_get_client_ip(),
        )
    except Exception:
        pass
    return jsonify({"token": token, "expires_in": 86400})


@app.route("/api/auth/me", methods=["GET"])
@jwt_auth.require_auth()
def auth_me():
    """현재 사용자 정보를 반환한다."""
    payload = getattr(request, "jwt_payload", None)
    if payload:
        return jsonify({
            "username": payload.get("sub"),
            "role": payload.get("role"),
        })
    # When auth is disabled (TESTING/ADMIN_AUTH_DISABLED), return default
    return jsonify({"username": "admin", "role": "admin"})


@app.route("/admin")
def admin():
    """관리자 대시보드 페이지를 반환한다."""
    return send_from_directory(os.path.join(BASE_DIR, "web"), "admin.html")


@app.route("/api/admin/stats", methods=["GET"])
@jwt_auth.require_auth()
def admin_stats():
    """통계 JSON을 반환한다."""
    return jsonify(chat_logger.get_stats())


@app.route("/api/admin/logs", methods=["GET"])
@jwt_auth.require_auth()
def admin_logs():
    """최근 로그 JSON을 반환한다."""
    limit = request.args.get("limit", 50, type=int)
    return jsonify({"logs": chat_logger.get_recent_logs(limit=limit)})


@app.route("/api/admin/unmatched", methods=["GET"])
@jwt_auth.require_auth()
def admin_unmatched():
    """미매칭 질문 JSON을 반환한다."""
    limit = request.args.get("limit", 20, type=int)
    return jsonify({"queries": chat_logger.get_unmatched_queries(limit=limit)})


@app.route("/api/admin/recommendations", methods=["GET"])
@jwt_auth.require_auth()
def admin_recommendations():
    """미매칭 질문 기반 FAQ 추가 후보 추천 목록을 반환한다."""
    top_k = request.args.get("top_k", 10, type=int)
    try:
        recommendations = faq_recommender.get_recommendations(top_k=top_k)
        return jsonify({"recommendations": recommendations, "count": len(recommendations)})
    except Exception as e:
        logger.error(f"FAQ 추천 생성 실패: {e}")
        return jsonify({"error": "추천 생성 중 오류가 발생했습니다."}), 500


@app.route("/api/feedback", methods=["POST"])
def feedback():
    """사용자 피드백을 저장한다."""
    data = request.get_json(silent=True)
    if not data or "query_id" not in data or "rating" not in data:
        return jsonify({"error": "query_id와 rating 필드가 필요합니다."}), 400

    query_id = data["query_id"]
    rating = data["rating"]
    comment = data.get("comment", "")

    if rating not in ("helpful", "unhelpful"):
        return jsonify({"error": "rating은 'helpful' 또는 'unhelpful'이어야 합니다."}), 400

    try:
        feedback_id = feedback_manager.save_feedback(
            query_id=query_id, rating=rating, comment=comment
        )
        return jsonify({"success": True, "feedback_id": feedback_id}), 201
    except Exception as e:
        logger.error(f"피드백 저장 실패: {e}")
        return jsonify({"error": "피드백 저장에 실패했습니다."}), 500


@app.route("/api/admin/feedback", methods=["GET"])
@jwt_auth.require_auth()
def admin_feedback():
    """피드백 통계를 반환한다."""
    stats = feedback_manager.get_feedback_stats()
    low_rated = feedback_manager.get_low_rated_queries(limit=20)
    return jsonify({"stats": stats, "low_rated_queries": low_rated})


@app.route("/api/admin/analytics", methods=["GET"])
@jwt_auth.require_auth()
def admin_analytics():
    """분석 리포트를 반환한다."""
    try:
        days = request.args.get("days", 7, type=int)
        trend = query_analytics.get_trend_report(days=days)
        quality = query_analytics.get_quality_score()
        peak_hours = query_analytics.get_peak_hours()
        return jsonify({
            "trend": trend,
            "quality": quality,
            "peak_hours": peak_hours,
        })
    except Exception as e:
        logger.error(f"분석 리포트 생성 실패: {e}")
        return jsonify({"error": "분석 리포트 생성 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/report", methods=["GET"])
@jwt_auth.require_auth()
def admin_report():
    """주간 리포트 텍스트를 반환한다."""
    try:
        report_text = query_analytics.generate_report_text()
        return jsonify({"report": report_text})
    except Exception as e:
        logger.error(f"주간 리포트 생성 실패: {e}")
        return jsonify({"error": "주간 리포트 생성 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/reports/daily", methods=["GET"])
@jwt_auth.require_auth()
def admin_report_daily():
    """일별 리포트 JSON을 반환한다."""
    try:
        date = request.args.get("date", None)
        report_data = report_generator.generate_daily_report(date=date)
        return jsonify(report_data)
    except Exception as e:
        logger.error(f"일별 리포트 생성 실패: {e}")
        return jsonify({"error": "일별 리포트 생성 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/reports/weekly", methods=["GET"])
@jwt_auth.require_auth()
def admin_report_weekly():
    """주별 리포트 JSON을 반환한다."""
    try:
        start = request.args.get("start", None)
        report_data = report_generator.generate_weekly_report(week_start=start)
        return jsonify(report_data)
    except Exception as e:
        logger.error(f"주별 리포트 생성 실패: {e}")
        return jsonify({"error": "주별 리포트 생성 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/reports/monthly", methods=["GET"])
@jwt_auth.require_auth()
def admin_report_monthly():
    """월별 리포트 JSON을 반환한다."""
    try:
        year = request.args.get("year", type=int)
        month = request.args.get("month", type=int)
        if not year or not month:
            return jsonify({"error": "year와 month 파라미터가 필요합니다."}), 400
        if month < 1 or month > 12:
            return jsonify({"error": "month는 1-12 사이여야 합니다."}), 400
        report_data = report_generator.generate_monthly_report(year, month)
        return jsonify(report_data)
    except Exception as e:
        logger.error(f"월별 리포트 생성 실패: {e}")
        return jsonify({"error": "월별 리포트 생성 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/reports/html", methods=["GET"])
@jwt_auth.require_auth()
def admin_report_html():
    """HTML 리포트 파일을 다운로드한다."""
    import tempfile
    try:
        report_type = request.args.get("type", "daily")
        if report_type == "daily":
            date = request.args.get("date", None)
            report_data = report_generator.generate_daily_report(date=date)
        elif report_type == "weekly":
            start = request.args.get("start", None)
            report_data = report_generator.generate_weekly_report(week_start=start)
        elif report_type == "monthly":
            year = request.args.get("year", type=int)
            month = request.args.get("month", type=int)
            if not year or not month:
                return jsonify({"error": "year와 month 파라미터가 필요합니다."}), 400
            report_data = report_generator.generate_monthly_report(year, month)
        else:
            return jsonify({"error": "유효하지 않은 리포트 타입입니다."}), 400

        tmp = tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, dir=os.path.join(BASE_DIR, "logs")
        )
        tmp.close()
        report_generator.export_html(report_data, tmp.name)
        with open(tmp.name, "r", encoding="utf-8") as f:
            html_content = f.read()
        os.unlink(tmp.name)

        filename = f"report_{report_type}_{report_data.get('start_date', 'unknown')}.html"
        return Response(
            html_content,
            mimetype="text/html",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        logger.error(f"HTML 리포트 생성 실패: {e}")
        return jsonify({"error": "HTML 리포트 생성 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/faq-pipeline", methods=["GET"])
@jwt_auth.require_auth()
def admin_faq_pipeline():
    """FAQ 후보 목록을 반환한다."""
    try:
        min_freq = request.args.get("min_frequency", 3, type=int)
        candidates = auto_faq_pipeline.get_pending_candidates(min_frequency=min_freq)
        return jsonify({"candidates": candidates, "count": len(candidates)})
    except Exception as e:
        logger.error(f"FAQ 파이프라인 조회 실패: {e}")
        return jsonify({"error": "FAQ 파이프라인 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/faq-pipeline/approve", methods=["POST"])
@jwt_auth.require_auth()
def admin_faq_approve():
    """FAQ 후보를 승인한다."""
    data = request.get_json(silent=True)
    if not data or "candidate_id" not in data:
        return jsonify({"error": "candidate_id 필드가 필요합니다."}), 400

    try:
        result = auto_faq_pipeline.approve_candidate(data["candidate_id"])
        return jsonify({"success": True, "candidate": result})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"FAQ 후보 승인 실패: {e}")
        return jsonify({"error": "FAQ 후보 승인 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/faq-pipeline/reject", methods=["POST"])
@jwt_auth.require_auth()
def admin_faq_reject():
    """FAQ 후보를 거부한다."""
    data = request.get_json(silent=True)
    if not data or "candidate_id" not in data:
        return jsonify({"error": "candidate_id 필드가 필요합니다."}), 400

    try:
        result = auto_faq_pipeline.reject_candidate(data["candidate_id"])
        return jsonify({"success": True, "candidate": result})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"FAQ 후보 거부 실패: {e}")
        return jsonify({"error": "FAQ 후보 거부 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/monitor", methods=["GET"])
@jwt_auth.require_auth()
def admin_monitor():
    """실시간 모니터링 데이터를 반환한다."""
    try:
        stats = realtime_monitor.get_live_stats()
        alerts = realtime_monitor.get_alerts()
        return jsonify({"stats": stats, "alerts": alerts})
    except Exception as e:
        logger.error(f"모니터링 조회 실패: {e}")
        return jsonify({"error": "모니터링 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/quality", methods=["GET"])
@jwt_auth.require_auth()
def admin_quality():
    """FAQ 품질 검사 결과를 반환한다."""
    try:
        result = faq_quality_checker.check_all()
        return jsonify(result)
    except Exception as e:
        logger.error(f"품질 검사 실패: {e}")
        return jsonify({"error": "품질 검사 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/realtime", methods=["GET"])
@jwt_auth.require_auth()
def admin_realtime():
    """실시간 모니터링 라이브 통계를 반환한다."""
    try:
        stats = realtime_monitor.get_live_stats()
        alerts = realtime_monitor.get_alerts()

        # Build hourly query counts for the last 24 hours
        import time as _time
        now = _time.time()
        hourly_counts: list[dict] = []
        with realtime_monitor._lock:
            events = list(realtime_monitor._buffer)
        for h in range(23, -1, -1):
            start = now - (h + 1) * 3600
            end = now - h * 3600
            count = sum(
                1 for e in events
                if e["event_type"] in ("query", "unmatched")
                and start <= e["timestamp"] < end
            )
            hour_label = _time.strftime("%H", _time.localtime(end))
            hourly_counts.append({"hour": hour_label, "count": count})

        return jsonify({
            "queries_per_minute": stats["queries_last_minute"],
            "avg_response_time_ms": stats["avg_response_time_ms"],
            "active_sessions": stats["active_sessions"],
            "error_rate": stats["error_rate"],
            "unmatched_rate": stats["unmatched_rate"],
            "queries_last_hour": stats["queries_last_hour"],
            "top_categories": stats["top_categories"],
            "alerts": alerts,
            "hourly_counts": hourly_counts,
        })
    except Exception as e:
        logger.error(f"실시간 모니터링 조회 실패: {e}")
        return jsonify({"error": "실시간 모니터링 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/faq-quality", methods=["GET"])
@jwt_auth.require_auth()
def admin_faq_quality():
    """FAQ 품질 대시보드 데이터를 반환한다."""
    try:
        result = faq_quality_checker.check_all()
        # Enrich each issue with severity level
        for issue in result.get("issues", []):
            check_type = issue.get("check", "")
            count = issue.get("count", 0)
            if check_type == "duplicates" or (check_type == "keyword_coverage" and count > 5):
                issue["severity"] = "critical"
            elif count > 2:
                issue["severity"] = "warning"
            else:
                issue["severity"] = "good"
        return jsonify(result)
    except Exception as e:
        logger.error(f"FAQ 품질 대시보드 조회 실패: {e}")
        return jsonify({"error": "FAQ 품질 대시보드 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/satisfaction", methods=["GET"])
@jwt_auth.require_auth()
def admin_satisfaction():
    """만족도 트렌드 데이터를 반환한다."""
    try:
        stats = satisfaction_tracker.get_satisfaction_stats()
        low_queries = satisfaction_tracker.get_low_satisfaction_queries(limit=10)

        # Determine trend direction based on recent vs overall score
        avg_score = stats.get("avg_satisfaction_score", 0.0)
        if avg_score >= 0.7:
            trend = "up"
        elif avg_score >= 0.4:
            trend = "stable"
        else:
            trend = "down"

        return jsonify({
            "overall_score": avg_score,
            "trend": trend,
            "total_queries": stats.get("total_queries", 0),
            "re_ask_rate": stats.get("re_ask_rate", 0.0),
            "response_type_distribution": stats.get("response_type_distribution", {}),
            "lowest_rated": low_queries,
        })
    except Exception as e:
        logger.error(f"만족도 트렌드 조회 실패: {e}")
        return jsonify({"error": "만족도 트렌드 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/export", methods=["POST"])
def export_conversation():
    """대화 내역을 파일로 내보낸다.

    요청 본문: {"session_id": "...", "format": "text|json|csv|html"}
    """
    data = request.get_json(silent=True)
    if not data or "session_id" not in data:
        return jsonify({"error": "session_id 필드가 필요합니다."}), 400

    session_id = data["session_id"]
    fmt = data.get("format", "text")
    if fmt not in ("text", "json", "csv", "html"):
        return jsonify({"error": "format은 text, json, csv, html 중 하나여야 합니다."}), 400

    session = chatbot.session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "세션을 찾을 수 없거나 만료되었습니다."}), 404

    history = session.history

    ext_map = {"text": "txt", "json": "json", "csv": "csv", "html": "html"}
    mime_map = {
        "text": "text/plain; charset=utf-8",
        "json": "application/json",
        "csv": "text/csv; charset=utf-8",
        "html": "text/html; charset=utf-8",
    }
    filename = f"conversation_export.{ext_map[fmt]}"

    if fmt == "json":
        content = conversation_exporter.export_json(history, session_id)
    elif fmt == "csv":
        content = conversation_exporter.export_csv(history, session_id)
    elif fmt == "html":
        content = conversation_exporter.export_html(history, session_id)
    else:
        content = conversation_exporter.export_text(history, session_id)

    resp = app.response_class(content, mimetype=mime_map[fmt])
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@app.route("/api/session/<session_id>/export", methods=["GET"])
def session_export(session_id):
    """세션 대화를 내보낸다."""
    session = chatbot.session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "세션을 찾을 수 없습니다."}), 404

    fmt = request.args.get("format", "text")
    history = session.history

    if fmt == "json":
        content = conversation_exporter.export_json(history, session_id)
        return app.response_class(content, mimetype="application/json")
    elif fmt == "csv":
        content = conversation_exporter.export_csv(history, session_id)
        return app.response_class(content, mimetype="text/csv")
    elif fmt == "html":
        content = conversation_exporter.export_html(history, session_id)
        return app.response_class(content, mimetype="text/html")
    else:
        content = conversation_exporter.export_text(history, session_id)
        return app.response_class(content, mimetype="text/plain; charset=utf-8")


@app.route("/api/related/<faq_id>", methods=["GET"])
def related_faq(faq_id):
    """관련 FAQ를 반환한다."""
    top_k = request.args.get("top_k", 3, type=int)
    try:
        related = chatbot.related_faq_finder.find_related(faq_id, top_k=top_k)
        return jsonify({"related": related, "count": len(related)})
    except Exception as e:
        logger.error(f"관련 FAQ 조회 실패: {e}")
        return jsonify({"error": "관련 FAQ 조회 중 오류가 발생했습니다."}), 500


# 카카오 i 오픈빌더 블루프린트 등록
kakao_blueprint = init_kakao_routes(chatbot, chat_logger)
app.register_blueprint(kakao_blueprint)


@app.route("/api/kakao/chat", methods=["POST"])
def api_kakao_chat():
    """카카오 i 오픈빌더 스킬 요청을 처리한다 (API 경로).

    카카오 요청 형식:
    {
        "userRequest": {"utterance": "...", "user": {"id": "..."}},
        "bot": {"id": "..."},
        "action": {"name": "..."}
    }

    카카오 응답 형식: simpleText + quickReplies
    """
    data = request.get_json(silent=True)
    if not data:
        resp = build_skill_response([format_simple_text("요청을 처리할 수 없습니다.")])
        return jsonify(resp), 200

    parsed = parse_kakao_request(data)
    utterance = parsed["utterance"]

    if not utterance:
        resp = build_skill_response([
            format_simple_text("질문을 입력해 주세요.\n\n예: 보세전시장이 무엇인가요?")
        ])
        return jsonify(resp), 200

    # 챗봇 응답 생성
    answer = chatbot.process_query(utterance)
    categories = classify_query(utterance)
    primary_category = categories[0] if categories else "GENERAL"
    escalation = check_escalation(utterance)

    outputs = [format_simple_text(answer)]

    # 에스컬레이션 필요 시 연락처 카드 추가
    if escalation is not None:
        contact = get_escalation_contact(escalation)
        if contact:
            outputs.append(format_escalation_card(contact))

    # 바로가기 버튼: FAQ 카테고리
    config_categories = chatbot.config.get("categories", [])
    category_names = [
        c["name"] if isinstance(c, dict) else str(c)
        for c in config_categories
    ]
    if category_names:
        quick_replies = format_quick_replies(category_names[:5])
    else:
        quick_replies = format_quick_replies([
            "보세전시장이란?",
            "물품 반입 절차",
            "현장 판매 가능?",
            "문의처 안내",
        ])

    # 로깅
    try:
        faq_match = chatbot.find_matching_faq(utterance, primary_category)
        chat_logger.log_query(
            query=utterance,
            category=primary_category,
            faq_id=faq_match.get("id") if faq_match else None,
            is_escalation=escalation is not None,
            response_preview=answer[:200],
        )
    except Exception:
        pass

    resp = build_skill_response(outputs, quick_replies)
    return jsonify(resp), 200


@app.route("/api/kakao/faq", methods=["POST"])
def api_kakao_faq():
    """FAQ 목록을 카카오 캐러셀 카드 형식으로 반환한다.

    카카오 요청 형식 (표준 스킬 요청):
    {
        "userRequest": {"utterance": "...", "user": {"id": "..."}},
        "bot": {"id": "..."},
        "action": {"name": "..."}
    }

    응답: 카카오 carousel 카드 (FAQ 항목)
    """
    data = request.get_json(silent=True)

    # 카테고리 필터링 (action params에서 category 추출)
    category_filter = None
    if data:
        action = data.get("action", {})
        params = action.get("params", {})
        category_filter = params.get("category", "").strip() or None

    faq_items = chatbot.faq_items
    if category_filter:
        faq_items = [
            item for item in faq_items
            if item.get("category", "") == category_filter
        ]

    # 최대 10개 카드로 제한 (카카오 캐러셀 제한)
    faq_items = faq_items[:10]

    if not faq_items:
        resp = build_skill_response([
            format_simple_text("해당 카테고리의 FAQ가 없습니다.")
        ])
        return jsonify(resp), 200

    carousel = format_carousel(faq_items)

    # 카테고리 바로가기 버튼
    all_categories = sorted(set(
        item.get("category", "") for item in chatbot.faq_items if item.get("category")
    ))
    quick_replies = format_quick_replies(all_categories[:5])

    resp = build_skill_response([carousel], quick_replies)
    return jsonify(resp), 200


@app.route("/api/admin/law-updates", methods=["GET"])
def admin_law_updates():
    """최근 법령 변경과 영향 받는 FAQ를 반환한다."""
    try:
        since = request.args.get("since", "1970-01-01")
        changes = law_version_tracker.get_changes_since(since)
        pending = faq_update_notifier.get_pending_notifications()
        history = law_update_scheduler.get_update_history()
        return jsonify({
            "changes": changes,
            "pending_notifications": pending,
            "update_history": history,
        })
    except Exception as e:
        logger.error(f"법령 업데이트 조회 실패: {e}")
        return jsonify({"error": "법령 업데이트 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/law-updates/check", methods=["POST"])
def admin_law_updates_check():
    """수동 법령 업데이트 확인을 트리거한다."""
    try:
        result = law_update_scheduler.check_for_updates()
        return jsonify(result)
    except Exception as e:
        logger.error(f"법령 업데이트 확인 실패: {e}")
        return jsonify({"error": "법령 업데이트 확인 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/law-updates/acknowledge", methods=["POST"])
def admin_law_updates_acknowledge():
    """법령 변경 알림을 확인 처리한다."""
    data = request.get_json(silent=True)
    if not data or "notification_id" not in data:
        return jsonify({"error": "notification_id 필드가 필요합니다."}), 400

    try:
        success = faq_update_notifier.acknowledge(data["notification_id"])
        if success:
            return jsonify({"success": True})
        else:
            return jsonify({"error": "알림을 찾을 수 없거나 이미 확인되었습니다."}), 404
    except Exception as e:
        logger.error(f"알림 확인 처리 실패: {e}")
        return jsonify({"error": "알림 확인 처리 중 오류가 발생했습니다."}), 500


# --- 국가법령정보센터 API 동기화 ---
from src.law_api_sync import LawSyncManager  # noqa: E402
law_sync_manager = LawSyncManager()


@app.route("/api/admin/law-sync/check", methods=["POST"])
@jwt_auth.require_auth()
def admin_law_sync_check():
    """국가법령정보센터에서 법령 변경을 실시간 확인한다."""
    try:
        result = law_sync_manager.check_all()
        return jsonify(result)
    except Exception as e:
        logger.error(f"법령 동기화 확인 실패: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/law-sync/sync", methods=["POST"])
@jwt_auth.require_auth()
def admin_law_sync_apply():
    """법령 변경을 확인하고 legal_references.json을 자동 업데이트한다."""
    try:
        check_result = law_sync_manager.check_all()
        update_result = law_sync_manager.update_legal_references()
        return jsonify({
            "check": check_result,
            "update": update_result,
        })
    except Exception as e:
        logger.error(f"법령 동기화 실패: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/law-sync/history", methods=["GET"])
@jwt_auth.require_auth()
def admin_law_sync_history():
    """법령 동기화 이력을 조회한다."""
    limit = request.args.get("limit", 50, type=int)
    return jsonify({"history": law_sync_manager.get_sync_history(limit=limit)})


@app.route("/api/admin/law-sync/monitored", methods=["GET"])
@jwt_auth.require_auth()
def admin_law_sync_monitored():
    """모니터링 대상 법령 목록을 조회한다."""
    return jsonify({"laws": law_sync_manager.get_monitored_laws()})


@app.route("/api/admin/backup", methods=["POST"])
@jwt_auth.require_auth()
def admin_backup_create():
    """수동 백업을 트리거한다."""
    try:
        backup_path = backup_manager.create_backup()
        try:
            audit_logger.log(
                actor=_get_audit_actor(), action="backup", resource_type="backup",
                resource_id=os.path.basename(backup_path), ip=_get_client_ip(),
            )
        except Exception:
            pass
        return jsonify({
            "success": True,
            "backup_path": backup_path,
            "filename": os.path.basename(backup_path),
        }), 201
    except Exception as e:
        logger.error(f"백업 생성 실패: {e}")
        return jsonify({"error": "백업 생성 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/backups", methods=["GET"])
@jwt_auth.require_auth()
def admin_backup_list():
    """사용 가능한 백업 목록을 반환한다."""
    try:
        backups = backup_manager.list_backups()
        return jsonify({"backups": backups, "count": len(backups)})
    except Exception as e:
        logger.error(f"백업 목록 조회 실패: {e}")
        return jsonify({"error": "백업 목록 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/restore", methods=["POST"])
@jwt_auth.require_auth()
def admin_restore():
    """특정 백업에서 복원한다."""
    data = request.get_json(silent=True)
    if not data or "filename" not in data:
        return jsonify({"error": "filename 필드가 필요합니다."}), 400

    filename = data["filename"]
    backup_path = os.path.join(BASE_DIR, "backups", filename)

    if not os.path.isfile(backup_path):
        return jsonify({"error": "백업 파일을 찾을 수 없습니다."}), 404

    try:
        result = backup_manager.restore_from_backup(backup_path)
        try:
            audit_logger.log(
                actor=_get_audit_actor(), action="restore", resource_type="backup",
                resource_id=filename, ip=_get_client_ip(),
            )
        except Exception:
            pass
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error(f"복원 실패: {e}")
        return jsonify({"error": "복원 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/backup/<backup_id>", methods=["DELETE"])
@jwt_auth.require_auth()
def admin_backup_delete(backup_id):
    """특정 백업을 삭제한다."""
    backup_path = os.path.join(BASE_DIR, "backups", backup_id)

    if not os.path.isfile(backup_path):
        return jsonify({"error": "백업 파일을 찾을 수 없습니다."}), 404

    try:
        os.remove(backup_path)
        enc_path = backup_path + ".enc"
        if os.path.isfile(enc_path):
            os.remove(enc_path)
        try:
            audit_logger.log(
                actor=_get_audit_actor(), action="delete", resource_type="backup",
                resource_id=backup_id, ip=_get_client_ip(),
            )
        except Exception:
            pass
        return jsonify({"success": True, "deleted": backup_id})
    except Exception as e:
        logger.error(f"백업 삭제 실패: {e}")
        return jsonify({"error": "백업 삭제 중 오류가 발생했습니다."}), 500


# 네이버 톡톡 어댑터 인스턴스
naver_adapter = NaverTalkTalkAdapter()

NAVER_WELCOME_MESSAGE = (
    "안녕하세요! 보세전시장 민원응대 챗봇입니다.\n\n"
    "보세전시장에 관한 질문을 입력해 주세요.\n"
    "예: 보세전시장이 무엇인가요?"
)


@app.route("/api/naver/webhook", methods=["POST"])
def naver_webhook_post():
    """네이버 톡톡 웹훅 수신 엔드포인트.

    네이버 톡톡 웹훅 형식:
    {
        "event": "send",
        "user": "유저식별값",
        "textContent": {"text": "사용자 메시지"}
    }

    이벤트 타입별 처리:
    - send: 사용자 메시지를 챗봇으로 처리하여 응답
    - open: 환영 메시지 반환
    - leave, friend: 200 OK 반환
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": True}), 200

    parsed = naver_adapter.parse_webhook(data)
    event = parsed["event"]
    user_id = parsed["user_id"]

    if event == EVENT_SEND:
        text = parsed["text"]
        if not text:
            response = naver_adapter.build_response(event, {
                "user_id": user_id,
                "text": "질문을 입력해 주세요.\n\n예: 보세전시장이 무엇인가요?",
            })
            return jsonify(response), 200

        # 챗봇 응답 생성
        answer = chatbot.process_query(text)

        # 바로가기 버튼 추가
        buttons = [
            {"label": "보세전시장이란?", "value": "보세전시장이란?"},
            {"label": "물품 반입 절차", "value": "물품 반입 절차"},
            {"label": "현장 판매 가능?", "value": "현장 판매 가능?"},
            {"label": "문의처 안내", "value": "문의처 안내"},
        ]

        response = naver_adapter.build_response(event, {
            "user_id": user_id,
            "text": answer,
            "buttons": buttons,
        })

        # 로깅
        try:
            categories = classify_query(text)
            primary_category = categories[0] if categories else "GENERAL"
            faq_match = chatbot.find_matching_faq(text, primary_category)
            chat_logger.log_query(
                query=text,
                category=primary_category,
                faq_id=faq_match.get("id") if faq_match else None,
                is_escalation=False,
                response_preview=answer[:200],
            )
        except Exception:
            pass

        return jsonify(response), 200

    elif event == EVENT_OPEN:
        response = naver_adapter.build_response(event, {
            "user_id": user_id,
            "text": NAVER_WELCOME_MESSAGE,
        })
        return jsonify(response), 200

    # leave, friend 등 기타 이벤트는 200 OK 반환
    return jsonify({"success": True}), 200


@app.route("/api/naver/webhook", methods=["GET"])
def naver_webhook_get():
    """네이버 톡톡 웹훅 검증 엔드포인트.

    네이버 톡톡이 웹훅 URL 등록 시 GET 요청으로 검증한다.
    challenge 파라미터를 그대로 반환하여 인증을 완료한다.
    """
    challenge = request.args.get("challenge", "")
    return challenge, 200


# --- 멀티 테넌트 관리 API ---


@app.route("/api/admin/tenants", methods=["GET"])
@jwt_auth.require_auth()
def admin_list_tenants():
    """테넌트 목록을 반환한다."""
    try:
        tenants = tenant_manager.list_tenants()
        return jsonify({"tenants": tenants, "count": len(tenants)})
    except Exception as e:
        logger.error(f"테넌트 목록 조회 실패: {e}")
        return jsonify({"error": "테넌트 목록 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/tenants", methods=["POST"])
@jwt_auth.require_auth()
def admin_create_tenant():
    """새 테넌트를 생성한다."""
    data = request.get_json(silent=True)
    if not data or "tenant_id" not in data or "name" not in data:
        return jsonify({"error": "tenant_id와 name 필드가 필요합니다."}), 400

    try:
        tenant = tenant_manager.create_tenant(
            tenant_id=data["tenant_id"],
            name=data["name"],
            config=data.get("config"),
        )
        try:
            audit_logger.log(
                actor=_get_audit_actor(), action="create", resource_type="tenant",
                resource_id=data["tenant_id"], details={"name": data["name"]},
                ip=_get_client_ip(),
            )
        except Exception:
            pass
        return jsonify({"success": True, "tenant": tenant}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"테넌트 생성 실패: {e}")
        return jsonify({"error": "테넌트 생성 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/tenants/<tenant_id>", methods=["PUT"])
@jwt_auth.require_auth()
def admin_update_tenant(tenant_id):
    """테넌트 설정을 업데이트한다."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "업데이트할 데이터가 필요합니다."}), 400

    try:
        tenant = tenant_manager.update_tenant(tenant_id, data)
        try:
            audit_logger.log(
                actor=_get_audit_actor(), action="update", resource_type="tenant",
                resource_id=tenant_id, details={"fields": list(data.keys())},
                ip=_get_client_ip(),
            )
        except Exception:
            pass
        return jsonify({"success": True, "tenant": tenant})
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error(f"테넌트 업데이트 실패: {e}")
        return jsonify({"error": "테넌트 업데이트 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/tenants/<tenant_id>", methods=["DELETE"])
@jwt_auth.require_auth()
def admin_delete_tenant(tenant_id):
    """테넌트를 삭제한다."""
    try:
        tenant_manager.delete_tenant(tenant_id)
        try:
            audit_logger.log(
                actor=_get_audit_actor(), action="delete", resource_type="tenant",
                resource_id=tenant_id, ip=_get_client_ip(),
            )
        except Exception:
            pass
        return jsonify({"success": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"테넌트 삭제 실패: {e}")
        return jsonify({"error": "테넌트 삭제 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/tenants/<tenant_id>/faq", methods=["GET"])
@jwt_auth.require_auth()
def admin_tenant_faq(tenant_id):
    """테넌트별 FAQ 데이터를 반환한다."""
    try:
        faq = tenant_manager.get_tenant_faq(tenant_id)
        return jsonify(faq)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error(f"테넌트 FAQ 조회 실패: {e}")
        return jsonify({"error": "테넌트 FAQ 조회 중 오류가 발생했습니다."}), 500



# --- FAQ Manager CRUD API ---

@app.route("/admin/faq")
def admin_faq_page():
    """FAQ 관리 페이지를 반환한다."""
    return send_from_directory(os.path.join(BASE_DIR, "web"), "faq-manager.html")


@app.route("/api/admin/faq", methods=["GET"])
@jwt_auth.require_auth()
def admin_faq_list():
    """모든 FAQ 항목을 메타데이터와 함께 반환한다."""
    try:
        category = request.args.get("category", "").strip()
        search = request.args.get("search", "").strip().lower()
        items = faq_manager.list_all()

        if category:
            items = [it for it in items if it.get("category") == category]
        if search:
            items = [
                it for it in items
                if search in it.get("question", "").lower()
                or search in it.get("answer", "").lower()
                or search in it.get("id", "").lower()
            ]

        return jsonify({"items": items, "count": len(items)})
    except Exception as e:
        logger.error(f"FAQ 목록 조회 실패: {e}")
        return jsonify({"error": "FAQ 목록 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/faq", methods=["POST"])
@jwt_auth.require_auth()
def admin_faq_create():
    """새 FAQ 항목을 추가한다."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "요청 본문이 필요합니다."}), 400

    try:
        item = faq_manager.create(data)
        _reload_chatbot_faq()
        try:
            audit_logger.log(
                actor=_get_audit_actor(), action="create", resource_type="faq",
                resource_id=item.get("id"), details={"question": data.get("question", "")},
                ip=_get_client_ip(),
            )
        except Exception:
            pass
        return jsonify({"success": True, "item": item}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"FAQ 생성 실패: {e}")
        return jsonify({"error": "FAQ 생성 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/faq/<faq_id>", methods=["PUT"])
@jwt_auth.require_auth()
def admin_faq_update(faq_id):
    """FAQ 항목을 수정한다."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "요청 본문이 필요합니다."}), 400

    try:
        item = faq_manager.update(faq_id, data)
        _reload_chatbot_faq()
        try:
            audit_logger.log(
                actor=_get_audit_actor(), action="update", resource_type="faq",
                resource_id=faq_id, details={"fields": list(data.keys())},
                ip=_get_client_ip(),
            )
        except Exception:
            pass
        return jsonify({"success": True, "item": item})
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"FAQ 수정 실패: {e}")
        return jsonify({"error": "FAQ 수정 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/faq/<faq_id>", methods=["DELETE"])
@jwt_auth.require_auth()
def admin_faq_delete(faq_id):
    """FAQ 항목을 삭제한다."""
    try:
        deleted = faq_manager.delete(faq_id)
        _reload_chatbot_faq()
        try:
            audit_logger.log(
                actor=_get_audit_actor(), action="delete", resource_type="faq",
                resource_id=faq_id, ip=_get_client_ip(),
            )
        except Exception:
            pass
        return jsonify({"success": True, "deleted": deleted})
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error(f"FAQ 삭제 실패: {e}")
        return jsonify({"error": "FAQ 삭제 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/faq/<faq_id>/history", methods=["GET"])
@jwt_auth.require_auth()
def admin_faq_history(faq_id):
    """FAQ 항목의 변경 이력을 반환한다."""
    try:
        history = faq_manager.get_history(faq_id)
        return jsonify({"faq_id": faq_id, "history": history, "count": len(history)})
    except Exception as e:
        logger.error(f"FAQ 이력 조회 실패: {e}")
        return jsonify({"error": "FAQ 이력 조회 중 오류가 발생했습니다."}), 500


def _reload_chatbot_faq():
    """Reload chatbot FAQ data after a CRUD operation."""
    try:
        chatbot.faq_data = load_json("data/faq.json")
        chatbot.faq_items = chatbot.faq_data.get("items", [])
        chatbot.tfidf_matcher = __import__(
            "src.similarity", fromlist=["TFIDFMatcher"]
        ).TFIDFMatcher(chatbot.faq_items)
        chatbot.related_faq_finder = __import__(
            "src.related_faq", fromlist=["RelatedFAQFinder"]
        ).RelatedFAQFinder(chatbot.faq_items)
        _refresh_faq_cache()
        logger.info("FAQ data reloaded after CRUD operation")
    except Exception as e:
        logger.error(f"FAQ 리로드 실패: {e}")



# --- FAQ Import/Export endpoints ---

@app.route("/api/admin/faq/import", methods=["POST"])
@jwt_auth.require_auth()
def admin_faq_import():
    """Upload a CSV or JSON file and import FAQ items."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    strategy = request.form.get("strategy", "skip")
    fmt = request.form.get("format", "")
    if not fmt:
        if file.filename.endswith(".json"):
            fmt = "json"
        else:
            fmt = "csv"

    import tempfile as _tempfile
    tmp_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(tmp_dir, exist_ok=True)
    suffix = ".json" if fmt == "json" else ".csv"
    fd, tmp_path = _tempfile.mkstemp(dir=tmp_dir, suffix=suffix)
    try:
        file.save(tmp_path)
        os.close(fd)

        if fmt == "json":
            items = faq_importer.import_json(tmp_path)
        else:
            items = faq_importer.import_csv(tmp_path)

        result = faq_importer.merge_import(items, strategy=strategy)
        _reload_chatbot_faq()

        try:
            audit_logger.log(
                actor=_get_audit_actor(), action="import", resource_type="faq",
                details={"format": fmt, "strategy": strategy, **{k: v for k, v in result.items() if k != "errors"}},
                ip=_get_client_ip(),
            )
        except Exception:
            pass

        return jsonify({"success": True, **result})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"FAQ import failed: {e}")
        return jsonify({"error": "FAQ import failed"}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.route("/api/admin/faq/import/preview", methods=["POST"])
@jwt_auth.require_auth()
def admin_faq_import_preview():
    """Preview a file import without applying changes."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    fmt = request.form.get("format", "")
    if not fmt:
        if file.filename.endswith(".json"):
            fmt = "json"
        else:
            fmt = "csv"

    import tempfile as _tempfile
    tmp_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(tmp_dir, exist_ok=True)
    suffix = ".json" if fmt == "json" else ".csv"
    fd, tmp_path = _tempfile.mkstemp(dir=tmp_dir, suffix=suffix)
    try:
        file.save(tmp_path)
        os.close(fd)
        preview = faq_importer.preview_import(tmp_path, format=fmt)
        return jsonify(preview)
    except Exception as e:
        logger.error(f"FAQ import preview failed: {e}")
        return jsonify({"error": "Preview failed"}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.route("/api/admin/faq/export", methods=["GET"])
@jwt_auth.require_auth()
def admin_faq_export():
    """Export FAQ items as a downloadable CSV or JSON file."""
    fmt = request.args.get("format", "csv").lower()

    import tempfile as _tempfile
    tmp_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        if fmt == "json":
            fd, tmp_path = _tempfile.mkstemp(dir=tmp_dir, suffix=".json")
            os.close(fd)
            faq_exporter.export_json(tmp_path)
            with open(tmp_path, "r", encoding="utf-8") as f:
                content = f.read()
            os.unlink(tmp_path)
            return Response(
                content,
                mimetype="application/json",
                headers={"Content-Disposition": "attachment; filename=faq_export.json"},
            )
        else:
            fd, tmp_path = _tempfile.mkstemp(dir=tmp_dir, suffix=".csv")
            os.close(fd)
            faq_exporter.export_csv(tmp_path)
            with open(tmp_path, "r", encoding="utf-8") as f:
                content = f.read()
            os.unlink(tmp_path)
            return Response(
                content,
                mimetype="text/csv",
                headers={"Content-Disposition": "attachment; filename=faq_export.csv"},
            )
    except Exception as e:
        logger.error(f"FAQ export failed: {e}")
        return jsonify({"error": "FAQ export failed"}), 500


# --- FAQ Snapshot / Diff API endpoints ---

@app.route("/api/admin/faq/snapshot", methods=["POST"])
@jwt_auth.require_auth()
def admin_faq_snapshot():
    """Create a snapshot of the current FAQ state."""
    data = request.get_json(silent=True) or {}
    label = data.get("label")
    try:
        result = faq_diff_engine.snapshot(label=label)
        return jsonify(result), 201
    except Exception as e:
        logger.error(f"FAQ snapshot failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/faq/snapshots", methods=["GET"])
@jwt_auth.require_auth()
def admin_faq_snapshots():
    """List all FAQ snapshots."""
    try:
        snapshots = faq_diff_engine.list_snapshots()
        return jsonify({"snapshots": snapshots})
    except Exception as e:
        logger.error(f"FAQ snapshot listing failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/faq/diff", methods=["GET"])
@jwt_auth.require_auth()
def admin_faq_diff():
    """Compare two FAQ snapshots."""
    a = request.args.get("a")
    b = request.args.get("b")
    if not a or not b:
        return jsonify({"error": "Both 'a' and 'b' snapshot IDs are required"}), 400
    try:
        a_id = int(a)
        b_id = int(b)
    except (ValueError, TypeError):
        return jsonify({"error": "Snapshot IDs must be integers"}), 400
    try:
        diff_result = faq_diff_engine.diff(a_id, b_id)
        summary = faq_diff_engine.get_change_summary(diff_result)
        return jsonify({"diff": diff_result, "summary": summary})
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error(f"FAQ diff failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/faq/rollback", methods=["POST"])
@jwt_auth.require_auth()
def admin_faq_rollback():
    """Rollback FAQ data to a specific snapshot."""
    data = request.get_json(silent=True) or {}
    snapshot_id = data.get("snapshot_id")
    if snapshot_id is None:
        return jsonify({"error": "snapshot_id is required"}), 400
    try:
        snapshot_id = int(snapshot_id)
    except (ValueError, TypeError):
        return jsonify({"error": "snapshot_id must be an integer"}), 400
    try:
        count = faq_diff_engine.rollback_to(snapshot_id)
        return jsonify({"restored_items": count, "snapshot_id": snapshot_id})
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error(f"FAQ rollback failed: {e}")
        return jsonify({"error": str(e)}), 500


# --- Webhook API endpoints ---

@app.route("/api/admin/webhooks", methods=["POST"])
@jwt_auth.require_auth()
def admin_webhook_register():
    """Register a new webhook subscription."""
    data = request.get_json(silent=True)
    if not data or "url" not in data or "events" not in data:
        return jsonify({"error": "url과 events 필드가 필요합니다."}), 400

    url = data["url"]
    events = data["events"]
    secret = data.get("secret")

    if not isinstance(events, list) or not events:
        return jsonify({"error": "events는 비어 있지 않은 배열이어야 합니다."}), 400

    try:
        subscription_id = webhook_manager.register(url, events, secret)
        try:
            audit_logger.log(
                actor=_get_audit_actor(), action="create", resource_type="webhook",
                resource_id=subscription_id, details={"url": url, "events": events},
                ip=_get_client_ip(),
            )
        except Exception:
            pass
        return jsonify({"success": True, "subscription_id": subscription_id}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"웹훅 등록 실패: {e}")
        return jsonify({"error": "웹훅 등록 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/webhooks", methods=["GET"])
@jwt_auth.require_auth()
def admin_webhook_list():
    """List all active webhook subscriptions."""
    try:
        subscriptions = webhook_manager.list_subscriptions()
        return jsonify({"subscriptions": subscriptions, "count": len(subscriptions)})
    except Exception as e:
        logger.error(f"웹훅 목록 조회 실패: {e}")
        return jsonify({"error": "웹훅 목록 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/webhooks/<subscription_id>", methods=["DELETE"])
@jwt_auth.require_auth()
def admin_webhook_unregister(subscription_id):
    """Unregister a webhook subscription."""
    try:
        removed = webhook_manager.unregister(subscription_id)
        if removed:
            try:
                audit_logger.log(
                    actor=_get_audit_actor(), action="delete", resource_type="webhook",
                    resource_id=subscription_id, ip=_get_client_ip(),
                )
            except Exception:
                pass
            return jsonify({"success": True, "subscription_id": subscription_id})
        else:
            return jsonify({"error": "구독을 찾을 수 없습니다."}), 404
    except Exception as e:
        logger.error(f"웹훅 해제 실패: {e}")
        return jsonify({"error": "웹훅 해제 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/webhooks/<subscription_id>/deliveries", methods=["GET"])
@jwt_auth.require_auth()
def admin_webhook_deliveries(subscription_id):
    """Get delivery log for a webhook subscription."""
    try:
        limit = request.args.get("limit", 50, type=int)
        deliveries = webhook_manager.get_delivery_log(subscription_id=subscription_id, limit=limit)
        return jsonify({"deliveries": deliveries, "count": len(deliveries)})
    except Exception as e:
        logger.error(f"웹훅 배달 로그 조회 실패: {e}")
        return jsonify({"error": "웹훅 배달 로그 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/webhooks/test", methods=["POST"])
@jwt_auth.require_auth()
def admin_webhook_test():
    """Send a test event to all subscribers of the given event type."""
    data = request.get_json(silent=True)
    event_type = data.get("event_type", "query.received") if data else "query.received"

    test_payload = {
        "test": True,
        "message": "This is a test webhook delivery.",
    }

    try:
        count = webhook_manager.emit(event_type, test_payload)
        return jsonify({"success": True, "subscribers_notified": count, "event_type": event_type})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"테스트 웹훅 전송 실패: {e}")
        return jsonify({"error": "테스트 웹훅 전송 중 오류가 발생했습니다."}), 500


# --- Alert Center API endpoints ---

@app.route("/api/admin/alerts", methods=["GET"])
@jwt_auth.require_auth()
def admin_alerts_list():
    """List alerts with optional filters."""
    try:
        unread_only = request.args.get("unread_only", "false").lower() == "true"
        severity = request.args.get("severity")
        category = request.args.get("category")
        limit = int(request.args.get("limit", "50"))
        alerts = alert_center.get_alerts(
            unread_only=unread_only, severity=severity, category=category, limit=limit,
        )
        return jsonify({"alerts": alerts, "count": len(alerts)})
    except Exception as e:
        logger.error(f"알림 목록 조회 실패: {e}")
        return jsonify({"error": "알림 목록 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/alerts/count", methods=["GET"])
@jwt_auth.require_auth()
def admin_alerts_count():
    """Get unread alert count."""
    try:
        count = alert_center.get_unread_count()
        return jsonify({"unread_count": count})
    except Exception as e:
        logger.error(f"알림 카운트 조회 실패: {e}")
        return jsonify({"error": "알림 카운트 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/alerts/<alert_id>/read", methods=["POST"])
@jwt_auth.require_auth()
def admin_alert_mark_read(alert_id):
    """Mark a single alert as read."""
    try:
        found = alert_center.mark_read(alert_id)
        if not found:
            return jsonify({"error": "알림을 찾을 수 없습니다."}), 404
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"알림 읽음 처리 실패: {e}")
        return jsonify({"error": "알림 읽음 처리 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/alerts/read-all", methods=["POST"])
@jwt_auth.require_auth()
def admin_alerts_mark_all_read():
    """Mark all alerts as read."""
    try:
        count = alert_center.mark_all_read()
        return jsonify({"success": True, "updated_count": count})
    except Exception as e:
        logger.error(f"전체 알림 읽음 처리 실패: {e}")
        return jsonify({"error": "전체 알림 읽음 처리 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/alerts/<alert_id>", methods=["DELETE"])
@jwt_auth.require_auth()
def admin_alert_delete(alert_id):
    """Delete an alert."""
    try:
        found = alert_center.delete_alert(alert_id)
        if not found:
            return jsonify({"error": "알림을 찾을 수 없습니다."}), 404
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"알림 삭제 실패: {e}")
        return jsonify({"error": "알림 삭제 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/alerts/check", methods=["POST"])
@jwt_auth.require_auth()
def admin_alerts_run_checks():
    """Manually run all alert rule checks."""
    try:
        new_alerts = alert_rule_engine.run_all_checks()
        return jsonify({"success": True, "new_alerts": new_alerts, "count": len(new_alerts)})
    except Exception as e:
        logger.error(f"알림 규칙 실행 실패: {e}")
        return jsonify({"error": "알림 규칙 실행 중 오류가 발생했습니다."}), 500


# --- Audit Log API endpoints ---


@app.route("/api/admin/audit", methods=["GET"])
@jwt_auth.require_auth()
def admin_audit_logs():
    """Query audit logs with optional filters."""
    try:
        actor = request.args.get("actor")
        action = request.args.get("action")
        resource_type = request.args.get("resource_type")
        since = request.args.get("since")
        limit = request.args.get("limit", 100, type=int)

        logs = audit_logger.get_logs(
            actor=actor, action=action, resource_type=resource_type,
            since=since, limit=limit,
        )
        return jsonify({"logs": logs, "count": len(logs)})
    except Exception as e:
        logger.error(f"감사 로그 조회 실패: {e}")
        return jsonify({"error": "감사 로그 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/audit/stats", methods=["GET"])
@jwt_auth.require_auth()
def admin_audit_stats():
    """Get audit statistics (actions per day, top actors)."""
    try:
        since = request.args.get("since")
        stats = audit_logger.get_stats(since=since)
        return jsonify(stats)
    except Exception as e:
        logger.error(f"감사 통계 조회 실패: {e}")
        return jsonify({"error": "감사 통계 조회 중 오류가 발생했습니다."}), 500




# --- Profiler API endpoints ---

@app.route("/api/admin/profiler/start", methods=["POST"])
def admin_profiler_start():
    """Enable request profiling."""
    if request_profiler.is_profiling:
        return jsonify({"message": "Profiling is already active."}), 200
    request_profiler.start_profiling()
    return jsonify({"success": True, "message": "Profiling started."})


@app.route("/api/admin/profiler/stop", methods=["POST"])
def admin_profiler_stop():
    """Disable request profiling and return accumulated results."""
    if not request_profiler.is_profiling:
        return jsonify({"error": "Profiling is not active."}), 400
    results = request_profiler.stop_profiling()
    return jsonify({"success": True, "results": results})


@app.route("/api/admin/profiler/benchmark", methods=["GET"])
def admin_profiler_benchmark():
    """Run component benchmarks with small iteration counts."""
    iterations = request.args.get("iterations", 5, type=int)
    iterations = max(1, min(iterations, 1000))
    try:
        results = {
            "classifier": component_benchmark.benchmark_classifier(iterations=iterations),
            "tfidf": component_benchmark.benchmark_tfidf(iterations=iterations),
            "bm25": component_benchmark.benchmark_bm25(iterations=iterations),
            "full_pipeline": component_benchmark.benchmark_full_pipeline(iterations=max(1, iterations // 2)),
        }
        return jsonify({"success": True, "benchmarks": results})
    except Exception as e:
        logger.error(f"벤치마크 실행 실패: {e}")
        return jsonify({"error": "벤치마크 실행 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/profiler/status", methods=["GET"])
def admin_profiler_status():
    """Return current profiling status."""
    return jsonify({
        "profiling": request_profiler.is_profiling,
        "summary": request_profiler.get_summary(),
    })


# --- I18n API endpoints ---

@app.route("/api/i18n/languages", methods=["GET"])
def i18n_languages():
    """지원 언어 목록을 반환한다."""
    return jsonify({"languages": i18n_manager.get_supported_languages()})


@app.route("/api/i18n/<lang>", methods=["GET"])
def i18n_locale(lang):
    """특정 언어의 번역 파일을 반환한다."""
    data = i18n_manager.load_locale(lang)
    if not data:
        return jsonify({"error": f"Locale '{lang}' not found"}), 404
    return jsonify(data)


# --- Advanced Rate Limit admin endpoints ---

@app.route("/api/admin/rate-limits", methods=["GET"])
def admin_rate_limits_get():
    """Return current rate limit configuration."""
    stats = advanced_rate_limiter.get_usage_stats()
    return jsonify({
        "endpoint_limits": stats["endpoint_limits"],
        "default_daily_quota": stats["default_daily_quota"],
    })


@app.route("/api/admin/rate-limits", methods=["PUT"])
def admin_rate_limits_update():
    """Update rate limit configuration."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    updated = []
    if "endpoint_limits" in data:
        for pattern, rpm in data["endpoint_limits"].items():
            if not isinstance(rpm, (int, float)) or rpm <= 0:
                return jsonify({"error": f"Invalid limit for {pattern}"}), 400
            advanced_rate_limiter.set_endpoint_limit(pattern, int(rpm))
            updated.append(pattern)

    if "default_daily_quota" in data:
        quota = data["default_daily_quota"]
        if not isinstance(quota, (int, float)) or quota <= 0:
            return jsonify({"error": "Invalid daily quota"}), 400
        advanced_rate_limiter._default_daily_quota = int(quota)

    if "user_quotas" in data:
        for api_key, limit in data["user_quotas"].items():
            if not isinstance(limit, (int, float)) or limit <= 0:
                return jsonify({"error": f"Invalid quota for {api_key}"}), 400
            advanced_rate_limiter.set_user_quota(api_key, int(limit))

    return jsonify({"status": "updated", "updated_endpoints": updated})


@app.route("/api/admin/usage", methods=["GET"])
def admin_usage():
    """Return usage dashboard data: top users and endpoint stats."""
    limit = request.args.get("limit", 10, type=int)
    api_key = request.args.get("api_key")

    stats = advanced_rate_limiter.get_usage_stats(api_key=api_key)
    top_users = advanced_rate_limiter.get_top_users(limit=limit)

    return jsonify({
        "stats": stats,
        "top_users": top_users,
    })


# --- Conversation Manager V3 API endpoints ---

@app.route("/api/session/<session_id>/conversation-summary", methods=["GET"])
def session_conversation_summary_v3(session_id):
    """Return a summary of the v3 conversation for the session."""
    try:
        summary = conversation_manager_v3.get_conversation_summary(session_id)
        return jsonify(summary)
    except Exception as e:  # pragma: no cover - defensive
        logger.error(f"conversation-summary 조회 실패: {e}")
        return jsonify({"error": "대화 요약 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/session/<session_id>/topic-path", methods=["GET"])
def session_topic_path_v3(session_id):
    """Return the category journey for the session."""
    try:
        path = conversation_manager_v3.topic_tracker.get_topic_path(session_id)
        coherent = conversation_manager_v3.topic_tracker.is_coherent(session_id)
        return jsonify({
            "session_id": session_id,
            "topic_path": path,
            "coherent": coherent,
            "length": len(path),
        })
    except Exception as e:  # pragma: no cover - defensive
        logger.error(f"topic-path 조회 실패: {e}")
        return jsonify({"error": "토픽 경로 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/session/<session_id>/followup", methods=["POST"])
def session_followup_v3(session_id):
    """Return a suggested follow-up question for the session."""
    try:
        question = conversation_manager_v3.generate_followup_question(session_id)
        return jsonify({
            "session_id": session_id,
            "followup": question,
        })
    except Exception as e:  # pragma: no cover - defensive
        logger.error(f"followup 생성 실패: {e}")
        return jsonify({"error": "후속 질문 생성 중 오류가 발생했습니다."}), 500


# --- Conversation Summary API endpoints ---

@app.route("/api/session/<session_id>/summary", methods=["GET"])
def session_summary(session_id):
    """세션 대화 요약을 반환한다."""
    summary = conversation_summarizer.summarize_session(session_id)
    if summary is None:
        return jsonify({"error": "세션을 찾을 수 없거나 만료되었습니다."}), 404
    return jsonify(summary)


@app.route("/api/admin/sessions/summaries", methods=["GET"])
def admin_sessions_summaries():
    """특정 날짜의 세션 일괄 요약을 반환한다."""
    date_str = request.args.get("date", "")
    # Collect all active session IDs
    all_sessions = chatbot.session_manager._sessions
    session_ids = []
    for sid, session in all_sessions.items():
        if date_str:
            session_date = datetime.fromtimestamp(session.created_at).strftime("%Y-%m-%d")
            if session_date == date_str:
                session_ids.append(sid)
        else:
            session_ids.append(sid)
    summaries = conversation_summarizer.summarize_batch(session_ids)
    return jsonify({"date": date_str, "count": len(summaries), "summaries": summaries})


@app.route("/api/admin/sessions/topics", methods=["GET"])
def admin_sessions_topics():
    """전체 세션에서 상위 대화 토픽을 반환한다."""
    all_sessions = chatbot.session_manager._sessions
    all_messages = []
    for session in all_sessions.values():
        all_messages.extend(session.history)
    keyword_extractor = conversation_summarizer.keyword_extractor
    topics = keyword_extractor.extract_topics(all_messages)
    return jsonify({"count": len(topics), "topics": topics})


# --- Database Migration API endpoints ---

@app.route("/api/admin/migrations", methods=["GET"])
def admin_migrations_status():
    """Return current migration version and pending migrations."""
    current = migration_manager.get_current_version()
    pending = migration_manager.get_pending_migrations()
    history = migration_manager.get_migration_history()
    validation = migration_manager.validate_migrations()
    return jsonify({
        "current_version": current,
        "pending": [{"version": v, "name": n} for v, n, _ in pending],
        "history": history,
        "valid": validation["valid"],
        "errors": validation["errors"],
    })


@app.route("/api/admin/migrations/apply", methods=["POST"])
def admin_migrations_apply():
    """Apply pending migrations."""
    data = request.get_json(silent=True) or {}
    target_version = data.get("target_version")
    try:
        applied = migration_manager.migrate(target_version=target_version)
        return jsonify({
            "success": True,
            "applied": applied,
            "current_version": migration_manager.get_current_version(),
        })
    except Exception as e:
        logger.error(f"Migration apply failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/migrations/rollback", methods=["POST"])
def admin_migrations_rollback():
    """Rollback the last N migrations."""
    data = request.get_json(silent=True) or {}
    steps = data.get("steps", 1)
    try:
        rolled_back = migration_manager.rollback(steps=steps)
        return jsonify({
            "success": True,
            "rolled_back": rolled_back,
            "current_version": migration_manager.get_current_version(),
        })
    except Exception as e:
        logger.error(f"Migration rollback failed: {e}")
        return jsonify({"error": str(e)}), 500


# --- Health Monitor API endpoints ---


@app.route("/api/admin/health/detailed", methods=["GET"])
def admin_health_detailed():
    """전체 헬스 리포트를 반환한다."""
    try:
        report = health_monitor.check_all()
        report["system_info"] = health_monitor.get_system_info()
        return jsonify(report)
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({"error": "헬스 체크 실행 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/health/components", methods=["GET"])
def admin_health_components():
    """개별 구성 요소의 상태를 반환한다."""
    try:
        component = request.args.get("component")
        if component:
            check_map = {
                "database": health_monitor.check_database,
                "faq_data": health_monitor.check_faq_data,
                "disk_space": health_monitor.check_disk_space,
                "memory_usage": health_monitor.check_memory_usage,
                "response_times": health_monitor.check_response_times,
                "error_rate": health_monitor.check_error_rate,
            }
            check_fn = check_map.get(component)
            if not check_fn:
                return jsonify({"error": f"Unknown component: {component}"}), 400
            return jsonify({component: check_fn()})
        report = health_monitor.check_all()
        return jsonify(report["components"])
    except Exception as e:
        logger.error(f"Component health check failed: {e}")
        return jsonify({"error": "구성 요소 상태 확인 중 오류가 발생했습니다."}), 500


@app.route("/health-dashboard")
def health_dashboard():
    """헬스 모니터링 대시보드 페이지를 반환한다."""
    return send_from_directory(os.path.join(BASE_DIR, "web"), "health.html")


@app.route("/admin/notifications")
def admin_notifications_page():
    """관리자 알림 센터 페이지를 반환한다."""
    return send_from_directory(os.path.join(BASE_DIR, "web"), "notifications.html")


@app.route("/admin/analytics")
def admin_analytics_page():
    """관리자 분석 대시보드 페이지를 반환한다."""
    return send_from_directory(os.path.join(BASE_DIR, "web"), "analytics-dashboard.html")


# ── A/B Testing API ──────────────────────────────────────────────────────

@app.route("/api/admin/ab-tests", methods=["POST"])
@jwt_auth.require_auth()
def create_ab_test():
    """A/B 테스트를 생성한다."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "요청 본문이 필요합니다."}), 400

    name = data.get("name")
    faq_id = data.get("faq_id")
    variants = data.get("variants")

    try:
        result = ab_test_manager.create_test(name, faq_id, variants)
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"A/B 테스트 생성 실패: {e}")
        return jsonify({"error": "A/B 테스트 생성 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/ab-tests", methods=["GET"])
@jwt_auth.require_auth()
def list_ab_tests():
    """A/B 테스트 목록을 반환한다."""
    active_only = request.args.get("active_only", "true").lower() == "true"
    try:
        tests = ab_test_manager.list_tests(active_only=active_only)
        return jsonify({"tests": tests, "count": len(tests)})
    except Exception as e:
        logger.error(f"A/B 테스트 목록 조회 실패: {e}")
        return jsonify({"error": "A/B 테스트 목록 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/ab-tests/<test_id>/results", methods=["GET"])
@jwt_auth.require_auth()
def get_ab_test_results(test_id):
    """A/B 테스트 결과를 반환한다."""
    try:
        results = ab_test_manager.get_results(test_id)
        if not results:
            return jsonify({"error": "테스트를 찾을 수 없습니다."}), 404
        return jsonify(results)
    except Exception as e:
        logger.error(f"A/B 테스트 결과 조회 실패: {e}")
        return jsonify({"error": "A/B 테스트 결과 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/ab-tests/<test_id>/stop", methods=["POST"])
@jwt_auth.require_auth()
def stop_ab_test(test_id):
    """A/B 테스트를 중지한다."""
    try:
        stopped = ab_test_manager.stop_test(test_id)
        if not stopped:
            return jsonify({"error": "활성 테스트를 찾을 수 없습니다."}), 404
        return jsonify({"success": True, "test_id": test_id})
    except Exception as e:
        logger.error(f"A/B 테스트 중지 실패: {e}")
        return jsonify({"error": "A/B 테스트 중지 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/ab-tests/<test_id>/apply-winner", methods=["POST"])
@jwt_auth.require_auth()
def apply_ab_test_winner(test_id):
    """A/B 테스트 우승 변형을 FAQ에 적용한다."""
    try:
        result = ab_test_manager.apply_winner(test_id)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"A/B 테스트 우승 적용 실패: {e}")
        return jsonify({"error": "A/B 테스트 우승 적용 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/flow/sankey", methods=["GET"])
@jwt_auth.require_auth()
def flow_sankey():
    """Sankey diagram data for conversation flows."""
    try:
        data = flow_analyzer.generate_sankey_data()
        return jsonify(data)
    except Exception as e:
        logger.error(f"Flow sankey data error: {e}")
        return jsonify({"error": "Failed to generate Sankey data."}), 500


@app.route("/api/admin/flow/paths", methods=["GET"])
@jwt_auth.require_auth()
def flow_paths():
    """Common conversation paths."""
    try:
        top_n = request.args.get("top_n", 10, type=int)
        data = flow_analyzer.get_common_paths(top_n=top_n)
        return jsonify({"paths": data})
    except Exception as e:
        logger.error(f"Flow paths error: {e}")
        return jsonify({"error": "Failed to get conversation paths."}), 500


@app.route("/api/admin/flow/dropoff", methods=["GET"])
@jwt_auth.require_auth()
def flow_dropoff():
    """Drop-off analysis for conversation flows."""
    try:
        data = flow_analyzer.get_drop_off_points()
        return jsonify({"drop_off_points": data})
    except Exception as e:
        logger.error(f"Flow drop-off error: {e}")
        return jsonify({"error": "Failed to get drop-off analysis."}), 500


@app.route("/api/admin/flow/transitions", methods=["GET"])
@jwt_auth.require_auth()
def flow_transitions():
    """Transition matrix for conversation flows."""
    try:
        data = flow_analyzer.get_transition_matrix()
        return jsonify({"transitions": data})
    except Exception as e:
        logger.error(f"Flow transitions error: {e}")
        return jsonify({"error": "Failed to get transition matrix."}), 500


@app.route("/api/admin/sentiment", methods=["GET"])
@jwt_auth.require_auth()
def admin_sentiment_stats():
    """감정 분석 통계를 반환한다."""
    try:
        session_id = request.args.get("session_id")
        stats = sentiment_analyzer.get_sentiment_stats(session_id=session_id)
        return jsonify(stats)
    except Exception as e:
        logger.error(f"감정 분석 통계 조회 실패: {e}")
        return jsonify({"error": "감정 분석 통계 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/sentiment/history", methods=["GET"])
@jwt_auth.require_auth()
def admin_sentiment_history():
    """감정 분석 이력을 반환한다."""
    try:
        session_id = request.args.get("session_id")
        limit = request.args.get("limit", 50, type=int)
        history = sentiment_analyzer.get_sentiment_history(session_id=session_id, limit=limit)
        return jsonify({"history": history, "count": len(history)})
    except Exception as e:
        logger.error(f"감정 분석 이력 조회 실패: {e}")
        return jsonify({"error": "감정 분석 이력 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/clusters", methods=["GET"])
@jwt_auth.require_auth()
def admin_clusters():
    """질문 클러스터를 반환한다."""
    try:
        threshold = request.args.get("threshold", 0.5, type=float)
        clusters = question_clusterer.cluster_questions(threshold=threshold)
        stats = question_clusterer.get_cluster_stats()
        return jsonify({"clusters": clusters, "stats": stats})
    except Exception as e:
        logger.error(f"클러스터 조회 실패: {e}")
        return jsonify({"error": "클러스터 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/duplicates", methods=["GET"])
@jwt_auth.require_auth()
def admin_duplicates():
    """중복 감지 리포트를 반환한다."""
    try:
        report = duplicate_detector.generate_report()
        return jsonify(report)
    except Exception as e:
        logger.error(f"중복 감지 실패: {e}")
        return jsonify({"error": "중복 감지 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/similar", methods=["GET"])
@jwt_auth.require_auth()
def admin_similar():
    """유사 질문을 검색한다."""
    try:
        query = request.args.get("q", "")
        top_k = request.args.get("top_k", 5, type=int)
        if not query:
            return jsonify({"error": "q 파라미터가 필요합니다."}), 400
        results = question_clusterer.find_similar_to(query, top_k=top_k)
        return jsonify({"query": query, "results": results, "count": len(results)})
    except Exception as e:
        logger.error(f"유사 질문 검색 실패: {e}")
        return jsonify({"error": "유사 질문 검색 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/clusters/refresh", methods=["POST"])
@jwt_auth.require_auth()
def admin_clusters_refresh():
    """클러스터를 재계산한다."""
    global question_clusterer, duplicate_detector
    try:
        question_clusterer = QuestionClusterer(chatbot.faq_items)
        duplicate_detector = DuplicateDetector(chatbot.faq_items)
        threshold = request.args.get("threshold", 0.5, type=float)
        clusters = question_clusterer.cluster_questions(threshold=threshold)
        stats = question_clusterer.get_cluster_stats()
        return jsonify({"message": "클러스터가 재계산되었습니다.", "clusters": clusters, "stats": stats})
    except Exception as e:
        logger.error(f"클러스터 재계산 실패: {e}")
        return jsonify({"error": "클러스터 재계산 중 오류가 발생했습니다."}), 500


# --- Task Scheduler API ---

@app.route("/api/admin/scheduler/tasks", methods=["GET"])
@jwt_auth.require_auth()
def scheduler_list_tasks():
    """등록된 스케줄러 작업 목록을 반환한다."""
    try:
        tasks = task_scheduler.list_tasks()
        return jsonify({"tasks": tasks, "count": len(tasks)})
    except Exception as e:
        logger.error(f"스케줄러 작업 목록 조회 실패: {e}")
        return jsonify({"error": "스케줄러 작업 목록 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/scheduler/tasks/<name>/run", methods=["POST"])
@jwt_auth.require_auth()
def scheduler_run_task(name):
    """스케줄러 작업을 수동 실행한다."""
    try:
        result = task_scheduler.run_task(name)
        return jsonify(result)
    except KeyError:
        return jsonify({"error": f"Task not found: {name}"}), 404
    except Exception as e:
        logger.error(f"스케줄러 작업 실행 실패: {e}")
        return jsonify({"error": "스케줄러 작업 실행 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/scheduler/tasks/<name>", methods=["PUT"])
@jwt_auth.require_auth()
def scheduler_update_task(name):
    """스케줄러 작업을 활성화/비활성화한다."""
    try:
        data = request.get_json() or {}
        if "enabled" in data:
            task_scheduler.set_task_enabled(name, bool(data["enabled"]))
        status = task_scheduler.get_task_status(name)
        return jsonify(status)
    except KeyError:
        return jsonify({"error": f"Task not found: {name}"}), 404
    except Exception as e:
        logger.error(f"스케줄러 작업 업데이트 실패: {e}")
        return jsonify({"error": "스케줄러 작업 업데이트 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/scheduler/log", methods=["GET"])
@jwt_auth.require_auth()
def scheduler_execution_log():
    """스케줄러 실행 이력을 반환한다."""
    try:
        task_name = request.args.get("task_name")
        limit = request.args.get("limit", 50, type=int)
        logs = task_scheduler.get_execution_log(task_name=task_name, limit=limit)
        return jsonify({"logs": logs, "count": len(logs)})
    except Exception as e:
        logger.error(f"스케줄러 실행 이력 조회 실패: {e}")
        return jsonify({"error": "스케줄러 실행 이력 조회 중 오류가 발생했습니다."}), 500



# ---------------------------------------------------------------------------
# Knowledge Graph API
# ---------------------------------------------------------------------------


@app.route("/api/admin/knowledge/graph", methods=["GET"])
@jwt_auth.require_auth()
def admin_knowledge_graph():
    """전체 지식 그래프를 반환한다."""
    try:
        data = knowledge_graph.export_graph()
        stats = knowledge_graph.get_graph_stats()
        return jsonify({"graph": data, "stats": stats})
    except Exception as e:
        logger.error(f"지식 그래프 조회 실패: {e}")
        return jsonify({"error": "지식 그래프 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/knowledge/node/<node_id>", methods=["GET"])
@jwt_auth.require_auth()
def admin_knowledge_node(node_id):
    """노드 정보 및 이웃 노드를 반환한다."""
    try:
        if node_id not in knowledge_graph.nodes:
            return jsonify({"error": f"Node '{node_id}' not found"}), 404
        node = knowledge_graph.nodes[node_id]
        neighbors = knowledge_graph.get_neighbors(node_id, depth=1)
        return jsonify({"node": node, "neighbors": neighbors})
    except Exception as e:
        logger.error(f"노드 조회 실패: {e}")
        return jsonify({"error": "노드 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/knowledge/path", methods=["GET"])
@jwt_auth.require_auth()
def admin_knowledge_path():
    """두 노드 사이의 최단 경로를 반환한다."""
    try:
        source = request.args.get("from")
        target = request.args.get("to")
        if not source or not target:
            return jsonify({"error": "'from' and 'to' parameters are required"}), 400
        if source not in knowledge_graph.nodes:
            return jsonify({"error": f"Node '{source}' not found"}), 404
        if target not in knowledge_graph.nodes:
            return jsonify({"error": f"Node '{target}' not found"}), 404
        path = knowledge_graph.find_path(source, target)
        return jsonify({"path": path, "length": len(path)})
    except Exception as e:
        logger.error(f"경로 탐색 실패: {e}")
        return jsonify({"error": "경로 탐색 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/knowledge/rebuild", methods=["POST"])
@jwt_auth.require_auth()
def admin_knowledge_rebuild():
    """지식 그래프를 재구축한다."""
    global knowledge_graph
    try:
        knowledge_graph = KnowledgeGraph.build_from_faq(chatbot.faq_items, chatbot.legal_refs)
        stats = knowledge_graph.get_graph_stats()
        return jsonify({"message": "지식 그래프가 재구축되었습니다.", "stats": stats})
    except Exception as e:
        logger.error(f"지식 그래프 재구축 실패: {e}")
        return jsonify({"error": "지식 그래프 재구축 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/segments", methods=["GET"])
@jwt_auth.require_auth()
def admin_segment_stats():
    """사용자 세그먼트 분포 통계를 반환한다."""
    try:
        stats = user_segmenter.get_segment_stats()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"세그먼트 통계 조회 실패: {e}")
        return jsonify({"error": "세그먼트 통계 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/segments/<session_id>", methods=["GET"])
@jwt_auth.require_auth()
def admin_segment_info(session_id):
    """특정 사용자의 세그먼트 정보를 반환한다."""
    try:
        info = user_segmenter.get_segment_info(session_id)
        if info is None:
            return jsonify({"error": "세그먼트 정보를 찾을 수 없습니다."}), 404
        return jsonify(info)
    except Exception as e:
        logger.error(f"세그먼트 정보 조회 실패: {e}")
        return jsonify({"error": "세그먼트 정보 조회 중 오류가 발생했습니다."}), 500



# ---- Template Admin API ------------------------------------------------

@app.route('/api/admin/templates', methods=['GET'])
@jwt_auth.require_auth()
def list_templates_api():
    """등록된 템플릿 목록을 반환한다."""
    names = template_engine.list_templates()
    return jsonify({'templates': names, 'count': len(names)})


@app.route('/api/admin/templates', methods=['POST'])
@jwt_auth.require_auth()
def create_template_api():
    """새 템플릿을 등록한다."""
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    tpl_content = data.get('content', '')
    if not name:
        return jsonify({'error': '템플릿 이름이 필요합니다.'}), 400
    if not tpl_content:
        return jsonify({'error': '템플릿 내용이 필요합니다.'}), 400
    try:
        template_engine.register_template(name, tpl_content)
        return jsonify({'message': f"템플릿 '{name}' 생성 완료.", 'name': name}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/admin/templates/<name>', methods=['PUT'])
@jwt_auth.require_auth()
def update_template_api(name):
    """기존 템플릿을 수정한다."""
    data = request.get_json(silent=True) or {}
    tpl_content = data.get('content', '')
    if not tpl_content:
        return jsonify({'error': '템플릿 내용이 필요합니다.'}), 400
    try:
        template_engine.get_template(name)
    except KeyError:
        return jsonify({'error': f"템플릿 '{name}'을(를) 찾을 수 없습니다."}), 404
    template_engine.register_template(name, tpl_content)
    return jsonify({'message': f"템플릿 '{name}' 수정 완료.", 'name': name})


@app.route('/api/admin/templates/<name>', methods=['DELETE'])
@jwt_auth.require_auth()
def delete_template_api(name):
    """템플릿을 삭제한다."""
    try:
        template_engine.delete_template(name)
        return jsonify({'message': f"템플릿 '{name}' 삭제 완료."})
    except KeyError:
        return jsonify({'error': f"템플릿 '{name}'을(를) 찾을 수 없습니다."}), 404


@app.route('/api/admin/templates/preview', methods=['POST'])
@jwt_auth.require_auth()
def preview_template_api():
    """템플릿 미리보기를 렌더링한다."""
    data = request.get_json(silent=True) or {}
    template_name = data.get('template_name')
    template_str = data.get('template_str')
    ctx = data.get('context', {})
    if not template_name and not template_str:
        return jsonify({'error': 'template_name 또는 template_str이 필요합니다.'}), 400
    try:
        if template_str:
            result = template_engine.render_string(template_str, ctx)
        else:
            result = template_engine.render(template_name, ctx)
        return jsonify({'rendered': result})
    except KeyError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# ---------------------------------------------------------------------------
# 도메인 설정 API
# ---------------------------------------------------------------------------

@app.route("/api/admin/domain", methods=["GET"])
@jwt_auth.require_auth()
def get_domain_config_api():
    """현재 도메인 설정을 반환한다."""
    if _domain_config.loaded:
        return jsonify(_domain_config.to_dict())
    return jsonify({"error": "도메인 설정이 로드되지 않았습니다."}), 404


@app.route("/api/admin/domain", methods=["PUT"])
@jwt_auth.require_auth()
def update_domain_config_api():
    """도메인 설정을 업데이트한다."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON 본문이 필요합니다."}), 400
    _domain_config.load_dict(data)
    validation = _domain_config.validate()
    if not validation["valid"]:
        return jsonify({"error": "설정 검증 실패", "details": validation}), 400
    return jsonify({"message": "도메인 설정이 업데이트되었습니다.", "config": _domain_config.to_dict()})


@app.route("/api/admin/domain/validate", methods=["POST"])
@jwt_auth.require_auth()
def validate_domain_config_api():
    """도메인 설정을 검증한다."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON 본문이 필요합니다."}), 400
    temp = DomainConfig()
    temp.load_dict(data)
    result = temp.validate()
    return jsonify(result)


@app.route("/api/admin/domain/template", methods=["GET"])
@jwt_auth.require_auth()
def get_domain_template_api():
    """빈 도메인 템플릿을 반환한다."""
    return jsonify(DomainConfig.export_template())


# --- Chart Data API ---
from src.chart_data import ChartDataGenerator  # noqa: E402
chart_data_gen = ChartDataGenerator(
    logger_db=chat_logger,
    feedback_db=feedback_manager,
    sentiment_analyzer=globals().get("sentiment_analyzer"),
    user_segmenter=globals().get("user_segmenter"),
)


@app.route("/api/admin/charts/categories", methods=["GET"])
@jwt_auth.require_auth()
def chart_categories():
    return jsonify(chart_data_gen.category_distribution())


@app.route("/api/admin/charts/trends", methods=["GET"])
@jwt_auth.require_auth()
def chart_trends():
    request.args.get("metric", "queries")
    days = int(request.args.get("days", 30))
    return jsonify(chart_data_gen.daily_query_trend(days=days))


@app.route("/api/admin/charts/heatmap", methods=["GET"])
@jwt_auth.require_auth()
def chart_heatmap():
    days = int(request.args.get("days", 7))
    return jsonify(chart_data_gen.hourly_heatmap(days=days))


@app.route("/api/admin/charts/dashboard", methods=["GET"])
@jwt_auth.require_auth()
def chart_dashboard():
    charts = {
        "categories": chart_data_gen.category_distribution(),
        "trends": chart_data_gen.daily_query_trend(days=30),
        "heatmap": chart_data_gen.hourly_heatmap(days=7),
        "top_queries": chart_data_gen.top_queries(limit=10),
    }
    return jsonify({"charts": charts})


# ── Quality Scoring Routes ───────────────────────────────────────────────


@app.route("/api/admin/quality/scores", methods=["GET"])
@jwt_auth.require_auth()
def quality_scores_overview():
    """Return comprehensive quality report."""
    try:
        days = int(request.args.get("days", 30))
        report = quality_report.generate(days=days)
        return jsonify(report)
    except Exception as e:
        logger.error(f"Quality scores overview failed: {e}")
        return jsonify({"error": "Failed to generate quality scores."}), 500


@app.route("/api/admin/quality/low", methods=["GET"])
@jwt_auth.require_auth()
def quality_low_responses():
    """Return responses below quality threshold."""
    try:
        threshold = int(request.args.get("threshold", 60))
        low = quality_scorer.get_low_quality_responses(threshold=threshold)
        return jsonify({"threshold": threshold, "count": len(low), "responses": low})
    except Exception as e:
        logger.error(f"Low quality query failed: {e}")
        return jsonify({"error": "Failed to retrieve low quality responses."}), 500


@app.route("/api/admin/quality/trend", methods=["GET"])
@jwt_auth.require_auth()
def quality_trend():
    """Return quality score trend over time."""
    try:
        days = int(request.args.get("days", 30))
        trend = quality_scorer.get_quality_trend(days=days)
        return jsonify({"days": days, "trend": trend})
    except Exception as e:
        logger.error(f"Quality trend query failed: {e}")
        return jsonify({"error": "Failed to retrieve quality trend."}), 500


@app.route("/api/admin/quality/score", methods=["POST"])
@jwt_auth.require_auth()
def quality_score_single():
    """Score a specific Q&A pair."""
    try:
        data = request.get_json(force=True)
        query = data.get("query", "")
        answer = data.get("answer", "")
        category = data.get("category", "")

        if not query or not answer:
            return jsonify({"error": "Both 'query' and 'answer' are required."}), 400

        result = quality_scorer.score_response(query, answer, category)
        suggestions = quality_scorer.suggest_improvements(
            query, answer, result["breakdown"]
        )
        result["suggestions"] = suggestions
        return jsonify(result)
    except Exception as e:
        logger.error(f"Quality scoring failed: {e}")
        return jsonify({"error": "Failed to score response."}), 500


# ── API v2 Routes ─────────────────────────────────────────────────────────


@app.route("/api/versions", methods=["GET"])
def api_versions():
    """List available API versions with their status."""
    versions = api_gateway.get_active_versions()
    return jsonify({"versions": versions})


@app.route("/api/v2/faq", methods=["GET"])
def v2_faq_list():
    """Paginated and sortable FAQ list (v2)."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    sort_by = request.args.get("sort", "id")
    order = request.args.get("order", "asc")

    items = []
    for item in chatbot.faq_items:
        items.append({
            "id": item.get("id", ""),
            "category": item.get("category", ""),
            "question": item.get("question", ""),
        })

    sorted_items = sort_helper.sort_items(items, sort_by, order)
    result = pagination_helper.paginate(sorted_items, page=page, per_page=per_page)

    resp = jsonify(result)
    resp.headers["X-API-Version"] = "v2"
    return api_gateway.add_deprecation_headers(resp, "v2")


@app.route("/api/v2/chat", methods=["POST"])
def v2_chat():
    """Chat endpoint (v2) - same as v1 but response includes API version."""
    client_ip = request.remote_addr or "unknown"
    if not app.config.get("TESTING") and not os.environ.get("TESTING"):
        if not rate_limiter.is_allowed(client_ip):
            return jsonify({"error": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."}), 429

    tenant_id = request.headers.get("X-Tenant-Id", "default")
    tenant = tenant_manager.get_tenant(tenant_id)
    if tenant is None:
        return jsonify({"error": f"테넌트 '{tenant_id}'를 찾을 수 없습니다."}), 404
    if not tenant.get("active", True):
        return jsonify({"error": f"테넌트 '{tenant_id}'가 비활성 상태입니다."}), 403

    data = request.get_json(silent=True)
    if not data or "query" not in data:
        return jsonify({"error": "query 필드가 필요합니다."}), 400

    raw_query = data["query"]
    if not isinstance(raw_query, str):
        return jsonify({"error": "query는 문자열이어야 합니다."}), 400

    query = sanitize_input(raw_query, max_length=MAX_QUERY_LENGTH)
    if not query:
        return jsonify({"error": "질문을 입력해 주세요."}), 400

    if len(query) > MAX_QUERY_LENGTH:
        return jsonify({"error": f"질문은 {MAX_QUERY_LENGTH}자 이내로 입력해 주세요."}), 400

    categories = classify_query(query)
    escalation = check_escalation(query)
    session_id = data.get("session_id")
    answer = chatbot.process_query(query, session_id=session_id)

    primary_category = categories[0] if categories else "GENERAL"
    is_escalation = escalation is not None

    response_data = {
        "answer": answer,
        "category": primary_category,
        "categories": categories,
        "is_escalation": is_escalation,
        "escalation_target": escalation.get("target") if escalation else None,
        "tenant_id": tenant_id,
        "api_version": "v2",
    }
    if session_id:
        response_data["session_id"] = session_id

    resp = jsonify(response_data)
    resp.headers["X-API-Version"] = "v2"
    return api_gateway.add_deprecation_headers(resp, "v2")


# ── Conversation Analytics Routes ─────────────────────────────────────────


@app.route("/api/admin/analytics/patterns", methods=["GET"])
@jwt_auth.require_auth()
def admin_analytics_patterns():
    """탐지된 대화 패턴을 반환한다."""
    try:
        days = request.args.get("days", 30, type=int)
        patterns = conversation_analytics.detect_patterns(days=days)
        sequences = conversation_analytics.pattern_detector.find_common_sequences()
        pairs = conversation_analytics.pattern_detector.find_question_pairs()
        seasonality = conversation_analytics.pattern_detector.detect_seasonality()
        return jsonify({
            "patterns": patterns,
            "sequences": sequences,
            "pairs": pairs,
            "seasonality": seasonality,
        })
    except Exception as e:
        logger.error(f"패턴 분석 실패: {e}")
        return jsonify({"error": "패턴 분석 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/analytics/insights", methods=["GET"])
@jwt_auth.require_auth()
def admin_analytics_insights():
    """자동 생성된 인사이트를 반환한다."""
    try:
        days = request.args.get("days", 30, type=int)
        insights = conversation_analytics.generate_insights(days=days)
        return jsonify(insights)
    except Exception as e:
        logger.error(f"인사이트 생성 실패: {e}")
        return jsonify({"error": "인사이트 생성 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/analytics/metrics", methods=["GET"])
@jwt_auth.require_auth()
def admin_analytics_metrics():
    """모든 대화 분석 지표를 반환한다."""
    try:
        metrics = conversation_analytics.get_all_metrics()
        return jsonify(metrics)
    except Exception as e:
        logger.error(f"분석 지표 조회 실패: {e}")
        return jsonify({"error": "분석 지표 조회 중 오류가 발생했습니다."}), 500


# --- 에러 복구 API 엔드포인트 ---


@app.route("/api/admin/errors", methods=["GET"])
@jwt_auth.require_auth()
def admin_errors():
    """최근 에러 목록을 반환한다."""
    try:
        limit = request.args.get("limit", 50, type=int)
        errors = error_recovery.error_logger.get_recent_errors(limit=limit)
        return jsonify({"errors": errors, "count": len(errors)})
    except Exception as e:
        logger.error(f"에러 로그 조회 실패: {e}")
        return jsonify({"error": "에러 로그 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/errors/stats", methods=["GET"])
@jwt_auth.require_auth()
def admin_error_stats():
    """에러 통계를 반환한다."""
    try:
        stats = error_recovery.get_error_stats()
        rate = error_recovery.error_logger.get_error_rate(minutes=60)
        stats["error_rate"] = rate
        return jsonify(stats)
    except Exception as e:
        logger.error(f"에러 통계 조회 실패: {e}")
        return jsonify({"error": "에러 통계 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/circuits", methods=["GET"])
@jwt_auth.require_auth()
def admin_circuits():
    """서킷 브레이커 상태를 반환한다."""
    try:
        status = error_recovery.get_circuit_status()
        return jsonify({"circuits": status})
    except Exception as e:
        logger.error(f"서킷 브레이커 상태 조회 실패: {e}")
        return jsonify({"error": "서킷 브레이커 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/suggestions", methods=["GET"])
def api_suggestions():
    """세션 기반 맥락 제안을 반환한다."""
    session_id = request.args.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id 파라미터가 필요합니다."}), 400

    try:
        session_history: list = []
        ctx = chatbot.session_manager.get_context(session_id) if hasattr(chatbot.session_manager, "get_context") else {}
        if isinstance(ctx, dict):
            session_history = ctx.get("queries", [])

        if not session_history:
            suggestions = smart_suggestion_engine.get_onboarding_suggestions()
            return jsonify({"session_id": session_id, "suggestions": suggestions, "type": "onboarding"})

        last_query = session_history[-1]
        category = classify_query(last_query)[0] if last_query else "GENERAL"
        suggestions = smart_suggestion_engine.get_follow_up_suggestions(
            last_query, "", category, session_history=session_history,
        )
        tips = smart_suggestion_engine.get_contextual_tips(category)
        return jsonify({
            "session_id": session_id,
            "suggestions": suggestions,
            "tips": tips,
            "type": "contextual",
        })
    except Exception as e:
        logger.error(f"제안 조회 실패: {e}")
        return jsonify({"error": "제안 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/onboarding", methods=["GET"])
def api_onboarding():
    """새 사용자를 위한 온보딩 제안을 반환한다."""
    try:
        suggestions = smart_suggestion_engine.get_onboarding_suggestions()
        tips = smart_suggestion_engine.get_contextual_tips("GENERAL")
        return jsonify({"suggestions": suggestions, "tips": tips})
    except Exception as e:
        logger.error(f"온보딩 제안 조회 실패: {e}")
        return jsonify({"error": "온보딩 제안 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/entities/dictionary", methods=["GET"])
def api_admin_entity_dictionary():
    """엔티티 사전을 반환한다."""
    try:
        dictionary = entity_extractor_v2.get_entity_dictionary()
        return jsonify({"entity_dictionary": dictionary})
    except Exception as e:
        logger.error(f"엔티티 사전 조회 실패: {e}")
        return jsonify({"error": "엔티티 사전 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/policy/evaluate", methods=["POST"])
@jwt_auth.require_auth()
def api_admin_policy_evaluate():
    """PolicyEngineV2로 질문을 평가한다 (관리자 전용)."""
    data = request.get_json(silent=True) or {}
    query = data.get("query", "")
    if not isinstance(query, str) or not query.strip():
        return jsonify({"error": "query 필드가 필요합니다."}), 400

    intent_id = data.get("intent_id")
    entities = data.get("entities") or []
    category = data.get("category")

    try:
        engine = get_policy_engine_v2()
        decision = engine.evaluate(
            query=query,
            intent_id=intent_id,
            entities=entities,
            category=category,
        )
        return jsonify(decision)
    except Exception as e:
        logger.error(f"PolicyEngineV2 평가 실패: {e}")
        return jsonify({"error": "정책 평가 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/policy/rules", methods=["GET"])
@jwt_auth.require_auth()
def api_admin_policy_rules():
    """현재 PolicyEngineV2 규칙을 반환한다 (관리자 전용)."""
    try:
        engine = get_policy_engine_v2()
        return jsonify(engine.get_rules())
    except Exception as e:
        logger.error(f"PolicyEngineV2 규칙 조회 실패: {e}")
        return jsonify({"error": "정책 규칙 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/benchmark/run", methods=["POST"])
@jwt_auth.require_auth()
def api_admin_benchmark_run():
    """답변 정확도 벤치마크를 실행한다.

    Request JSON (선택):
        {
            "testset_path": "data/golden_testset.json",  # 기본값
            "persist": true                                  # 기본값: true
        }

    Response:
        {
            "metrics": {...},
            "comparison": {...}
        }
    """
    try:
        data = request.get_json(silent=True) or {}
        testset_path = data.get("testset_path") or os.path.join(
            BASE_DIR, "data", "golden_testset.json"
        )
        persist = bool(data.get("persist", True))

        previous = accuracy_benchmark.get_latest()
        previous_metrics = previous.get("metrics") if previous else None

        metrics = accuracy_benchmark.run_benchmark(testset_path, persist=persist)
        comparison = accuracy_benchmark.compare_results(metrics, previous_metrics)
        return jsonify({"metrics": metrics, "comparison": comparison})
    except FileNotFoundError:
        return jsonify({"error": "지정된 테스트셋 파일을 찾을 수 없습니다."}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"정확도 벤치마크 실행 실패: {e}", exc_info=True)
        return jsonify({"error": "벤치마크 실행 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/benchmark/history", methods=["GET"])
@jwt_auth.require_auth()
def api_admin_benchmark_history():
    """과거 벤치마크 실행 이력을 반환한다."""
    try:
        limit = request.args.get("limit", 20, type=int)
        history = accuracy_benchmark.get_history(limit=limit)
        return jsonify({"history": history, "count": len(history)})
    except Exception as e:
        logger.error(f"벤치마크 이력 조회 실패: {e}", exc_info=True)
        return jsonify({"error": "벤치마크 이력 조회 중 오류가 발생했습니다."}), 500


def main():
    parser = argparse.ArgumentParser(description="보세전시장 챗봇 웹 서버")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()

    logger.info(f"Starting web server on {args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)

if __name__ == "__main__":
    main()
