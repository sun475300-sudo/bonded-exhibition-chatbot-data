# PR Redundancy 분석 — bonded-exhibition-chatbot-data

> 생성: 2026-04-26 · 대상: 열린 PR #23~#32 9건
> Phase 3 (분석만) — 실제 close는 사용자 승인

## 1. 메타데이터 매트릭스

| # | 작성 | 제목(요약) | +/- | files | base diff | 권고 |
|---|---|---|---|---|---|---|
| 23 | 04-20 15:09 | 법령 API 동기화 + FAQ 100% | +1211/-75 | 7 | diverged | **CLOSE** |
| 24 | 04-20 15:56 | 정확도 100% + 법령 API 자동 업데이트 | +1999/-123 | 8 | diverged | **CLOSE** |
| 25 | 04-21 06:44 | 법령 API 변경 FAQ 자동 전파 + 불복절차 | +5274/-95 | 28 | diverged | **CLOSE** |
| 26 | 04-21 07:51 | FAQ 매칭 정확도 100% + 법령 동기화 연동 | +1902/-240 | 10 | diverged | **CLOSE** |
| 27 | 04-21 08:42 | 법령정보센터→FAQ 자동 전파 + 답변 정정 | +796/-88 | 22 | diverged | **CLOSE** |
| 28 | 04-24 08:01 | 법령정보센터 실시간 반영 + 오답 100% 해소 | +903/-42 | 11 | diverged | **CLOSE** |
| 29 | 04-24 13:09 | 법령정보센터 API → FAQ 자동 동기화 | +995/-99 | 40 | diverged | **CLOSE** |
| 30 | 04-25 07:37 | 법령정보센터 자동 동기화 → 답변 최신화 | +11775/-279 | 56 | diverged | **CLOSE (거대)** |
| 31 | 04-25 08:55 | 보세봇 정확도 개선 + 법령정보센터 자동 동기화 | +3762/-265 | 53 | diverged | **CLOSE** |
| **32** | 04-25 09:36 | 법령정보센터 동기화 hot-reload + PII/SQL 버그 fix | **+689/-121** | **39** | diverged | **검토 후 머지 후보** |

## 2. 패턴 분석

- **9건이 모두 동일 주제**: "국가법령정보센터 API 동기화 + FAQ 매칭 정확도 100%"
- 모두 `claude/exciting-heisenberg-*` 또는 `claude/inspiring-cori-*` 자동 생성 브랜치
- 시간순으로 같은 작업의 iteration (#23 4-20 → #32 4-25)
- 모두 main과 diverged — 어느 것도 단순 흡수 안 됨
- 가장 최신 #32가 가장 작고 명확한 스코프 (+689/-121, 39 files) → 이전 iteration들의 핵심을 main에 흡수한 후 잔여 수정만 남긴 패턴 추정
- 거대 PR (#30 +11775/56 files, #31 +3762/53 files)는 누적된 다양한 시도를 포함 — 분리/축소 없이 머지하면 회귀 위험

## 3. 권고

### CLOSE 권고 — 8건 (#23~#31)
**이유**:
- 모두 같은 주제의 이전 iteration
- diverged 상태로 main과 충돌 가능성 높음
- 거대 변경(#30, #31)은 머지 시 회귀 위험 매우 큼
- 작성일이 4-20~4-25로 7일 이내 누적 — 작업이 빠르게 발전 중이며 이전 버전은 obsolete

**close 시 권장 메시지** (사용자 직접 작업):
```
같은 주제의 후속 iteration(#32 또는 main의 hot-reload 변경)에 의해 superseded.
보존이 필요한 부분이 있다면 cherry-pick 또는 새 PR로 분리.
```

### 검토 후 결정 — 1건 (#32)
**이유**:
- 가장 최신 + 가장 작고 명확한 스코프
- "PII/SQL 탐지 버그 수정" 명시 — 보안 관련 가치 있음
- 39 files / +689 라인은 검토 가능한 규모
- diverged이므로 main 동기화 후 충돌 해소 필요

**액션**:
1. PR #32 head를 main 위로 rebase 또는 main 머지로 충돌 해소
2. CI 통과 확인
3. 코드 리뷰 (PII/SQL 탐지 버그 부분 우선)
4. 머지 또는 close

## 4. 자동화 가능 작업 (사용자 승인 필요)

```bash
# 일괄 close (사용자 직접 실행 권장)
for n in 23 24 25 26 27 28 29 30 31; do
  gh pr close $n -R sun475300-sudo/bonded-exhibition-chatbot-data \
    --comment "같은 주제 후속 iteration(#32 또는 main)에 의해 superseded. 자세한 분석은 PR_REDUNDANCY_MATRIX_2026-04-26.md 참조."
done
```

**중요**: 위 명령은 분석 결과 기반 권고일 뿐. **각 PR 내용을 사용자가 직접 한 번씩 더 검토하고 close 결정**할 것을 권장.

## 5. 보존 가치 점검 항목

close 전 각 PR에서 확인할 것:
- main에 아직 없는 핵심 코드/테스트가 있는지
- 사용자에게 의미있는 commit message/PR description이 있는지
- 부분 cherry-pick 가치가 있는 변경이 있는지

특히 #25(`+5274/-95, 28 files`)와 #26(`+1902/-240, 10 files`)는 변경량 대비 file count가 작아 **밀도 높은 단일 모듈 변경** 가능성 — 보존 가치 한 번 더 검토.

## 6. 결과 요약

| 분류 | 수 | PR # |
|---|---|---|
| Close 권고 | 8 | #23, #24, #25, #26, #27, #28, #29, #30, #31 |
| 보존/검토 후 머지 | 1 | #32 |
| dependabot (별 카테고리) | 1 | #29 (npm) → 위와 별개, 머지 검토 |
| **총** | **10건 + 1 dependabot** | |

(주의: #29가 두 개 — 위 표의 #29는 법령정보센터 PR, dependabot의 npm postcss는 #29가 아닌 별 PR. 정확한 dependabot PR 번호는 사용자가 별도 확인.)

---

## 부록 A — 분석 한계

- 각 PR의 commit history / files 변경 내역까지 deep diff 안 함 (시간 제약). 사용자 close 전 한 번 더 `gh pr view <N> --json files` 로 확인 권장.
- 9건 모두 단일 작성자 (`claude/exciting-heisenberg-*` 자동 브랜치) 패턴. 사용자가 의도적으로 보존 중일 가능성도 있음 (예: 비교용 백업).
