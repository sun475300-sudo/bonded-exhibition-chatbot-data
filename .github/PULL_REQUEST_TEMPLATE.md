# PR 요약

## 변경 내용
<!-- 한 줄 요약 + 핵심 변경 bullet -->

-

## 종류
<!-- 해당하는 항목에 [x] -->

- [ ] feat — 새 기능
- [ ] fix — 버그 수정
- [ ] refactor — 코드 정리 (동작 변화 없음)
- [ ] perf — 성능 개선
- [ ] test — 테스트 추가/수정
- [ ] docs — 문서
- [ ] chore — 빌드/도구/설정
- [ ] ci — CI/CD 워크플로

## 영향 범위
<!-- 어느 모듈/기능에 영향? 회귀 위험? -->

-

## 검증
<!-- 어떻게 테스트했는지 -->

- [ ] 로컬 `pytest tests/` 통과 (또는 변경된 영역 핀포인트)
- [ ] `ruff check src/ web_server.py --select E,F,W --ignore E501` 통과
- [ ] CI green (또는 사유 명시)

## 체크리스트

- [ ] 새 기능에 테스트 추가
- [ ] secrets/credentials 미포함
- [ ] 운영 영향 있으면 RUNBOOK 업데이트
- [ ] 환경변수 추가 시 `.env.example` 업데이트

## 관련

<!-- 이슈/PR 링크 -->

Closes #
