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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, Response, request, jsonify, send_from_directory
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
from src.tenant_manager import TenantManager
from src.webhook_manager import WebhookManager
from src.audit_logger import AuditLogger
from src.alert_center import AlertCenter, AlertRuleEngine
from src.profiler import Profiler, RequestProfiler, ComponentBenchmark
from src.health_monitor import HealthMonitor
from src.i18n import I18nManager
from src.db_migration import MigrationManager
from src.ab_testing import ABTestManager
from src.user_recommender import UserRecommender
from src.utils import load_json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_QUERY_LENGTH = 2000

# logs 디렉토리 자동 생성
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "web"), static_url_path="/static")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("chatbot")

chatbot = BondedExhibitionChatbot()
chat_logger = ChatLogger(db_path=os.path.join(BASE_DIR, "logs", "chat_logs.db"))
feedback_manager = FeedbackManager(db_path=os.path.join(BASE_DIR, "logs", "feedback.db"))
translator = SimpleTranslator()
i18n_manager = I18nManager()
faq_recommender = FAQRecommender(chat_logger)
query_analytics = QueryAnalytics(chat_logger, feedback_manager)
report_generator = ReportGenerator(chat_logger, feedback_manager)
auto_faq_pipeline = AutoFAQPipeline(
    faq_recommender, faq_path=os.path.join(BASE_DIR, "data", "faq.json")
)

# 보안 미들웨어 초기화
api_key_auth = APIKeyAuth(app)
rate_limit_value = int(os.environ.get("CHATBOT_RATE_LIMIT", "60"))
rate_limiter = RateLimiter(max_requests=rate_limit_value)
advanced_rate_limiter = AdvancedRateLimiter()

# Phase 13-18 모듈 초기화
realtime_monitor = RealtimeMonitor()
conversation_exporter = ConversationExporter()
conversation_summarizer = ConversationSummarizer(chatbot.session_manager)
legal_refs = load_json("data/legal_references.json")
faq_quality_checker = FAQQualityChecker(chatbot.faq_items, legal_refs)
satisfaction_tracker = SatisfactionTracker()

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

# 사용자 추천 시스템 초기화
user_recommender = UserRecommender(
    db_path=os.path.join(BASE_DIR, "data", "user_profiles.db")
)

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


# --- Advanced rate limiting middleware ---
_RATE_LIMIT_EXEMPT_PATHS = {"/", "/api/health", "/static", "/manifest.json", "/sw.js"}


@app.before_request
def _check_advanced_rate_limit():
    """Apply per-endpoint rate limits via AdvancedRateLimiter."""
    if app.config.get("TESTING"):
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
        except Exception:
            pass
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
    """사용자 질문을 처리하여 답변을 반환한다."""
    # Rate Limiting 적용
    client_ip = request.remote_addr or "unknown"
    if not rate_limiter.is_allowed(client_ip):
        return jsonify({"error": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."}), 429

    # 멀티 테넌트 지원: X-Tenant-Id 헤더 (선택, 기본값 "default")
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

    # 입력 살균 적용
    query = sanitize_input(raw_query, max_length=MAX_QUERY_LENGTH)
    if not query:
        return jsonify({"error": "질문을 입력해 주세요."}), 400

    if len(query) > MAX_QUERY_LENGTH:
        return jsonify({"error": f"질문은 {MAX_QUERY_LENGTH}자 이내로 입력해 주세요."}), 400

    categories = classify_query(query)
    escalation = check_escalation(query)
    # 세션 ID 처리 (선택적)
    session_id = data.get("session_id")
    answer = chatbot.process_query(query, session_id=session_id)

    logger.info(f"질문: {query[:50]}... | 분류: {categories[0]} | 에스컬레이션: {escalation is not None}")

    primary_category = categories[0] if categories else "GENERAL"
    is_escalation = escalation is not None

    # FAQ 매칭 결과에서 faq_id 추출
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
        except Exception:
            pass

    response = {
        "answer": translated_answer,
        "category": primary_category,
        "categories": categories,
        "is_escalation": is_escalation,
        "escalation_target": escalation.get("target") if escalation else None,
        "lang": lang,
        "related_questions": [{"id": r["id"], "question": r["question"]} for r in related],
        "tenant_id": tenant_id,
    }

    if session_id:
        response["session_id"] = session_id
        # 개인화 추천 추가
        try:
            recommended = user_recommender.get_recommendations(session_id, top_n=3)
            response["recommended"] = recommended
        except Exception:
            response["recommended"] = []

    return jsonify(response)


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
    """헬스 체크 엔드포인트."""
    return jsonify({"status": "ok", "faq_count": len(chatbot.faq_items)})


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


def main():
    parser = argparse.ArgumentParser(description="보세전시장 챗봇 웹 서버")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()

    logger.info(f"보세전시장 챗봇 웹 서버 시작: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
