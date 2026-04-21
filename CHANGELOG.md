# Changelog

모든 주요 변경 사항을 기록합니다.

형식: [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)

## [14.2.0] - 2026-04-21

### Added
- `LawUpdateScheduler`를 `LawSyncManager`와 연결하여 예약된 확인 주기마다
  국가법령정보센터 API 원문 → legal_references → FAQ 전파까지 풀 사이클로 실행.
  - 새 파라미터: `sync_manager`, `on_propagate` 콜백
  - 새 메서드: `run_full_sync` (sync_manager 주입 시 사용)
  - 환경변수 `LAW_SYNC_AUTOSTART=1`로 서버 기동 시 자동 시작,
    `LAW_SYNC_INTERVAL_HOURS`로 주기 조정 (기본 24시간)
  - 전파가 발생하면 챗봇 인스턴스의 `reload_faq`를 자동 호출하여
    재시작 없이 최신 법령 상태를 반영
- `tests/test_law_updater.py`: `TestLawUpdateSchedulerFullSync` 4건 추가
  (콜백 호출 / 미호출 / sync_manager 누락 / 콜백 예외 스왈로 검증)

## [14.1.0] - 2026-04-21

### Added
- 국가법령정보센터 API 변경 감지 시 FAQ 답변까지 자동 전파되도록 확장
  - `LawSyncManager.propagate_to_faq`: 변경된 법령을 인용한 FAQ 항목에 `law_update_pending`·`last_law_sync` 플래그 기록
  - `LawSyncManager.sync_and_propagate`: 법령 확인 → legal_references.json 갱신 → FAQ 전파까지 한 번에 수행
  - `LawSyncManager.clear_faq_pending_flags`: 관리자가 수동 검토한 FAQ의 재검토 플래그 해제
  - `LawSyncManager.get_recent_changes`: 변경 이력 중 실제 변경분만 필터링
- 새 엔드포인트: `/api/admin/law-sync/propagate`, `/api/admin/law-sync/faq-pending`, `/api/admin/law-sync/faq-pending/clear`
- `BondedExhibitionChatbot.reload_faq`: 법령 전파 후 챗봇 재시작 없이 FAQ·법령 근거 핫 리로드
- `build_response`에 `law_update_pending`·`last_law_sync` 지원: 최근 법령 변경이 감지된 FAQ 답변에는 국가법령정보센터 재확인 안내 문구 추가
- 모니터링 대상에 관세법 제119조(심사와 심판) 추가

### Fixed
- FAQ 항목 **AQ(관세 불복 절차)**에서 잘못된 법전 편(編) 인용 수정
  - 기존: "관세법 제7편(불복절차)" → 수정: "관세법 제5장(납세자의 권리 및 불복절차) 제2절(심사와 심판, 제119조부터 제132조)"
  - `legal_basis`에 `관세법 제119조` 명시
  - 심사청구(관세청장)·심판청구(조세심판원장)·이의신청(처분 세관장) 주체 구분을 문구에 반영

## [14.0.0] - 2026-04-02

### Added
- Phase 59: 스트레스 테스트 (test_stress)
  - 전체 API 엔드포인트 커버리지 테스트 (30+ 엔드포인트)
  - 엣지 케이스 검증: 빈 쿼리, 초장문 쿼리, 유니코드/이모지, CJK 혼합, SQL 인젝션, XSS 방어
  - 데이터 무결성 검증: FAQ 50개 항목, 10개 카테고리, 필수 필드, ID 중복, 에스컬레이션 규칙, 법령 참조
  - 동시성 테스트: 연속 고속 요청, 다중 세션 고유성, 자동완성 연속 호출
  - 크로스 모듈 통합 테스트: 감정 분석 연동, 세션 기반 대화, 다국어 채팅
- Phase 60: 범용 도메인 설정 시스템 (domain_config)
  - DomainConfig: JSON 기반 도메인 설정 로드/저장, 점(.) 표기법 get/set
  - 스키마 검증: 8개 섹션(domain, categories, persona, response_format, escalation, legal_references, features, limits)
  - DomainInitializer: 도메인 생성/목록/전환 관리, 빈 템플릿 내보내기
  - 챗봇을 보세전시장 외 다른 도메인에서도 재사용 가능하도록 범용화
- Phase 61: 대시보드 차트 데이터 API (chart_data)
  - Chart.js 호환 시각화 데이터 생성기 (ChartDataGenerator)
  - 9종 차트: 카테고리 분포(파이), 일별 질문 추이(라인), 시간대 히트맵, 응답 시간 히스토그램, 만족도 추이, 상위 질문 TOP-N(바), 에스컬레이션 추이, FAQ 매칭률 추이, 사용자 세그먼트/감정 분포

### Changed
- 테스트 커버리지를 스트레스/보안/무결성 영역으로 대폭 확장
- 대시보드 시각화를 Chart.js 표준 데이터 형식으로 통합

## [13.0.0] - 2026-04-01

### Added
- Phase 55: 지식 그래프 (knowledge_graph)
  - KnowledgeGraph: FAQ/개념/법령/카테고리 노드 및 관계 그래프
  - 4종 노드 타입(faq, concept, law, category), 4종 관계(related_to, requires, part_of, cites)
  - BFS 기반 이웃 탐색(depth 지정), 최단 경로 탐색, 서브그래프 추출
  - FAQ 데이터 자동 그래프 빌드: 공유 키워드/법적근거/카테고리 기반 엣지 자동 생성
  - 노드 검색, 관련 개념 조회, 그래프 통계(밀도/노드/엣지 수), JSON 내보내기
- Phase 56: 사용자 세분화 (user_segment)
  - TermComplexityScorer: 법률 용어(60+), 조문 참조 패턴(8종), 기술 전문 용어(40+) 기반 복잡도 0-1 점수
  - UserSegmenter: beginner/intermediate/expert 3단계 세그먼트 자동 분류
  - SQLite 기반 세그먼트 이력 추적 (user_segments, segment_history 테이블)
  - 세그먼트별 답변 깊이 조절: beginner(쉬운 설명 추가), expert(법률 인용 강조)
  - 전체 세그먼트 분포 통계
- Phase 57: 장기 대화 컨텍스트 메모리 (context_memory)
  - ContextMemory: SQLite 기반 세션 간 컨텍스트 저장 (TTL 7일 기본)
  - 세션 링크를 통한 이전 세션 컨텍스트 이관 (merge_context)
  - 사용자 프로필 자동 구성 (토픽 빈도, 선호도 축적)
  - ConversationMemoryManager: 고수준 토픽 기억, 대화 재개 메시지, 재방문 사용자 감지
  - 만료 컨텍스트 자동 정리 (cleanup_expired)
- Phase 58: 동적 응답 템플릿 엔진 (template_engine)
  - TemplateEngine: 변수 치환({{var}}), 조건문({%if%}), 반복문({%for%}), 기본값(|default) 지원
  - 5종 빌트인 템플릿: standard_answer, escalation, unknown_query, welcome, error
  - 커스텀 템플릿 등록/삭제/목록 관리 (CRUD)
  - ResponseFormatter: 도메인별 설정 병합을 통한 범용 답변 포맷팅
- 추가 분석 모듈:
  - 감정 분석기 (sentiment_analyzer): 긍정/부정/중립 감정 판별, 부정어 반전 처리, 강조어 배율, SQLite 이력 저장, 답변 톤 자동 조절, 에스컬레이션 트리거
  - 질문 클러스터링 (question_cluster): TF-IDF 기반 코사인 유사도, agglomerative 클러스터링, FAQ 중복 감지 및 병합 제안, DuplicateDetector
  - 대화 흐름 분석 (flow_analyzer): 세션 대화 경로 추적, 전환 매트릭스, 이탈 지점 감지, Sankey 다이어그램 데이터, 경로별 만족도 분석
  - 주기적 작업 스케줄러 (task_scheduler): cron 표현식 파서(CronParser), 작업 등록/실행/이력 관리, 5종 기본 작업(백업/리포트/법령체크/로그정리/FAQ품질)

### Changed
- 챗봇 응답 파이프라인에 감정 분석 및 사용자 세분화 통합
- 대화 컨텍스트를 세션 단위에서 장기 메모리로 확장
- 답변 생성을 하드코딩에서 동적 템플릿 엔진 방식으로 전환

## [11.0.0] - 2026-03-28

### Added
- Phase 47: 최종 통합 테스트 및 릴리스 준비
  - 전체 모듈 통합 테스트 (50개 모듈 연동 검증)
  - 릴리스 체크리스트 자동화
- Phase 48: 운영 안정성 최종 점검
  - 통합 헬스 모니터링 (health_monitor): DB/FAQ/디스크/메모리/응답시간/에러율 점검, healthy/degraded/unhealthy 상태
  - 대화 요약 엔진 (conversation_summary): 세션 요약, 핵심 포인트 추출, 카테고리 감지, 배치 요약
- Phase 49: 문서화 및 품질 보증
  - 전체 API 문서 최종 갱신
  - CHANGELOG v11.0.0 최종 릴리스 노트
- Phase 50: v11.0.0 최종 릴리스
  - 50단계 전체 Phase 완료
  - 프로덕션 배포 최종 검증

### Changed
- 전체 모듈 안정성 최종 검증 완료
- 테스트 커버리지 최종 확인 (50개 테스트 파일)

## [10.0.0] - 2026-03-28

### Added
- Phase 43: DB 마이그레이션 시스템 (db_migration)
  - 버전별 스키마 마이그레이션 (migrations/ 디렉토리)
  - 롤백 지원 (migrate/rollback), SQLite 메타데이터 관리
  - 마이그레이션 파일: 001_initial_schema, 002_add_indexes
- Phase 44: 고급 Rate Limiter (rate_limiter_v2)
  - 엔드포인트별 슬라이딩 윈도우 Rate Limit
  - 사용자별 일일 쿼터 관리, 사용량 통계 및 Top-user 추적
  - 엔드포인트별 기본 제한: /api/chat 30회, /api/faq 60회, /api/admin/* 20회
- Phase 45: 국제화 확장 (i18n)
  - 6개 언어 지원 (ko, en, cn, jp, vi, th)
  - JSON 기반 번역 파일 (data/locales/), 키 기반 번역 조회
  - 유니코드 범위 기반 언어 자동 감지
- Phase 46: 성능 프로파일러 (profiler)
  - cProfile 기반 함수 레벨 프로파일링
  - 요청 프로파일링 미들웨어, 컴포넌트 벤치마크

### Changed
- Rate Limiter를 v2로 업그레이드 (슬라이딩 윈도우 알고리즘)
- i18n 지원 언어를 4개(ko/en/cn/jp)에서 6개(+vi/th)로 확장

## [9.0.0] - 2026-03-28

### Added
- Phase 39: 알림 센터 (alert_center)
  - SQLite 기반 영구 알림 저장소
  - 자동 규칙 엔진 (미매칭률 급증, 만족도 하락, 법령 변경, 시스템 오류, FAQ 품질)
  - 심각도 3단계 (info, warning, critical), 카테고리 6종
- Phase 40: 감사 로그 (audit_logger)
  - 관리자 작업 전수 기록 (CRUD, 로그인/로그아웃, 백업/복원)
  - SQLite 기반, 리소스 타입별 조회 (faq, tenant, webhook, backup, session, config)
  - 컴플라이언스 및 보안 모니터링용
- Phase 41: 자동 리포트 생성기 (report_generator)
  - 일별/주별/월별 분석 리포트 자동 생성
  - HTML/JSON 내보내기, 대화 로그 + 피드백 데이터 통합 분석
- Phase 42: Slack 알림 연동 (slack_notifier)
  - Incoming Webhook 기반 알림 전송
  - 심각도별 색상/이모지, 재시도 로직 (최대 3회, 지수 백오프)
  - Dry-run 모드 (webhook 미설정 시 로그만 기록)

### Changed
- 관리자 대시보드에 알림 센터 통합
- 운영 모니터링 파이프라인 고도화 (알림 → Slack 연동)

## [8.0.0] - 2026-03-28

### Added
- Phase 35: FAQ CRUD 관리 (faq_manager)
  - FAQ 항목 생성/조회/수정/삭제, 원자적 파일 쓰기
  - SQLite 기반 변경 이력 추적 (faq_history.db)
  - 10개 카테고리 검증, 필수 필드 검증
- Phase 36: 백업 자동화 (backup_manager)
  - 전체/증분 백업, ZIP 압축, HMAC-SHA256 암호화
  - 무결성 검증, 스케줄링, 자동 복원
  - 대상: faq.json, legal_references.json, escalation_rules.json, *.db
- Phase 37: 멀티 테넌트 (tenant_manager)
  - 보세전시장별 독립 FAQ 및 설정 관리
  - SQLite 기반 테넌트 메타데이터, 스레드별 DB 연결
  - 기본 테넌트 자동 생성
- Phase 38: 이벤트 Webhook (webhook_manager)
  - 외부 연동용 Webhook 구독/배달 시스템
  - HMAC-SHA256 서명, SQLite 배달 로그
  - 지원 이벤트: query.received/matched/unmatched, escalation.triggered, feedback.received, faq.updated

### Changed
- 데이터 관리를 파일 기반에서 FAQManager CRUD 방식으로 전환
- 백업/복원 프로세스 자동화

## [7.0.0] - 2026-03-28

### Added
- Phase 31: 법령 업데이트 감지 (law_updater)
  - 보세전시장 관련 법령 변경 감지 (데이터 파일 해시 비교)
  - SQLite 기반 법령 버전 이력 관리 (law_versions.db)
  - CLI 지원: --check 옵션으로 현재 상태 확인
- Phase 32: JWT 관리자 인증 (auth)
  - 순수 Python JWT 구현 (HS256, hmac+hashlib)
  - SHA256+salt 비밀번호 해싱, 토큰 발급/검증
  - 외부 JWT 라이브러리 불필요
- Phase 33: 데이터 정합성 검증 (data_validator)
  - FAQ ↔ 법령 근거 참조 일치 검증
  - 카테고리 커버리지, 에스컬레이션 규칙 정합성 자동 검사
- Phase 34: LLM 하이브리드 폴백 (llm_fallback)
  - 키워드 매칭 실패 시 LLM API 호출 (Claude/OpenAI)
  - 환경변수 기반 활성화, 스텁 구현 포함

### Changed
- 보안 강화: API Key + JWT 이중 인증 체계
- 법령 변경 시 FAQ 자동 업데이트 파이프라인 연동

## [6.0.0] - 2026-03-28

### Added
- Phase 27: 카카오톡 어댑터 (kakao_adapter)
  - 카카오 i 오픈빌더 스킬서버 연동
  - 텍스트 제한 자동 처리 (SimpleText 1000자, Card 400자)
  - Flask Blueprint 기반 (/kakao/chat)
- Phase 28: 네이버 톡톡 어댑터 (naver_adapter)
  - 네이버 톡톡 웹훅 어댑터
  - 이벤트 타입 지원: send, open, leave, friend
  - 버튼(18자)/캐러셀(10개) 제한 자동 처리
- Phase 29: Prometheus 호환 메트릭 (metrics)
  - 순수 Python 메트릭 수집기 (외부 라이브러리 불필요)
  - Counter/Histogram/Gauge 지원, 스레드 안전
  - Prometheus text format 노출
- Phase 30: 환경설정 관리 (config_manager)
  - 환경변수 우선, config 파일 폴백 방식
  - 타입 자동 캐스팅, 지원 변수 정의

### Changed
- 챗봇을 카카오톡/네이버톡톡 메신저 플랫폼으로 확장
- 모니터링 체계를 Prometheus 표준으로 통합

## [5.0.0] - 2026-03-28

### Added
- Phase 19-22: UX 고도화
  - 자동완성 검색 최적화
  - 키보드 단축키 및 접근성 개선
  - 모바일 반응형 UI 최적화
  - 챗봇 응답 애니메이션 및 타이핑 인디케이터
- Phase 23-24: 성능 최적화
  - FAQ 캐싱 레이어 (LRU 캐시, TTL 기반 만료)
  - 응답 시간 최적화 (TF-IDF 인덱스 사전 로딩)
- Phase 25: Rate Limiter 기본 구현 (security 모듈 내)
  - IP 기반 요청 제한, 분당 임계값 설정
- Phase 26: 부하 테스트 및 성능 벤치마크 (tests/benchmark.py, tests/load_test.py)
  - 동시 접속 시뮬레이션, 응답 시간 측정

### Changed
- chatbot.py 전처리 파이프라인 성능 개선
- TF-IDF 매칭 알고리즘 최적화

## [4.0.0] - 2026-03-28

### Added
- Phase 13: 대화 품질 고도화
  - 동의어 사전 (synonym_resolver): 30개 동의어 매핑, 쿼리 확장
  - 오타 교정 (spell_corrector): 레벤슈타인 거리 + 자모 분해, 153개 도메인 용어
  - 모호 질문 되묻기 (clarification): 짧은/모호 질문 감지, 명확화 질문 생성
  - 답변 만족도 추적 (satisfaction_tracker): 세션 내 재질문 감지, 품질 점수
- Phase 14: 고급 검색 엔진
  - 한국어 토크나이저 (korean_tokenizer): 조사 제거, 도메인 용어 보존, n-gram
  - BM25 랭킹 (bm25_ranker): k1=1.5, b=0.75, TF-IDF 보완
  - 관련 질문 추천 (related_faq): Jaccard 유사도 기반, 카테고리 이웃
- Phase 15: 실시간 모니터링 (realtime_monitor)
  - 링 버퍼 이벤트 기록, 분/시간 통계, 임계값 알림 3종
- Phase 16: FAQ 품질 자동 검사 (faq_quality_checker)
  - 중복 감지, 키워드 커버리지, 법령 정합성, 답변 일관성, 카테고리 균형
- Phase 17: 대화 내보내기 (conversation_export)
  - 텍스트/JSON/CSV/HTML 4가지 형식 지원
- Phase 18: 플러그인 시스템 (plugin_system)
  - 6개 훅 포인트, 우선순위 파이프라인 패턴
- 신규 API: /api/admin/monitor, /api/admin/quality, /api/session/export, /api/related
- chatbot.py에 전처리 파이프라인 통합 (오타교정 → 동의어확장)

## [3.0.0] - 2026-03-27

### Added
- Phase 7: API 보안 (API Key 인증, Rate Limiter, 입력 살균)
- Phase 8: 분석 (트렌드 리포트, 품질 점수, FAQ 자동 파이프라인)
- Phase 9: 프로덕션 배포 (nginx, gunicorn, 백업/복원, 환경변수 관리)
- Phase 10: E2E 테스트, 회귀 테스트 16건, 부하 테스트
- Phase 11: API 문서, 운영 매뉴얼, 개발자 가이드
- Phase 12: UX 고도화 (대화 내보내기, 다크/라이트 토글)

## [2.0.0] - 2026-03-27

### Added
- Phase 4: SmartClassifier (대화 맥락 분류), FAQ 자동 추천
- Phase 5: 피드백 시스템, GitHub Actions CI/CD
- Phase 6: PWA, 음성 입력, 다국어 (KO/EN/CN/JP)
- FAQ 50개로 확장 (v3.0.0)
- TF-IDF 유사도 매칭 (순수 Python 구현)
- 멀티턴 대화 (세션 관리, 30분 만료)
- Docker 배포 패키지
- SQLite 로그 DB + 관리자 대시보드

## [1.0.0] - 2026-03-26

### Added
- 보세전시장 민원응대 챗봇 초기 구축
- FAQ 7개, 질문 분류기 (10개 카테고리)
- 답변 생성기 (템플릿 기반, 면책 문구 자동)
- 에스컬레이션 판단 (5개 규칙)
- 웹 챗봇 UI (Flask + 다크 테마)
- 터미널 시뮬레이터

### Fixed
- 에스컬레이션 우선순위 로직 버그
- 분류기 오타 (설영특허 → 설치특허)
- FAQ 매칭 동점 타이브레이크
- 키워드 0개 FAQ 반환 버그
- 범용 키워드 오매칭 16건
