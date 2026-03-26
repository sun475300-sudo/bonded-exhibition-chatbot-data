# 보세전시장 민원응대 챗봇

법제처 국가법령정보센터의 현행 법령과 관세청 공식 자료를 기반으로 한 보세전시장 민원응대 챗봇 시스템입니다.

---

## 시스템 아키텍처

```
                         사용자 질문
                             |
                    +--------v--------+
                    |   웹 UI / API   |
                    |  (Flask 서버)   |
                    +--------+--------+
                             |
              +--------------v--------------+
              |        질문 의도 분류기       |
              |      (10개 카테고리)         |
              +---------+----+---------+----+
                        |    |         |
              +---------v-+  |  +------v--------+
              | FAQ 매칭   |  |  | 에스컬레이션   |
              | (29개 항목) |  |  | 판단 (5규칙)  |
              +---------+--+  |  +------+--------+
                        |     |         |
              +---------v-----v---------v--------+
              |         답변 생성기               |
              |  (결론→설명→근거→면책 구조)       |
              +----------------------------------+
                             |
                        구조화된 답변
```

## 질문 처리 흐름도

```
[사용자 입력] ──> [분류기] ──> 카테고리 결정
                                  |
                    +-------------+-------------+
                    |                           |
              에스컬레이션 체크            FAQ 키워드 매칭
                    |                           |
              +-----v-----+              +------v------+
              | 트리거 됨  |              | 매칭 성공   |
              | (5개 규칙) |              | (29개 FAQ)  |
              +-----+-----+              +------+------+
                    |                           |
         +----------+----------+         +------v------+
         | 키워드   | FAQ도    |         | 확인 질문   |
         | 매칭 0개 | 매칭 됨  |         | 자동 선택   |
         +----+-----+----+----+         +------+------+
              |           |                    |
     에스컬레이션    FAQ + 에스컬       템플릿 기반 답변
     전용 답변      레이션 병행               조립
              |           |                    |
              +-----+-----+--------------------+
                    |
              [구조화된 답변 출력]
              결론 → 설명 → 확인사항 → 근거 → 면책
```

## 카테고리별 FAQ 분포

```
  제도 일반   ████████████  4개  (A, T, U, AC)
  특허/운영   █████████     3개  (F, G, AA)
  반입/반출   ████████████  4개  (B, V, W, AB)
  전시/사용   █████████     3개  (H, I, J)
  판매/직매   ██████        2개  (C, X)
  견본품     ██████        2개  (D, Z)
  시식용식품  ██████        2개  (E, Y)
  서류/신고   █████████     3개  (K, L, M)
  벌칙/제재   █████████     3개  (N, O, P)
  담당기관    █████████     3개  (Q, R, S)
  ─────────────────────────────────
  총 29개 FAQ  |  10개 카테고리
```

## 에스컬레이션 분기도

```
  사용자 질문
       |
  +----v----+     +-----------+     +-----------------+
  | ESC001  | ──> | 즉시인도  | ──> | 관할 세관       |
  +---------+     +-----------+     +-----------------+
  | ESC002  | ──> | 시식/검역 | ──> | 고객지원 (125)  |
  +---------+     +-----------+     +-----------------+
  | ESC003  | ──> | 특허/제재 | ──> | 보세산업과      |
  +---------+     +-----------+     +-----------------+
  | ESC004  | ──> | 유권해석  | ──> | 보세산업과      |
  +---------+     +-----------+     +-----------------+
  | ESC005  | ──> | 전산오류  | ──> | 기술지원 (1544) |
  +---------+     +-----------+     +-----------------+
```

## 답변 구조 템플릿

```
  +--------------------------------------------------+
  | 문의하신 내용은 [카테고리]에 관한 사항입니다.       |
  +--------------------------------------------------+
  | 결론:                                             |
  | - 한 줄 결론 (핵심 답변)                           |
  +--------------------------------------------------+
  | 설명:                                             |
  | 1. 상세 설명                                      |
  | 2. 실무상 주의사항                                 |
  +--------------------------------------------------+
  | 민원인이 확인할 사항:                              |
  | - 물품이 외국물품인지?                             |
  | - 행사 목적 (전시/판매/시식)?                       |
  | - 관할 세관 사전 협의 여부?                         |
  +--------------------------------------------------+
  | 근거:                                             |
  | - 관세법 제○조                                    |
  | - 보세전시장 운영에 관한 고시 제○조                 |
  +--------------------------------------------------+
  | 안내:                                             |
  | - 일반적 안내용 설명, 사실관계에 따라 상이          |
  | - 최종 처리는 관할 세관 확인 필요                   |
  +--------------------------------------------------+
```

## 버그 수정 이력 (발견 및 수정)

```
  버그 #1  에스컬레이션 우선순위 누락
  ───────  UNI-PASS/유권해석 질문에 무관한 FAQ 출력
  수정 →   에스컬레이션 트리거 시 FAQ 키워드 강도 확인

  버그 #2  분류기 오타
  ───────  '설영특허' (존재하지 않는 단어)
  수정 →   '설치특허'로 수정

  버그 #3  FAQ 매칭 동점 버그
  ───────  보세창고 비교 질문 → 보세전시장 정의로 매칭
  수정 →   동점 시 키워드 매칭 수 기반 타이브레이크

  버그 #4  키워드 0개 FAQ 반환
  ───────  '물류 법률' 질문 → 보세전시장 정의 FAQ 매칭
  수정 →   최소 1개 키워드 매칭 필수 조건

  버그 #5  '관세' 범용 키워드
  ───────  '수출입 관세율' → 견본품 FAQ 오매칭
  수정 →   '견본품 관세' 등 구체적 키워드로 변경

  버그 #6  '허가 없이 반출' 분류 오류
  ───────  PENALTIES가 아닌 IMPORT_EXPORT로 분류
  수정 →   PENALTIES에 '허가 없이' 키워드 추가

  버그 #7~16  대규모 스캔 16건
  ─────────  분류기 범용 키워드, 에스컬레이션 대소문자,
             같은 카테고리 내 FAQ 혼동 등
  수정 →     키워드 구체화, .lower() 비교, FAQ별 고유 키워드
```

---

## 프로젝트 구조

```
bonded-exhibition-chatbot-data/
├── config/
│   ├── system_prompt.txt          # 챗봇 시스템 프롬프트
│   └── chatbot_config.json        # 챗봇 설정 (페르소나, 카테고리, 연락처)
├── data/
│   ├── faq.json                   # FAQ 데이터셋 (29개 항목)
│   ├── legal_references.json      # 법령 근거 데이터
│   └── escalation_rules.json      # 에스컬레이션 규칙 (5개 조건)
├── templates/
│   └── response_template.json     # 답변 포맷 템플릿
├── src/
│   ├── chatbot.py                 # 메인 챗봇 로직
│   ├── classifier.py              # 질문 의도 분류기 (10개 카테고리)
│   ├── response_builder.py        # 답변 생성기
│   ├── escalation.py              # 에스컬레이션 판단 로직
│   ├── data_validator.py          # 데이터 정합성 검증기
│   ├── validator.py               # 확인 질문 관리
│   └── utils.py                   # 유틸리티 함수
├── tests/                         # 테스트 101개
│   ├── test_chatbot.py            # 통합 테스트
│   ├── test_classifier.py         # 분류기 테스트
│   ├── test_response_builder.py   # 답변 생성기 테스트
│   ├── test_escalation.py         # 에스컬레이션 테스트
│   ├── test_validator.py          # 검증기 테스트
│   ├── test_data_validator.py     # 데이터 정합성 테스트
│   ├── test_edge_cases.py         # 에지케이스 테스트
│   └── test_web_api.py            # 웹 API 테스트
├── web/
│   └── index.html                 # 웹 챗봇 UI (다크 테마)
├── web_server.py                  # Flask 웹 서버
├── simulator.py                   # 터미널 챗봇 시뮬레이터
├── requirements.txt
└── README.md
```

## 시작하기

### 설치
```bash
git clone https://github.com/sun475300-sudo/bonded-exhibition-chatbot-data.git
cd bonded-exhibition-chatbot-data
git checkout claude/find-logic-bug-pFtiQ
pip install -r requirements.txt
```

### 웹 챗봇 실행
```bash
python web_server.py --port 8080
# 브라우저에서 http://127.0.0.1:8080 접속
```

### 터미널 시뮬레이터
```bash
python simulator.py              # 대화형 모드
python simulator.py --test       # 자동 테스트 10개 시나리오
python simulator.py -q "질문"    # 단일 질문
```

### 테스트 실행
```bash
python -m pytest tests/ -v       # 101개 테스트
```

## 핵심 법적 근거

| 법령 | 조문 | 내용 |
|------|------|------|
| 관세법 | 제190조 | 보세전시장 정의 |
| 관세법 | 제161조 | 견본품 반출 (세관장 허가) |
| 관세법 시행령 | 제101조 | 판매용품의 면허전 사용금지 |
| 관세법 시행령 | 제102조 | 직매된 전시용품의 통관전 반출금지 |
| 관세청 고시 | 제2026-15호 | 보세전시장 운영에 관한 고시 |

## 운영자 가이드: 내 사이트에 챗봇 적용하기

### 방법 1. 독립 서버로 운영 (가장 간단)

서버 1대에 Flask를 띄우고, 기존 사이트에서 iframe이나 링크로 연결합니다.

```bash
# 1. 서버에 코드 배포
git clone https://github.com/sun475300-sudo/bonded-exhibition-chatbot-data.git
cd bonded-exhibition-chatbot-data
pip install -r requirements.txt

# 2. 서버 실행 (백그라운드)
nohup python web_server.py --port 8080 --host 0.0.0.0 &

# 3. 프로덕션 배포 시 gunicorn 사용 권장
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8080 web_server:app
```

기존 사이트 HTML에 iframe 삽입:
```html
<iframe src="http://챗봇서버주소:8080"
        width="400" height="600"
        style="border:none; border-radius:12px; box-shadow:0 4px 24px rgba(0,0,0,0.15);">
</iframe>
```

### 방법 2. 팝업 위젯으로 기존 사이트에 삽입

기존 웹사이트의 `</body>` 바로 위에 아래 코드를 붙여넣으면 우측 하단에 챗봇 버튼이 생깁니다.

```html
<!-- 보세전시장 챗봇 위젯 -->
<div id="chatbot-widget" style="position:fixed;bottom:24px;right:24px;z-index:9999;">
  <iframe id="chatbot-frame" src="http://챗봇서버주소:8080"
          style="display:none;width:400px;height:600px;border:none;border-radius:12px;
                 box-shadow:0 8px 32px rgba(0,0,0,0.3);"></iframe>
  <button onclick="
    var f=document.getElementById('chatbot-frame');
    f.style.display=f.style.display==='none'?'block':'none';
  " style="width:60px;height:60px;border-radius:50%;border:none;
           background:linear-gradient(135deg,#1565C0,#1E88E5);color:#fff;
           font-size:24px;cursor:pointer;box-shadow:0 4px 16px rgba(21,101,192,0.4);">
    B
  </button>
</div>
```

### 방법 3. API만 사용 (자체 UI 구축)

챗봇 서버의 REST API만 호출하여 자체 UI에서 사용합니다.

```
POST /api/chat
Content-Type: application/json

요청: {"query": "보세전시장이 무엇인가요?"}

응답: {
  "answer": "문의하신 내용은 [제도 일반]에 관한...",
  "category": "GENERAL",
  "categories": ["GENERAL"],
  "is_escalation": false,
  "escalation_target": null
}
```

API 엔드포인트 목록:

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| `/api/chat` | POST | 질문 처리 및 답변 반환 |
| `/api/faq` | GET | FAQ 29개 목록 반환 |
| `/api/config` | GET | 챗봇 설정 (카테고리, 연락처) |
| `/api/health` | GET | 서버 상태 확인 |

JavaScript 호출 예시:
```javascript
async function askChatbot(question) {
  const res = await fetch('http://챗봇서버주소:8080/api/chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({query: question})
  });
  const data = await res.json();
  console.log(data.answer);      // 답변 텍스트
  console.log(data.category);    // 분류 카테고리
  console.log(data.is_escalation); // 에스컬레이션 여부
}
```

### 방법 4. 카카오톡/슬랙 연동

카카오톡 챗봇이나 슬랙 봇에서 `/api/chat` API를 호출하면 동일한 답변을 받을 수 있습니다.

```
카카오톡 스킬서버 / 슬랙 Webhook
         |
    POST /api/chat  ──>  챗봇 서버  ──>  JSON 응답
         |
    응답을 카카오/슬랙 형식으로 변환하여 반환
```

### FAQ/법령 데이터 수정 방법

운영 중 FAQ를 추가하거나 법령이 개정되면:

```
1. data/faq.json          ← FAQ 추가/수정
2. data/legal_references.json  ← 법령 근거 업데이트
3. data/escalation_rules.json  ← 에스컬레이션 규칙 변경
4. src/classifier.py      ← 분류 키워드 조정 (필요 시)
```

FAQ 항목 추가 형식:
```json
{
  "id": "NEW01",
  "category": "GENERAL",
  "question": "새로운 질문?",
  "answer": "답변 내용...",
  "legal_basis": ["관세법 제○조"],
  "notes": "",
  "keywords": ["키워드1", "키워드2", "키워드3"]
}
```

수정 후 검증:
```bash
python -m pytest tests/ -v          # 기존 테스트 통과 확인
python simulator.py --test          # 시나리오 테스트
python -c "from src.data_validator import run_all_validations; print(run_all_validations())"  # 정합성 검증
```

### 운영 시 주의사항

| 항목 | 내용 |
|------|------|
| 법령 업데이트 | 관세청 고시 개정 시 `data/` 내 JSON 파일 갱신 필요 |
| 면책 문구 | 모든 답변에 자동 포함됨, 제거 금지 |
| 에스컬레이션 | 5개 규칙에 해당하면 사람 상담 연결 안내 자동 출력 |
| CORS 설정 | 다른 도메인에서 API 호출 시 Flask-CORS 설치 필요 |
| HTTPS | 프로덕션 배포 시 nginx + SSL 인증서 적용 권장 |
| 로깅 | 민원 분석을 위해 질문/답변 로그 저장 기능 별도 구축 권장 |

---

## 전체 로직 정밀 분석 결과

### 발견된 문제 분포

```
  높음  ████████  8건
  중간  ██████    6건
  낮음  ██████    6건
  ──────────────────
  총 20건 발견
```

### 심각도 높음 (8건)

```
  #  파일                  문제                                        상태
  ── ──────────────────── ────────────────────────────────────────── ──────
  1  chatbot.py L88-93    에스컬레이션 우선순위 dead code 가능성       확인됨
                          → FAQ 키워드 1개 이상 매칭 필수 조건으로
                            인해 escalation_only가 항상 False
  2  classifier.py L89    동점 카테고리 알파벳 순 정렬                 확인됨
                          → 도메인 로직과 무관한 임의 우선순위
  3  chatbot.py L36       정규화 방식 불일치                          확인됨
     vs classifier.py L72 → normalize_query vs .lower() 차이로
                            공백 포함 키워드 매칭 결과 불일치
  4  chatbot.py L117      .split(".")[0] 결론 추출                    확인됨
                          → FAQ-B "네." 등 짧은 도입부만 잘림
  5  web_server.py L62    categories[0] IndexError 가능성             확인됨
                          → classify_query가 빈 리스트 반환 시
  6  web_server.py 전체    글로벌 에러 핸들러 부재                     확인됨
                          → 500 에러 시 HTML 반환 (JSON API 불일치)
  7  web_server.py L109   --debug 프로덕션 노출 위험                  확인됨
                          → Werkzeug 디버거 원격 코드 실행 가능
  8  web_server.py 전체    로깅 시스템 부재                            확인됨
```

### 심각도 중간 (6건)

```
  #  파일                  문제
  ── ──────────────────── ──────────────────────────────────────────
  9  classifier.py L78    부분 문자열 매칭 → "사다"가 "사다리" 매칭
  10 chatbot.py L46       카테고리 보너스 +2 매직넘버 하드코딩
  11 web_server.py L53    query 타입 미검증 (숫자/배열 시 에러)
  12 web_server.py L53    query 길이 제한 없음 (DoS 벡터)
  13 web_server.py L22    정적 파일 상대 경로 → CWD 의존
  14 legal_references     관세법 제269조, 제183조 누락
```

### 심각도 낮음 (6건)

```
  #  파일                  문제
  ── ──────────────────── ──────────────────────────────────────────
  15 classifier.py L8-60  분류 키워드 코드 하드코딩 (JSON과 이중관리)
  16 chatbot.py L61       최소 매칭 임계값 1 매직넘버
  17 escalation.py        매 호출마다 JSON 파일 재로드 (캐싱 없음)
  18 legal_references     관세법 제226조 URL 빈 문자열
  19 web_server.py        CORS 미설정 (분리 배포 시 문제)
  20 web_server.py        WSGI 서버 미사용 (개발 서버 직접 실행)
```

### 질문 처리 흐름 내 문제 위치 시각화

```
  [사용자 입력]
       |
       v
  ┌─────────────────────────────┐
  │ classifier.py               │
  │ #2 동점→알파벳순 (임의 우선) │
  │ #3 .lower()만 사용          │
  │ #9 부분 문자열 매칭 위험     │
  │ #15 키워드 하드코딩          │
  └──────────┬──────────────────┘
             v
  ┌─────────────────────────────┐
  │ chatbot.py                  │
  │ #1 에스컬레이션 dead code   │
  │ #3 normalize_query 불일치   │
  │ #4 .split(".") 결론 잘림    │
  │ #10 보너스+2 매직넘버       │
  └──────────┬──────────────────┘
             v
  ┌─────────────────────────────┐
  │ web_server.py               │
  │ #5 IndexError 가능성        │
  │ #6 에러핸들러 부재          │
  │ #7 디버그 모드 위험         │
  │ #8 로깅 부재               │
  │ #11 타입 미검증            │
  │ #12 길이 미제한            │
  └─────────────────────────────┘
```

### 데이터 정합성 검사 결과

```
  검사 항목                              결과
  ────────────────────────────────────── ──────
  FAQ 29개 스키마 일관성                  PASS
  FAQ category ↔ config 매핑              PASS
  에스컬레이션 target ↔ contacts 매핑      PASS
  legal_references ↔ FAQ 법령 참조        FAIL (2건 누락)
    - 관세법 제269조(밀수출입죄) 미정의
    - 관세법 제183조(보세창고) 미정의
  legal_references URL 완전성             FAIL (1건 빈 URL)
    - 관세법 제226조 URL 비어있음
```

---

## 업데이트 내역

| 커밋 | 내용 |
|------|------|
| `446d9a2` | feat: 챗봇 전체 시스템 구축 (FAQ 7개, 분류기, 답변 생성기, 에스컬레이션, 테스트 61개) |
| `853bea3` | feat: FAQ 29개 확장, 분류기 강화, 데이터 검증기, 에지케이스 테스트 |
| `eb591f3` | feat: 웹 챗봇 인터페이스 구축 (Flask API + HTML/JS UI) |
| `a02838f` | fix: FAQ 매칭 로직 버그 + 웹 UI 전면 개선 (다크 테마, 구조화 렌더링) |
| `aaef67c` | fix: 분류기 키워드 정확도 개선 ('관세' 범용, '허가 없이' 분류 오류) |
| `95d9eba` | fix: 대규모 버그 스캔 16건 일괄 수정 (분류기, 에스컬레이션, FAQ 변별력) |

## 라이선스

이 프로젝트의 법령 데이터는 법제처 국가법령정보센터 및 관세청 공식 자료를 참고하였습니다.
