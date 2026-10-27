# 변경 기록 (change.md)

2026-10-03 세션에서 요청받은 수정과 실제로 바꾼 내용을 정리한 문서입니다.
요청 순서대로 적었고, 마지막에 남은 할 일과 알아둘 점을 모았습니다.

---

## 0. 요청: "오류나 개선할 것이 있으면 알려줘"

코드와 데이터를 점검해 아래 10개 항목과 정리 대상을 찾았습니다. 이 단계에서는 코드를 바꾸지 않았습니다.

| # | 내용 | 처리 |
|---|---|---|
| 1 | 요약의 88%(296/336건)가 한국어 요약이 아니라 영어 부제 복사 | 2번 요청에서 처리 |
| 2 | 메일 HTML에 이스케이프가 없음 | 2번 요청에서 처리 |
| 3 | 수집이 실패해도 "성공"으로 끝남 | 2번 요청에서 처리 |
| 4 | 메일 한 통에 들어가는 건수 제한이 없음 | 2번 요청에서 처리 |
| 5 | `config.yaml`의 `sources`와 `base_url`을 무시함 | 3번 요청에서 처리 |
| 6 | 유료 글 판정 조건 불일치 | 2번 요청에서 같이 처리 |
| 7 | 같은 빌드 작업이 여러 번 실행됨 | 3번 요청에서 처리 |
| 8 | 상대경로 사용으로 실행 위치에 따라 깨짐 | 3번 요청에서 처리 |
| 9 | 효과 없는 `embedded-data` 코드 | 3번 요청에서 처리 |
| 10 | 같은 URL 목록을 담은 상태 파일이 4개 | 3번 요청에서 처리 |

점검 당시 데이터는 336건이었고 `results.json`, `processed.json`, `seen.json`, `sent.json`, jsonl, `docs/items.json`이 서로 일치했습니다.

---

## 1. 요청: "중복파일 정리"

### 삭제한 파일 (내용이 같은 쪽)
- `scripts/record_sent.py`, `scripts/sent.py`, `scripts/update_sent.py` → `scripts/mark_sent.py`만 남김
- `scripts/filter_pending.py` → `scripts/pending.py`만 남김
- `scripts/generate_redesigned_index.py` → `scripts/generate_nordic_index.py`만 남김
- `index_old.html`, `dashboard.html`

### 합치거나 이름을 바꾼 파일
- 루트 `dedup.py`(results.json·processed.json까지 처리)를 `scripts/dedup.py`로 옮기고, 원래 `scripts/dedup.py`와 `scripts/dedup_seen.py`는 삭제
- 루트 `build_dashboard.py` → `build_site.py`로 이름 변경 (`scripts/build_dashboard.py`와 이름만 같고 역할이 달랐음)
  - `build_site.py`: `assets/data.js` 생성 + HTML·assets를 `docs/`로 동기화
  - `scripts/build_dashboard.py`: `docs/items.json` 생성
- `build_site.py`에서 `dashboard.html` 복사 단계 제거, `monitor.py`의 import 수정
- README 폴더 구조 설명 갱신

### 참고
- 삭제 전 파일들을 scratchpad의 `backup_dupes/`에 복사해 두었지만 임시 폴더라 오래 남지 않을 수 있습니다.
- 대부분 git에 커밋되지 않은 파일이라 git으로는 복구할 수 없습니다.

---

## 2. 요청: 점검 항목 1~4번 수행

### 1번. 한국어 요약 생성 (Claude API)
- 새 파일 `summarizer.py`
  - `claude-sonnet-5-5`로 제목·부제·설명을 받아 "사실만, 한국어 3문장 이내"로 요약
  - 결과는 `data/summaries.json`에 slug별로 캐시 (같은 글은 다시 요약하지 않음)
  - `ANTHROPIC_API_KEY`는 환경변수 또는 `.env`에서 읽음. 키가 없거나 호출이 실패하면 `None`을 반환하고, 호출한 쪽이 부제로 대신함
- `monitor.py`
  - 코드에 직접 적어 두었던 `SUMMARIES` 사전(44건)을 `data/summaries.json`으로 이동
  - `make_summary()`가 `summarizer.get_summary()`를 사용
- 새 파일 `scripts/backfill_summaries.py`
  - 캐시에 없는 글을 요약해서 `results.json`, `data/items/*.jsonl`, 캐시를 갱신
  - 옵션: `--dry-run`(비용 추정만), `--limit N`, `--workers N`, `--no-build`
  - 키가 없으면 아무것도 바꾸지 않고 정상 종료
- `run_daily.py`에 "요약 보강" 단계 추가 (하루 최대 20건)
- `.env.example`에 `ANTHROPIC_API_KEY` 안내 추가
- `pip install anthropic`으로 SDK 설치 (1.11.0)

### 2번. 메일 HTML 이스케이프 (`scripts/send_email.py`)
- 제목, 요약, 출처, 날짜, 제목 머리말을 `html.escape()`로 처리
- 링크는 `http(s)`로 시작할 때만 허용하고, 그 밖의 스킴(`javascript:` 등)은 `#`로 대체

### 3번. 수집 실패 감지 (`monitor.py`)
- 첫 페이지를 가져오지 못하면 `RuntimeError`를 던져 비정상 종료 → `run_daily` 로그에 실패로 기록되고 이후 단계 중단

### 4번. 발송 상한 (`scripts/send_email.py`, `config/email.yml`)
- `config/email.yml`에 `max_items: 30` 추가
- 한 통에 최대 30건만 발송하고, 보낸 항목만 `sent.json`에 기록
- 남은 항목은 `docs/pending.json`에 남겨 다음 실행에서 발송 (메일 끝에 "남은 N건은 다음 발송에" 문구 표시)

### 6번 (같이 처리)
- 유료 글 판정을 `audience != "everyone"` 하나로 통일하고, 유료 안내 문구를 `PAYWALL_NOTE` 상수로 분리

---

## 3. 요청: 점검 항목 5~10번 수행

### 5번. 설정 반영
- `config/config.yaml`의 각 소스에 `archive:` 주소 추가 (`https://newsletter.semianalysis.com`)
- `monitor.py`
  - `load_period()`를 `load_config()`로 확장해 `period`와 `sources`를 함께 읽음 (PyYAML 없이 이 파일 형식만 해석)
  - `sources`마다 `archive` 주소로 수집하고, 메일에 찍히는 `source`도 소스별 값을 사용
  - 일부 소스가 실패하면 나머지는 계속 수집하고 실패 사유를 기록, 전부 실패하면 예외로 종료
  - `sources`가 없거나 `archive`가 없는 소스는 건너뛰고 사유 기록

### 7번. 중복 실행 제거
- `monitor.py`에서 사이트 빌드 호출(`build_all()`, `scripts/build_dashboard.py`)을 제거
- `run_daily.py`에 "사이트 빌드"(`build_site.py`) 단계를 추가해 빌드를 한 번만 수행
- 요약 보강 단계에는 `--no-build`를 붙임 (수동 실행 시에는 끝에 사이트 데이터도 갱신)

### 8번. 상대경로 제거
- `scripts/build_dashboard.py`, `scripts/pending.py`, `scripts/mark_sent.py`, `scripts/check_posts.py`를 `BASE_DIR`(파일 위치 기준) 경로로 변경

### 9번. 효과 없는 코드 제거
- `scripts/build_dashboard.py`에서 `embedded-data` 갱신 블록 삭제

### 10번. 상태 파일 정리
- `data/state/seen.json`을 중복 판정의 단일 기준으로 통일
- `data/processed.json` 삭제, `scripts/dedup.py`에서도 해당 처리 제거
- `seen.json`이 없거나 깨지면 `output/results.json`에서 복구
- `results.json`(사이트용 전체 레코드)과 jsonl(월별 로그, 메일·`docs/items.json` 원본)은 역할이 달라 유지

### README 갱신
- 폴더 구조(`summarizer.py`, `backfill_summaries.py`, `build_site.py`, `summaries.json`), 수집 단계 설명, 사이트 빌드 명령 반영

---

## 4. 현재 일일 파이프라인 (`run_daily.py`, 7단계)

1. `monitor.py` — `sources`에서 새 글 수집
2. `scripts/backfill_summaries.py --limit 20 --no-build` — 요약 보강
3. `scripts/dedup.py` — 중복 제거
4. `scripts/build_dashboard.py` — `docs/items.json`
5. `build_site.py` — `assets/data.js` + `docs/` 동기화
6. `scripts/pending.py` — 미발송 목록
7. `scripts/send_email.py` — 메일 발송 (최대 `max_items`건)

---

## 5. 검증 결과

통과한 시험 (가짜 입력·임시 폴더 사용, 실제 메일 발송 없음):
- HTML 이스케이프, 위험한 링크 차단
- 발송 상한: 8건을 3·3·2건으로 나눠 발송, 마지막 메일에는 "남은 건수" 문구 없음
- 수집 실패 시 예외, 다중 소스 수집, 일부/전체 소스 실패, `seen.json` 유실 복구
- 요약 모듈의 응답 처리와 키 없을 때 동작, 백필 `--dry-run`(296건, 예상 약 $1.15)
- 다른 폴더에서 `dedup`·`build_dashboard`·`pending`·`build_site` 실행
- 실제 Substack API 첫 페이지 읽기(23건, 최신 글 2026-09-28)

하지 못한 시험:
- **실제 Claude API 호출** (API 키 없음). 첫 실행에서 오류가 날 수 있음
- `run_daily.py` 전체 실행 (메일이 실제로 나갈 수 있어 생략)

---

## 6. 남은 할 일

1. [console.anthropic.com](https://console.anthropic.com)에서 크레딧 충전 후 API 키 발급
2. `.env`에 `ANTHROPIC_API_KEY=sk-ant-...` 추가
3. `python scripts/backfill_summaries.py --limit 3`으로 요약 품질 확인
4. 괜찮으면 `python scripts/backfill_summaries.py`로 전체(약 296건, 약 $1) 실행
5. 작업 스케줄러가 `anthropic`이 설치된 같은 Python으로 실행되는지 확인
6. 첫 자동 실행 후 `logs/daily_*.log`에서 7단계가 모두 ✔인지 확인

## 7. 알아둘 점

- 요약 입력은 제목·부제·설명뿐이라, 본문이 없는 글은 요약이 짧거나 제목 수준일 수 있음. 무료 글(94건)은 본문을 가져오면 더 풍부해짐 (미구현)
- 모델 거절 시 다른 모델로 넘기는 폴백 옵션은 켜지 않음. 실패하면 부제로 대체됨
- 요약 캐시가 slug 기준이라, 소스를 늘렸을 때 다른 소스에서 slug가 같은 글이 나오면 요약이 섞일 수 있음
- 소스를 추가하려면 `config.yaml`에 `label`, `url`, `archive` 세 줄을 넣으면 됨
- `config/email.yml`의 수신자가 아직 테스트 주소(`test@example.com`)임. Resend 도메인 인증 후 변경 필요
- `.claude/settings.local.json`은 개인 권한 설정이므로 `.gitignore`에 추가하는 것을 권장
- 삭제한 파일은 대부분 git에 커밋된 적이 없어 되돌릴 수 없음

---

## 8. 2026-10-06 세션: `index.html` 파이프라인 연동 및 정리

### 요청 순서와 처리 내용

1. **사전 점검** — 루트 파일이 `index.html.html`(이중 확장자)이라 `build_site.py`가 `docs/`로 복사하지 못하고 있었음. 데이터는 `index.html` 안에 하드코딩(URL 313줄)되어 파이프라인 결과와 무관했음.
2. **`index.html`을 기준으로 데이터 연동**
   - `<script src="assets/data.js">`의 `window.POSTS`(336건)를 읽도록 변경, 로드 실패 시 `STATIC_SOURCE_LINES`로 폴백
   - 제목·발행일(`published_at`)·요약·유료 여부를 실데이터로 표시. 건수·날짜 하드코딩(313/97/216, `2026-10-04`) 제거
   - 슬러그 연도 기반 발행일 추정은 실제 날짜가 없을 때만 사용, 감사 T4는 실제 날짜가 있는 기사의 슬러그 연도 검사를 생략
3. **불필요 파일 삭제** — `index.html.bak_*`, 빈 `새 텍스트 문서*.txt` 2개, `__pycache__`, `index.html`을 덮어쓰던 구 생성기 4개(`generate_dashboard_html.py`, `build_full_system.py`, `generate_nordic_index.py`, `generate_detail_dashboard.py`), 일회성 스크립트 5개(`test_load`, `test_pages`, `backfill_phase2`, `enhance_phase3`, `verify_links`)와 `docs/review_phase3.md`. 파이프라인이 쓰는 파일은 유지.
4. **템플릿 인사이트 제거** — `generateDeepReport()`(근거 없는 수치·일반 문장)를 삭제하고 실데이터만 쓰는 `buildReport()`로 교체. 큐레이션 44건은 `points`/`takeaway`/`entities` 표시, 나머지 292건은 피드 요약만 표시. 배지는 "큐레이션 요약/피드 요약". 모달의 "핵심 기술" 섹션 삭제.
5. **카테고리 분류 개선** — 파이프라인 `category` 코드 우선(`CATEGORY_CODE_MAP`) + 슬러그·제목 키워드 규칙(`CATEGORY_RULES`). 전략·지정학 149건(44%) → 85건(25%).
6. **폰트·크기 정리** — 한글 폰트 Pretendard 추가, 글자 크기 9종 → 7종(10px/11px → 12px), `font-black` → `font-extrabold`.
7. **무결성 검증 패널 삭제** — 상단 5/5 PASS 바 제거, 헤더 배지를 `N Articles` 하나로 정리. 점검 로직은 내부에서만 실행하고 실패 시 콘솔 경고. 탭 제목·MD 내보내기의 "무결성 검증" 문구 제거.
8. **커밋** — `837f7da` (저장소 첫 커밋). `.claude/settings.local.json`, `scheduled_tasks.lock`을 `.gitignore`에 추가.
9. **메모리·`CLAUDE.md` 정리** — 작업 방식·주의사항을 메모리 7건과 프로젝트 `CLAUDE.md`로 정리.

### 사고 기록
- **`python run_daily.py --help`를 실행** — 인자 처리가 없어 전체 파이프라인이 실제로 실행됨. 신규 글이 없어(미발송 0건) 메일은 발송되지 않았고 데이터도 336건 그대로. 이후 테스트 용도로 실행하지 않기로 함.
- **스크립트 편집 실수로 `index.html`이 중복(2,132줄)** — 마커 검색이 파일 앞쪽 동일 문자열을 잡았음. `docs/index.html` 정상본에서 복원 후 검색 시작 위치를 지정해 재적용(최종 1,582줄).

### 검증
- 무결성 점검 T1~T5 전부 통과, 336건 중복 0건, 콘솔 에러 없음
- 브라우저(Chrome)에서 렌더링·검색·카테고리 필터·상세 모달 확인

### 남은 할 일
- `ANTHROPIC_API_KEY`가 없어 요약 보강 단계는 계속 건너뜀 — 키를 넣으면 292건의 피드 요약이 하루 20건씩 채워짐
- 카테고리는 키워드 기반이라 일부 오분류 가능(예: Rivos RISC-V → 데이터센터). `CATEGORY_RULES` 조정으로 해결
- `config/email.yml`의 개인 수신자 주소는 공개 푸시 전에 제외 필요
- Pretendard는 CDN 로드라 오프라인에서는 OS 기본 한글 폰트로 대체됨(필요하면 `assets/`에 로컬 호스팅)

---

## 9. 2026-10-12 세션: 대시보드에 요약 md 병합

### 요청 순서와 처리 내용
1. **`docs/index.html` → `index.html` 업데이트 문의** — 두 파일의 해시가 동일해 변경할 내용이 없었음. 루트 `index.html`이 원본이고 `docs/`는 `build_site.py`가 덮어쓰는 산출물이라는 방향을 확인.
2. **`<title>`/요약 반영 방법 문의** — `<title>`은 고정 문자열이고 본문 데이터는 `assets/data.js`의 `window.POSTS`에서 온다는 점을 확인.
3. **`output/semianalysis/summary/` 복사 여부** — `docs/summaries/`와 312개 파일명·내용이 모두 같아 복사 불필요. 동기화는 `scripts/build_catalog.py`가 담당.
4. **대시보드 요약/본문을 summary 폴더 내용으로 채우기** — `scripts/build_data.py`에 요약 md 병합을 추가(`index.html`은 이미 `points`/`takeaway`/`summary`를 렌더링하므로 수정 없음).

### 변경 내용 (`scripts/build_data.py`)
| 요약 md 섹션 | `POSTS` 필드 |
|---|---|
| Executive Summary | `summary` (기존 피드 요약은 `feed_summary`로 보존) |
| Key Insights (`###` 제목, 구형은 최상위 불릿) | `points` (최대 5개) |
| Conclusion | `takeaway` |

- 매칭: 요약 헤더의 `/p/<slug>` URL 우선, 없으면 정규화한 제목. 339건 중 312건 병합.
- 기존 `points`/`takeaway`(큐레이션)는 덮어쓰지 않음.
- 카드가 `innerHTML`을 쓰므로 요약의 `<`/`>`는 전각 `＜`/`＞`로 치환(5개 파일).
- 커밋: `a774687` (`scripts/build_data.py`, `assets/data.js`, `docs/assets/data.js`)

### 검증
- 브라우저(Chrome)에서 상세 모달의 핵심 포인트·요약·TAKEAWAY가 요약 내용으로 표시됨, 콘솔 에러 없음
- `assets/data.js`와 `docs/assets/data.js` 해시 일치

### 남은 할 일
- 요약 파일이 없는 27건(이 중 6건은 `points`도 없음, 예: 최신 글 "Beijing Will Not Pace the Frontier")은 `scripts/batch_summarize.py`로 요약을 만든 뒤 `python build_site.py`
- 카드의 "요약"은 `line-clamp-2`라 Executive Summary의 앞 두 줄만 보임. 길이 조정이 필요하면 `index.html` 카드 영역을 수정
- 작업 트리의 미커밋 변경(삭제된 `.agents/skills`, `docs/summaries/`, 신규 스크립트 등)은 별도 정리 필요

## 2026-10-13 — 모달에 요약 1~10번 항목 표시 (개요/상세/참고 탭)

### 변경
- `scripts/build_data.py`: 요약 md 전체(헤더 표 + 1~10번)를 구조화하는 `parse_detail()` 추가. 글별로 `assets/summaries/<slug>.json`을 생성하고, `data.js`에는 `has_detail: true` 플래그만 넣어 용량 증가를 막음. 빌드 때마다 이전 JSON은 지우고 재생성.
- `index.html`: 모달에 탭 3개 추가. JSON이 있으면 모달을 열 때 fetch해서 렌더, 없거나 실패하면 기존 화면(핵심 포인트·요약·한 줄 요점)을 그대로 유지.
  - 개요: Executive Summary, Key Insights(원문 근거/필자 판단 라벨 색 구분), Conclusion
  - 상세: 핵심 수치 표(성격 배지), Topic-by-Topic 카드, 기술 관점
  - 참고: 한계 및 주의점, 후속 확인 과제(체크리스트), 용어집 (부록(10번)은 표시하지 않음, JSON에도 미포함)
  - 타이포: 본문 16px/줄간격 28px, 라벨·배지 14px, 섹션 제목 18px. 라벨 칩 높이를 본문 줄 높이에 맞춰 첫 줄 정렬.
  - 라벨 박스(정의/결과/조건/필자 판단 등), 표의 성격·출처 배지, 인사이트 태그 배지, 개수 배지를 모두 제거. 문장은 불릿(•)으로만 구분하고, 인사이트 제목은 "1. [태그] 제목" 형식의 텍스트로 표시. 표는 텍스트 셀만 사용. (JSON의 label 값은 유지하므로 필요하면 다시 표시 가능)
- 요약 312건 전부 JSON 생성 (`docs/assets/summaries/`로 복사됨).

  - 모달 가로 폭 15% 확대(`max-w-[64.4rem]`, xl `max-w-[73.6rem]`).
  - 모달 푸터에 "Word 다운로드" 버튼: 개요/상세/참고 전체(또는 상세 JSON이 없는 글은 기본 보기)를 `.docx`(OOXML)로 클라이언트에서 직접 생성해 저장. 파일명 `<발행일>_<slug>.docx`. 외부 라이브러리 없이 무압축 ZIP + XML(styles/numbering 포함)로 구성, 제목 스타일·불릿·표 지원.

### 검증
- Chrome에서 `intel-panther-lake-teardown` 모달의 3개 탭 확인, 콘솔 에러 없음.

### 주의
- 요약 md 헤더에 "개인 아카이브용, 외부 공유 지양"이 있어 상세 JSON은 **비공개**로 결정: `.gitignore`에 `assets/summaries/`, `docs/assets/summaries/` 추가, `build_site.py`가 `docs/`로 복사할 때 `summaries`를 제외. 공개 사이트에서는 JSON이 없어 기존 화면(핵심 포인트·요약·한 줄 요점)으로 표시되고, 탭 보기는 로컬(`index.html`)에서만 동작.
- 모달 JSON이 없는 27건은 기존 화면으로 표시됨.

## 2026-10-13 — file://에서도 모달 탭이 보이도록 상세 데이터 로드 방식 변경

- 원인: `index.html`을 더블클릭(`file://`)으로 열면 브라우저가 `fetch`로 JSON을 읽는 것을 막아 탭이 나오지 않았음(서버로 열면 정상).
- 변경: 글별 상세 데이터를 `assets/summaries/<slug>.json` 대신 `<slug>.js`(`window.POST_DETAIL[slug] = {...}`)로 생성하고, 모달이 열릴 때 `<script>`를 동적으로 붙여 읽음(`fetchDetail`). Word 다운로드도 같은 로더 사용.
- 비공개 유지: `assets/summaries/`는 계속 `.gitignore` 및 `docs/` 복사 제외 대상.
- 요약 생성: Kimi K3, Beijing 요약 생성(`batch_summarize.py`) → 339건 중 314건 반영. Cerebras는 원문만 수집, 요약은 Gemini 월 지출 한도(429)로 보류. 나머지 24건은 원문 없음.


## 2026-10-13 — 게시판 기본 보기를 테이블 리스트로 변경

- `index.html`: `state.viewMode` 기본값을 `grid`에서 `list`로 변경하고, 초기 로드 시 `setViewMode(state.viewMode)`로 토글 버튼 상태와 렌더를 맞춤. 그리드 카드 뷰는 토글 버튼으로 계속 선택 가능.

## 2026-10-13 — 전체 폰트를 Noto Sans KR로 통일, 자간 -0.02em

- `index.html`: Tailwind `fontFamily.sans/mono`를 모두 Noto Sans KR(폴백 Malgun Gothic, Apple SD Gothic Neo)로 변경. Inter, JetBrains Mono, Pretendard 로드 제거, Google Fonts의 Noto Sans KR(300~800)만 로드.
- 전역 CSS `*{letter-spacing:-0.02em !important}`로 자간 통일(Tailwind `tracking-*` 유틸리티 포함 덮어씀).
- CLAUDE.md의 폰트 안내(Inter + JetBrains Mono + Pretendard)는 이 변경과 달라졌으므로 갱신 필요.

## 2026-10-13 — 폰트를 Pretendard로 변경, 게시판 메인 글자 크기 확대

- `index.html`: Tailwind `fontFamily.sans/mono`와 `html, body`를 Pretendard Variable(폴백 Malgun Gothic 등)로 변경. Noto Sans KR 로드 제거, jsDelivr의 Pretendard 가변 폰트 서브셋만 로드. 자간 -0.02em 전역 규칙은 유지.
- 게시판 리스트 뷰(기본): 제목 16px→20px(`text-lg sm:text-xl`), 날짜 12~14px→14~16px, 요약 줄 12~14px→16px, 표 헤더 12~14px→14~16px, 번호 14px→16px, 카테고리·접근 배지 12px→14px, 행 상하 패딩 16px→20px. `uppercase tracking-wider font-mono` 제거.
- 그리드 카드: 요약·핵심 포인트 12~14px→14~16px, 카테고리·근거·태그 배지 12px→14px.
- CLAUDE.md의 폰트 안내(Inter + JetBrains Mono + Pretendard)는 현재와 달라 갱신 필요.

## 2026-10-21 — 게시판을 Full HD 3분할 레이아웃으로 개편 (`index.html`)

- 레이아웃: 좌측 사이드바(240px) / 중앙 목록(640px) / 우측 상세 패널(나머지). 기존 모달을 없애고 글을 누르면 우측 패널에 개요·상세·참고 탭이 바로 표시됨. 1500px 미만에서는 사이드바를 숨김.
- 사이드바: 카테고리 > 소분류(펼침식), 연도(3열 동일 크기 박스). 카테고리 글자 17px. 이전 상단 통계 카드 5개, 카테고리 필 버튼, 그리드/리스트 전환, 정렬 선택은 제거(목록은 발행일 최신순 고정).
- 분류: `detectCategory`가 코드 → 대분류 키워드 → 소분류 키워드 순으로 찾고, 어디에도 안 걸리면 `기타`(`etc`, 28건)로 분리. 이전에는 기본값이 `전략·지정학`이었음. 소분류는 `SUBS`(대분류별 키워드 규칙)로 판정, 대분류 이름도 짧게 변경(패키징·메모리, 가속기, LLM 경제성, 데이터센터·인프라, 전략·지정학).
- 검색: 입력 즉시 반영. 검색 중 사이드바 숫자는 `검색결과/전체`로 병기하고 0건은 흐리게 표시. 검색창 `Esc`로 검색어 삭제, 헤더의 `✕ 필터 초기화`로 검색·카테고리·소분류·연도·북마크 필터 일괄 해제.
- 최신 글 강조: 발행 후 7일 이내이면서 안 읽은 글은 글로우 박스 + `NEW` 배지(`FRESH_MS`). 읽으면 해제되고, 읽음 기록은 `localStorage`(`semianalysis_read`). 동작 줄이기 설정에서는 애니메이션 끔.
- 테마: 기본 Light(OS 설정을 따르지 않음), 헤더 버튼으로 전환하며 선택값 저장. 제목을 "SemiAnalysis 분석리포트"(ExtraBold)로 변경.
- 유지: 북마크(목록 별표·상세 버튼·헤더 필터), Word 다운로드, MD 내보내기, 무결성 점검, 키보드(`/` 검색, J·K 이동, Enter 원문).
- 시안 파일 `mockup_fullhd.html`은 반영 후 삭제함(미추적 파일이라 git으로는 복구 불가).
- 확인: 1920×1080 Chrome에서 목록·탭·북마크·다크 모드 동작, 콘솔 오류 없음. `build_site.py`로 `docs/index.html` 동기화.
- 알려진 한계: 1100px 이하(모바일)용 별도 레이아웃은 없음. 글자 17px은 Tailwind 눈금(16/18) 밖의 예외.

## 2026-10-21 — md 저장 위치를 Google Drive 기본으로 변경

- 새 모듈 `scripts/storage.py`: 원문·요약 md 경로를 한곳에서 관리. `config/config.yaml`에 `storage.drive_dir`(`G:/내 드라이브/SemiAnalysis`) 추가(환경변수 `SEMIANALYSIS_DRIVE_DIR`가 우선, 빈 값이면 Drive 미사용). `monitor.py`의 설정 파서는 모르는 섹션을 무시하므로 영향 없음.
- 저장은 Drive(원문: 연도 폴더, 요약: 연도 폴더 + `00_핵심요약_전체`)와 로컬 양쪽에 쓰고, 읽기는 Drive+로컬을 합쳐 본다. Drive가 연결돼 있지 않으면 로컬에만 저장하고 경고한다. 기존 파일은 덮어쓰지 않고, 로컬 `output/semianalysis/`는 유지(삭제 없음).
- 수정한 스크립트: `build_data.py`(사이트 빌드의 요약 병합, 결과 314/341건 동일), `fetch_archive_md.py`(`--out` 미지정 시 Drive+로컬), `batch_summarize.py`, `url_monitor_pipeline.py`(Drive 복사를 storage로 교체, 하드코딩 경로 제거), `build_catalog.py`, `compare_summaries.py`.
- `sync_to_gdrive.py`를 양방향(추가만) 동기화 도구로 재작성. 실행 결과 로컬→Drive 원문 3개·요약 4개를 복사해 Drive와 로컬이 일치(원문 315, 요약 314).
- 알아둘 점: md 수집(`fetch_archive_md.py`)과 Gemini 요약은 `run_daily.py`에 없는 수동 스크립트다. 아직 md가 없는 글은 4건(2026-10-19 Datacenters 2027, 2026-10-15 ASML, 2026-05-26 800VDC Part 1, 2026-05-21 EDA Market Primer, 모두 유료 프리뷰)이며 수집하려면 Firecrawl 크레딧이 든다.

## 2026-10-21 — 일일 실행에 원문 md 수집 추가, 원문 전용 Drive 폴더 지원

- `run_daily.py`: `수집` 다음에 `원문 md 수집`(`scripts/fetch_archive_md.py --days 14`) 단계 추가. 최근 14일 글 중 아직 없는 글만 Firecrawl로 스크랩(글당 약 1크레딧, 첫 실행은 10-19·10-15 2건). 이 단계는 실패(키 없음·크레딧 소진·네트워크)해도 이후 단계(요약·사이트 빌드·메일)를 막지 않는다(STEPS 3번째 값 `True`). `fetch_archive_md.py`에 `--days N` 옵션 추가.
- `storage.raw_dir`(config) / 환경변수 `SEMIANALYSIS_RAW_DIR`: 새 원문 md를 `<폴더>/YYYY-MM-DD_제목.md`로 직접 저장할 폴더. 비었거나 연결 안 됐으면 Drive 연도 폴더로 폴백(경고). 로컬 사본은 항상 유지. 원문 전용 폴더의 `NNN_...` 기존 파일은 원문으로 읽지 않음(`RAW_NAME_RE`).
- 대상 폴더: 테스트 계정 Drive의 `Semianalysis원본/semianalysis_markdown_archive_with_dates/semianalysis_markdown_archive`(ID `10p1yHS5…`). PC의 G:는 다른 계정이라 보이지 않아, 공유 + '내 드라이브에 바로가기 추가' 후 G:\ 경로를 `raw_dir`에 적어야 활성화된다→ 2026-10-21 `raw_dir`에 `G:/.shortcut-targets-by-id/<ID>/Semianalysis원본/.../semianalysis_markdown_archive` 경로를 설정, 읽기·쓰기 시험 통과(기존 `NNN_…` 파일 345개는 원문으로 세지 않음).
- `sync_to_gdrive.py`: 원문 전용 폴더나 연도 폴더 어디에든 있는 원문은 다시 복사하지 않음.

## 2026-10-21 — 원문 md 파일명을 `NNN_YYYY-MM-DD_slug.md`로 변경

- 요청: 크롤링 원문 md를 `343_2023-10-01_accelerator-model` 형태로 저장. 원문 전용 폴더에는 이미 `001`~`343`번 카탈로그 파일과 `README.md`(색인)가 있어 그 번호를 이어 `344`번부터 부여.
- `fetch_archive_md.py`: 이름을 `NNN_날짜_slug.md`(slug는 Substack slug)로 저장, 오래된 글부터 번호 부여. 중복 판단은 옛 이름 또는 날짜+slug로 해 이미 받은 글은 다시 받지 않음. `--out` 직접 지정 시에는 옛 이름 유지.
- `storage.py`: `next_raw_number`, `raw_key_exists` 추가, `year_of`가 `NNN_` 접두가 있어도 연도를 읽도록 수정(이전 방식이면 `기타` 폴더로 갔을 것). 원문 전용 폴더는 입력으로 읽지 않도록 변경(기존 카탈로그 파일과 이름이 같아 구분 불가, 로컬 사본이 입력). `build_data.py`·`build_catalog.py`는 `NNN_` 접두를 허용하도록 정규식 수정.
- 먼저 옛 이름으로 저장했던 2건은 크레딧 재사용 없이 이름만 변경: `344_2026-10-15_asml-is-building-a-hybrid-bonding.md`, `345_2026-10-19_datacenters-2027-the-year-of-acceleration.md` (로컬+Drive). 재수집 대상 0건, 다음 번호 346 확인.
- `run_daily.py` 끝에 `Drive 동기화` 단계(실패해도 계속) 추가, `sync_to_gdrive.py`가 로컬에만 있는 원문을 원문 전용 폴더로 보충하도록 수정(Drive 연결 전에 실행된 날 보충용).
- 알려진 한계: 기존 번호는 카탈로그 순서(대체로 최신순)이고 새 번호는 수집 순서로 이어지므로 번호와 날짜 순서가 일치하지 않음. 폴더의 `README.md` 색인은 갱신하지 않음.

## 2026-10-21 — 요약 없는 7건 채움, 원문 목록 누락 버그 수정

- 요약이 없던 7건(Datacenters 2027, ASML, LEGO Datacenters, Anthropic Growth/Bedrock, 800VDC Part 1, EDA Market Primer, Cerebras)의 한국어 요약을 `semianalysis_summary_prompt.md`의 10개 섹션 형식으로 작성해 Drive(연도 폴더 + `00_핵심요약_전체`)와 로컬 `summary/`에 저장. 사이트 빌드 결과 요약 병합 321/341건, 요약 없는 글 0건.
- 자동 경로는 모두 막혀 있어 직접 작성: Gemini는 `429 RESOURCE_EXHAUSTED`(프로젝트 월 지출 한도 초과, 분당 한도가 아님), Anthropic 키는 `.env`에 없음. 수집 범위가 유료 미리보기인 글은 paywall 이후를 추측하지 않고 "누락"으로 명시, 원문 내 수치 불일치는 한계 섹션에 기록.
- 원문 md 4건 추가 수집(`349`~`352`, Firecrawl 4크레딧): eda-market-primer, inside-the-800vdc-revolution-part, anthropic-growth-and-bedrock-mix, the-wild-wild-west-of-lego-datacenters.
- 버그 수정 `fetch_archive_md.py`: archive API는 한 번에 최대 25건만 돌려주는데 `PAGE = 50`으로 offset을 50씩 건너뛰어 목록 341건 중 314건만 읽혔다(25건씩 누락). `PAGE = 25`로 수정해 339건을 읽음. 이 누락이 "원문 md 없는 27건"의 원인이었음. `--slug a,b` 옵션 추가.
- 버그 수정 `storage.raw_key_exists`: 원문 전용 폴더의 기존 카탈로그 메모 파일(약 2KB, 같은 `NNN_날짜_slug` 형식)을 "이미 받은 원문"으로 세어 받아야 할 글을 건너뛰었다. 4,000바이트 이하 파일은 원문으로 인정하지 않도록 수정(`STUB_MAX_BYTES`). 수정 후 아직 원문이 없는 글은 약 22건(9월 28일 이후 글은 모두 있음).
- 알려진 문제(미수정): 사이트 데이터의 `published_at`이 아카이브 API의 `post_date`와 다른 글이 있다. 예: `kimi-k3-the-manos-the-mythos-the`는 실제 발행일 2026-08-03인데 사이트에는 수집일 2026-09-28로 표시. 아카이브 목록에 없던 27건이 수집일로 대체된 것으로 보이며, 날짜 정렬·NEW 배지·연도 필터에 영향을 준다.

## 2026-10-27 — 원문 md 파일명의 번호 접두어(`NNN_`) 제거

- 원문 전용 Drive 폴더의 353개 파일에서 `NNN_`을 지워 `YYYY-MM-DD_slug.md`로 통일(350+1건 변경, 로컬 `output/semianalysis/` 사본 8건도 동일). 이전 이름→새 이름 매핑은 작업 중 임시 파일로만 남겼다.
- 이름이 겹치는 3쌍은 작은 카탈로그 메모(약 2KB)를 삭제하고 원문만 남김: `048_eda-market-primer`, `025_the-wild-wild-west-of-lego-datacenters`, `001_sparse-savings-persistent-demand-inside-glm53`(원문은 `349`/`352`/`346`이었던 파일).
- `storage.py`: `NUMBERED_RE`→`DATED_RE`(접두어 선택적), `next_raw_number` 제거. `fetch_archive_md.py`: 새 이름을 `<날짜>_<slug>.md`로 저장. `CLAUDE.md` 저장 규칙 갱신.
- 폴더의 `README.md` 색인은 옛 이름을 가리킬 수 있으며 갱신하지 않음.

- 날짜 보정: 아카이브 API(341건)와 사이트 데이터(343건)를 `slug`로 비교해 발행일이 어긋난 `kimi-k3-the-manos-the-mythos-the` 1건의 `published_at`을 수집일(2026-09-28)에서 실제 발행일(2026-08-03)로 고침(`output/results.json`, `data/items/2026-09.jsonl`). 이후 `scripts/build_dashboard.py`·`build_site.py`로 재빌드. 아카이브 목록에 없는 `are-open-models-catching-up`, `cerebrass-next-generation-cs-4-fast`는 비교하지 못했다.

## 2026-10-27 — 원문 md 22건 추가 수집

- 원문이 없던 22건(2026-05-28~08-07, 유료 21건은 프리뷰만)을 `fetch_archive_md.py`로 수집해 Drive 원문 전용 폴더와 로컬에 저장. 첫 실행에서 10건 성공 후 Firecrawl `429`로 12건 실패 → `--delay 8`로 재실행해 12건 성공. Firecrawl 잔여 크레딧 1,101(청구 주기 갱신분 포함).
- 알려진 한계: `2026-07-08_anthropic-3q26-profit-over-1b-the.md`는 프리뷰가 3,656바이트로 `STUB_MAX_BYTES`(4,000) 미만이라 매번 "새로 저장할 글"로 잡힌다. 실행해도 같은 내용이 다시 저장되며 크레딧 1개가 든다.
- 요약(`summary/`)은 아직 만들지 않았다(Gemini 한도·Anthropic 키 문제 그대로).

## 2026-10-27 — 폴더 정리 (참조 없는 파일 삭제)

- 삭제(A): 빈 파일 `새 텍스트 문서.txt`, `__pycache__/`, `scripts/check_posts.py`, `skills-lock.json`, 10-09~10-20 일일 로그 6개.
- 삭제(B): `data/substack_archive.json`, `output/reports_detailed.json`, `DESIGN.md` (코드 참조 없음).
- 삭제(C): 구 다중 페이지 사이트 `topic.html`, `entity.html`, `post.html`, `detail.html`(루트·`docs/`)와 이를 위한 `assets/app.js`·`app.css`(루트·`docs/assets/`). 현재 `index.html`은 이들을 링크하거나 불러오지 않는다.
- `build_site.py`의 복사 대상을 `index.html`만으로 줄였고, `README.md`에서 `detail.html` 언급을 제거했으며, `SITE_SPEC.md` 맨 위에 계획 폐기 안내를 추가했다(본문은 참고용으로 유지).
- 복구: 추적 파일은 `git`에서, 로그 등은 scratchpad의 `backup_AB/`에 사본이 있다(임시 폴더).
- 손대지 않음: `output/semianalysis/`, `assets/summaries/`·`docs/summaries/`(재생성 가능한 비공개 사본), 수동 도구 스크립트.

## 2026-10-27 — 공개 저장소 준비

- 메일 수신자를 `.env`의 `EMAIL_RECIPIENT`로 옮김(`send_email.py`의 `get_env_value`, `.env.example` 갱신). `config/email.yml`은 자리표시자(`you@example.com`)이며, 수신자가 없거나 자리표시자면 오류로 종료한다. 코드에 하드코딩돼 있던 기본 주소도 제거.
- `README.md`·`change.md`·`config/config.yaml`에 적힌 개인 이메일을 `test@example.com`/`owner@example.com`으로 치환(과거 커밋 기록에는 남아 있음).
- 가이드 문서 3개를 `docs/`(공개 Pages 폴더) 밖 `notes/`로 이동.
- 유료 글 요약(약 470자)은 현재대로 사이트 데이터에 포함해 공개하기로 결정.
