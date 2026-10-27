# URL 자동 모니터링 및 AI 요약 파이프라인 가이드

본 문서는 **Firecrawl**, **Google Drive**, **Google Gemini API**를 결합하여 웹사이트 업데이트를 매일 자동으로 감지하고, 원문 아카이빙 및 전문가 수준의 요약 노트를 생성·저장하는 엔드투엔드(End-to-End) 자동화 시스템 구축 가이드입니다.

---

## 1. 파이프라인 전체 구조 (Architecture)

```
[ 일일 스케줄러 (Windows 작업 스케줄러 / Cron / GitHub Actions) ]
                             │
                             ▼
 1. Firecrawl Web Scraper
    - 대상 사이트(뉴스레터, 기술 블로그 등) URL 변경 감지 및 본문 Markdown 추출
                             │
                             ▼
 2. 신규 여부 검증 (Change Detection)
    - 로컬/드라이브 내 기존 파일 및 이력 DB(`data/history.json`) 대조
    - 이미 처리된 URL은 즉시 Skip
                             │
                             ▼
 3. 원문 아카이빙 (Google Drive)
    - 원문 전체 내용(Frontmatter 포함)을 `.md` 파일로 저장
    - 경로: `Google Drive/URL_Monitor/{날짜}_{제목}.md`
                             │
                             ▼
 4. Gemini 고품질 요약 생성
    - `semianalysis_summary_prompt.md` 템플릿 기반 실무자 읽기 노트 작성
    - 10대 핵심 섹션(Executive Summary, Key Insights, 수치 표, 기술 분석 등)
                             │
                             ▼
 5. 요약 파일 저장 (Google Drive /summary)
    - 경로: `Google Drive/URL_Monitor/summary/{날짜}_{제목}_summary.md`
```

---

## 2. 장점 및 타당성 분석

| 단계 | 채택 기술 | 타당성 및 장점 | 주의점 및 권장 사항 |
|---|---|---|---|
| **수집** | Firecrawl API | 동적 JS 렌더링을 완벽히 파싱하며, LLM 친화적인 Clean Markdown으로 즉시 변환 | 무료/유료 크레딧 소모율 관리 필요 |
| **저장** | Google Drive (.md) | 모바일/PC 어디서나 열람 가능하며, 마크다운 원본 서식 100% 보존 | GDoc 변환 대신 순수 `.md` 텍스트 파일로 저장 권장 |
| **요약** | Gemini 3.8 Flash | 대용량 컨텍스트 처리(1M+ 토큰) 지원 및 빠른 속도, 높은 가성비/무료 티어 | Thinking Budget 설정을 통해 사실 왜곡 억제 |
| **관리** | 1:1 파일 매칭 | 원문과 요약본이 나란히 보관되어 향후 RAG 또는 지식 검색에 최적 | 파일명 표준화(`{YYYY-MM-DD}_{Slug}_summary.md`) |

---

## 3. Google Drive 연동 방식 2가지

### 방식 A: Google Drive 데스크톱 앱 활용 (가장 추천 ⭐)
- **원리**: PC에 구글 드라이브 데스크톱 앱을 설치하면 `G:\내 드라이브\` 또는 가상 드라이브 경로로 연결됨.
- **장점**: 별도의 복잡한 Google Cloud OAuth/Service Account 설정 없이, 일반 로컬 폴더 경로 지정만으로 클라우드 자동 동기화.
- **설정법**: 환경변수나 스크립트의 `OUTPUT_BASE_DIR`을 드라이브 경로로 지정 (예: `G:/내 드라이브/AI_Monitor`).

### 방식 B: Google Drive API (Service Account) 활용
- **원리**: 서버나 클라우드(GitHub Actions, AWS)에서 PC 없이 24시간 가동할 때 사용.
- **장점**: 로컬 PC가 꺼져 있어도 클라우드 상에서 독립 실행 가능.
- **필요 요소**: Google Cloud Console에서 Service Account 발급 후 `credentials.json` 연동.

---

## 4. 환경 변수 설정 (`.env`)

프로젝트 루트 폴더의 `.env` 파일에 다음 인증 키들이 등록되어 있어야 합니다:

```env
# Firecrawl API Key (https://firecrawl.dev)
FIRECRAWL_API_KEY="fc-xxxxxxxxxxxxxxxx"

# Google Gemini API Key (https://aistudio.google.com)
GEMINI_API_KEY="AQ.xxxxxxxxxxxxxxxx"

# (선택) Google Drive 저장 경로 (미지정 시 프로젝트 내 output 폴더 사용)
# GOOGLE_DRIVE_DIR="G:/내 드라이브/URL_Monitor"
```

---

## 5. 실행 및 자동화 방법

### 수동 실행
```bash
python scripts/url_monitor_pipeline.py
```

### Windows 작업 스케줄러 자동화 (매일 아침 자동 실행)
1. `시작` 메뉴에서 **작업 스케줄러(Task Scheduler)** 실행
2. **기본 작업 만들기** 클릭 ➔ 이름: `URL Monitor Daily Pipeline`
3. 트리거: **매일(Daily)**, 시간: **오전 07:00**
4. 작업: **프로그램 시작**
   - 프로그램/스크립트: `python.exe` 전체 경로
   - 인수 추가: `C:\practice\Website_agent\url-monitor\scripts\url_monitor_pipeline.py`
   - 시작 위치: `C:\practice\Website_agent\url-monitor`
