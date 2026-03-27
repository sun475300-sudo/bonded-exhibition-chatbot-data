# Changelog

모든 주요 변경 사항을 기록합니다.

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
