# Changelog

모든 주요 변경 사항을 기록합니다.

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
