# CLAUDE.md

SemiAnalysis(Substack) 새 글을 매일 수집·요약해 메일로 보내고, 정적 대시보드(`index.html`)로 보여주는 프로젝트.
자세한 배경은 `README.md`, 사이트 명세는 `SITE_SPEC.md`, 변경 이력은 `change.md`.

## 파이프라인 (`run_daily.py`, 매일 자동 실행)
`monitor.py`(수집) → `scripts/fetch_archive_md.py --days 14`(원문 md 수집, Firecrawl 글당 약 1크레딧, **실패해도 계속**) → `scripts/backfill_summaries.py`(요약 보강) → `scripts/dedup.py` → `scripts/build_dashboard.py` → `build_site.py`(사이트 빌드) → `scripts/pending.py` → `scripts/send_email.py`(Resend 발송)

**`run_daily.py`는 인자를 처리하지 않는다.** `--help`를 붙여도 메일 발송까지 전체가 실행되므로 테스트 용도로 실행하지 말 것. 확인은 개별 스크립트나 문법 검사(`python -c "import ast; ast.parse(open(f, encoding='utf-8').read())"`)로 한다.

## 사이트
- **루트 `index.html`이 원본**이다. 수정 후 `python build_site.py`로 `assets/data.js`를 재생성하고 HTML·`assets/`를 `docs/`(GitHub Pages)로 복사한다. `docs/` 안의 파일은 직접 수정하지 않는다(덮어써짐).
- `index.html`은 `<script src="assets/data.js">`의 `window.POSTS`를 읽는다. 로드 실패 시에만 파일 안의 `STATIC_SOURCE_LINES`로 폴백한다.
- 화면은 Full HD(1920×1080) 3분할 게시판이다: 좌측 사이드바(카테고리·소분류·연도) / 중앙 목록 / 우측 상세 패널(모달 없음). 기본 Light 테마이고 `html.dark` 클래스로 다크를 전환한다.
- 화면에는 파이프라인 실데이터만 표시한다: `title`/`title_ko`, `summary`, `published_at`, `paywalled`, 큐레이션된 기사(`enrich.json` 기반)의 `points`/`takeaway`/`entities`. 근거 없는 문장·수치를 만들어 넣지 않는다.
- 카테고리: 파이프라인 `category` 코드가 있으면 우선 사용(`CATEGORY_CODE_MAP`), 없으면 `CATEGORY_RULES` 키워드 규칙, 그래도 없으면 `SUBS`(소분류 키워드)로 대분류를 찾고, 끝까지 안 걸리면 `기타`로 둔다. 소분류는 `detectSub`가 `SUBS`로 판정한다.
- 폰트: 전체 Pretendard Variable 단일(jsDelivr CDN), 자간 전역 -0.02em. 글자 크기는 Tailwind 표준 눈금(12~30px) 안에서 쓰며, 사이드바 카테고리 17px만 예외다.
- 확인은 브라우저에서 직접 연다. `file://`은 도구가 막으므로 `python -m http.server 8731 --bind 127.0.0.1`을 띄워 `http://127.0.0.1:8731/index.html`로 보고, 끝나면 탭과 서버를 종료한다(Windows는 `Get-NetTCPConnection` → `Stop-Process`).

## md 저장소 (Drive)
- 원문·요약 md의 기본 저장소는 Google Drive(`config/config.yaml`의 `storage.drive_dir`)이고, 로컬 `output/semianalysis/`(원문)·`summary/`(요약)는 사본으로 **지우지 않고 유지**한다. 경로 규칙은 `scripts/storage.py` 한곳에서 관리한다.
- Drive 구조: 원문 `<Drive>/<연도>/`, 요약 `<Drive>/<연도>/` + `<Drive>/00_핵심요약_전체/`. 저장은 Drive+로컬 양쪽, 읽기는 둘을 합쳐서(같은 이름이면 Drive 우선) 본다. Drive가 연결돼 있지 않으면 로컬만 쓰고 경고한다(실행은 멈추지 않는다).
- 쓰는 곳은 `fetch_archive_md.py`(원문), `batch_summarize.py`·`url_monitor_pipeline.py`(원문·요약)이고, 읽는 곳은 `build_data.py`(사이트 빌드)·`build_catalog.py`·`compare_summaries.py`다. 경로를 새로 하드코딩하지 말고 `storage`를 쓴다.
- 새 원문 md는 `storage.raw_dir`(원문 전용 Drive 폴더, 평평하게)에 `YYYY-MM-DD_slug.md`(예: `2026-10-15_asml-is-building-a-hybrid-bonding.md`)로 저장하고, 비어 있거나 연결 안 됐으면 위 Drive 연도 폴더로 폴백한다(로컬 사본은 항상 유지). 번호 접두어(`NNN_`)는 2026-10-27에 모두 제거했고 쓰지 않는다(옛 접두어 이름도 같은 글로 인식). 이미 저장한 글은 날짜+slug(`raw_key_exists`) 또는 옛 이름(`YYYY-MM-DD_제목.md`)으로 판별해 다시 받지 않는다.
- 원문 전용 폴더에는 같은 이름 형식의 기존 카탈로그 메모 파일(약 2KB)이 있어 이름으로 원문과 구분되지 않는다. 그래서 이 폴더는 저장 대상으로만 쓰고, 원문 입력(`storage.raw_files`)은 로컬 사본을 읽는다. 폴더의 `README.md` 색인은 자동 갱신되지 않는다.
- 한쪽에만 있는 파일은 `python scripts/sync_to_gdrive.py [--dry-run]`로 맞춘다(추가만 하고 지우거나 덮어쓰지 않는다). `run_daily.py`는 원문 md 수집만 하고 Gemini 요약(`batch_summarize.py`)·동기화는 수동이다.

## 파일·보안 규칙
- 비밀값(`.env`, `mcp_config.json`, `.agents/mcp_config.json`)은 `.gitignore`로 제외되어 있다. 커밋 전 키가 섞이지 않았는지 확인한다. `.mcp.json`은 `${변수}` 참조만 사용한다.
- `config/email.yml`에는 메일 수신자(테스트 주소)가 있다. 공개 저장소로 푸시하기 전에 반드시 점검한다.
- 큰 파일을 스크립트로 일괄 치환할 때는 마커 검색이 파일 앞쪽의 동일 문자열을 잡지 않는지 확인하고(검색 시작 위치 지정), 먼저 커밋·백업해 둔다.

## 환경
- Windows 11, 셸은 PowerShell/Git Bash. 한글 경로(`C:\Users\<사용자명>`)가 있어 Python 파일 입출력은 `encoding='utf-8'`을 명시한다.
- 답변은 한국어로 하고, 코드·경로는 원문을 유지한다.
