# API Reference

## 기본 정보

- Base URL: `http://서버주소:8080`
- Content-Type: `application/json`
- 인증: `X-API-Key` 헤더 (설정 시)

---

## 챗봇 API

### POST /api/chat

질문을 처리하여 답변을 반환합니다.

**Request**
```json
{
  "query": "보세전시장이 무엇인가요?",
  "session_id": "optional-session-id",
  "lang": "ko"
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| query | string | Yes | 질문 (최대 2000자) |
| session_id | string | No | 멀티턴 세션 ID |
| lang | string | No | 응답 언어 (ko/en/cn/jp, 기본: ko) |

**Response (200)**
```json
{
  "answer": "문의하신 내용은 [제도 일반]에 관한...",
  "category": "GENERAL",
  "categories": ["GENERAL"],
  "is_escalation": false,
  "escalation_target": null,
  "lang": "ko",
  "session_id": "abc123"
}
```

**Error (400/429)**
```json
{"error": "query 필드가 필요합니다."}
{"error": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."}
```

---

### POST /api/session/new

새 멀티턴 세션을 생성합니다.

**Response (201)**
```json
{"session_id": "uuid", "created_at": "ISO timestamp"}
```

### GET /api/session/{session_id}

세션 상태를 조회합니다.

**Response (200)**
```json
{
  "session_id": "uuid",
  "history": [...],
  "pending_confirmations": [...],
  "context": {...}
}
```

---

### GET /api/faq

FAQ 50개 목록을 반환합니다.

**Response (200)**
```json
{"items": [{"id": "A", "category": "GENERAL", "question": "..."}], "count": 50}
```

### GET /api/config

챗봇 설정 정보를 반환합니다.

**Response (200)**
```json
{"persona": "...", "categories": [...], "contacts": {...}}
```

### GET /api/health

서버 상태를 확인합니다.

**Response (200)**
```json
{"status": "ok", "faq_count": 50}
```

---

## 피드백 API

### POST /api/feedback

사용자 피드백을 저장합니다.

**Request**
```json
{"query_id": "msg_1", "rating": "helpful", "comment": "좋은 답변이었습니다"}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| query_id | string | Yes | 질문 식별자 |
| rating | string | Yes | "helpful" 또는 "unhelpful" |
| comment | string | No | 추가 코멘트 |

---

## 관리자 API

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| /admin | GET | 관리자 대시보드 HTML |
| /api/admin/stats | GET | 질문 통계 |
| /api/admin/logs | GET | 최근 로그 (?limit=50) |
| /api/admin/unmatched | GET | 미매칭 질문 (?limit=20) |
| /api/admin/feedback | GET | 피드백 통계 |
| /api/admin/recommendations | GET | FAQ 추가 추천 (?top_k=10) |
| /api/admin/analytics | GET | 트렌드 분석 |
| /api/admin/report | GET | 주간 리포트 |
| /api/admin/faq-pipeline | GET | FAQ 파이프라인 후보 |
| /api/admin/faq-pipeline/approve | POST | 후보 승인 {"candidate_id": "..."} |
| /api/admin/faq-pipeline/reject | POST | 후보 거부 {"candidate_id": "..."} |

---

## HTTP 상태 코드

| 코드 | 설명 |
|------|------|
| 200 | 성공 |
| 201 | 생성 완료 (세션, 피드백) |
| 400 | 잘못된 요청 |
| 404 | 리소스 없음 |
| 429 | Rate Limit 초과 |
| 500 | 내부 서버 오류 |
