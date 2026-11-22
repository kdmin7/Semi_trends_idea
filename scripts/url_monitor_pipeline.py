#!/usr/bin/env python3
"""
scripts/url_monitor_pipeline.py
================================
SemiAnalysis 신규 아티클 실시간 모니터링 & 자동화 파이프라인.

기능:
1. 최신 게시물 링크 탐색: Substack 아카이브 API (실패 시 Firecrawl v2 map 대체)
   (--check-latest 또는 인자로 URL 직접 지정)
2. 신규 판정: pipeline_history.json + 카탈로그(docs/index.json)·발송 기록·대기열의 slug 대조
3. Firecrawl v2 scrape로 원문 마크다운 수집 (로컬에 같은 글 원문이 있으면 재사용, 크레딧 절약)
4. Gemini 요약 (모델·설정·프롬프트는 summary_prompt.py, 규칙은 semianalysis_summary_prompt.md)
5. 원문·요약을 로컬 output/semianalysis/ 에 저장하고 Drive로 복사 (경로 규칙은 storage.py)
6. 이메일 발송 대기열(docs/pending.json)·output/results.json 갱신
7. 모든 글 처리 후 카탈로그(docs/index.json)·사이트(build_site.py) 한 번 재빌드
"""

import sys
import re
import json
import time
import pathlib
import subprocess
import urllib.request
from datetime import datetime, timezone
from google import genai
from google.genai import types

import storage  # 원문·요약 저장 위치(Drive + 로컬) 한곳 관리
import summary_prompt  # 요약 프롬프트 로딩·수집 범위 판정

# 윈도우 UTF-8 입출력 보정
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
PROMPT_FILE = BASE_DIR / "semianalysis_summary_prompt.md"
HISTORY_FILE = BASE_DIR / "data" / "pipeline_history.json"
PENDING_FILE = BASE_DIR / "docs" / "pending.json"

RAW_DIR = BASE_DIR / "output" / "semianalysis"
SUMMARY_DIR = RAW_DIR / "summary"

def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def sanitize_filename(name: str) -> str:
    # 윈도우 파일명 금지 문자 및 공백 정리
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", "_", name)
    return name[:120]

def load_history() -> set:
    if HISTORY_FILE.exists():
        try:
            return set(json.loads(HISTORY_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()

def save_history(history_set: set):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(sorted(list(history_set)), ensure_ascii=False, indent=2), encoding="utf-8")

SENT_FILE = BASE_DIR / "data" / "state" / "sent.json"
CATALOG_FILE = BASE_DIR / "docs" / "index.json"


# slug -> Substack 아카이브 API 의 post_date (fetch_latest_links 가 채운다)
ARCHIVE_POST_DATES: dict = {}


def slug_of(url: str) -> str:
    """글 URL에서 slug(/p/ 뒤 경로)만 뽑는다. 끝 슬래시·쿼리·대소문자 차이를 무시하기 위함."""
    path = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return path.rsplit("/p/", 1)[-1].lower() if "/p/" in path else path.lower()


def load_known_slugs(history: set) -> set:
    """이미 수집·요약·발송된 글의 slug 집합.

    pipeline_history.json 은 이 파이프라인이 직접 처리한 URL만 담고 있어서, fetch_archive_md.py 등
    다른 경로로 받은 옛 글이 '신규'로 다시 처리되는 문제가 있었다. 카탈로그·발송 기록·대기열도 함께 본다.
    """
    urls = set(history)
    for path, key in ((SENT_FILE, None), (PENDING_FILE, "url"), (CATALOG_FILE, "source_url")):
        try:
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data:
                u = item if key is None else (item.get(key) if isinstance(item, dict) else None)
                if isinstance(u, str) and u:
                    urls.add(u)
        except Exception as e:
            print(f"[!] 기존 글 목록 로드 실패 ({path.name}): {e}", file=sys.stderr)
    return {slug_of(u) for u in urls}


def firecrawl_map(site_url: str, api_key: str, limit: int = 25) -> list[str]:
    """Firecrawl Map API를 호출하여 사이트의 최신 링크 목록을 반환합니다."""
    endpoint = "https://api.firecrawl.dev/v2/map"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"url": site_url, "limit": limit}
    req = urllib.request.Request(endpoint, headers=headers, data=json.dumps(payload).encode("utf-8"))
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # v2 는 links 가 {url, title, description} 객체 목록이다 (v1 은 문자열)
            links = [l["url"] if isinstance(l, dict) else l for l in data.get("links", [])]
            # /p/ 가 포함된 아티클 링크만 필터링
            article_links = [l for l in links if "/p/" in l and not l.endswith("/comments")]
            return article_links
    except Exception as e:
        print(f"[!] Firecrawl map error: {e}", file=sys.stderr)
        return []

def fetch_latest_links(site_url: str, firecrawl_key: str, limit: int = 25) -> list[str]:
    """Substack 공식 아카이브 API(시간순 정렬)와 Firecrawl map을 결합하여 실제 최신 발행 링크를 반환합니다."""
    links = []
    # 1. Substack 공식 아카이브 API (시간순 최신 글 100% 보장)
    try:
        archive_url = f"{site_url.rstrip('/')}/api/v1/archive?sort=new&limit={limit}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        req = urllib.request.Request(archive_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # 응답 형태가 {"posts": [...]} 또는 [...] 둘 다 올 수 있다
            posts = data.get("posts", []) if isinstance(data, dict) else data
            for item in posts:
                link = item.get("canonical_url") or (
                    f"{site_url.rstrip('/')}/p/{item['slug']}" if item.get("slug") else None
                )
                if not link:
                    continue
                links.append(link)
                if item.get("post_date"):
                    # 실제 발행일 — Firecrawl 메타데이터에 publishedTime 이 없을 때 파일명 날짜로 쓴다
                    ARCHIVE_POST_DATES[slug_of(link)] = item["post_date"]
    except Exception as e:
        print(f"[!] Substack archive API check failed: {e}", file=sys.stderr)

    # 2. Firecrawl Map — 아카이브 API 가 실패했을 때만 쓰는 대체 수단.
    #    map 은 날짜순이 아니라 임의 순서의 링크를 주므로, 정상일 때 섞으면 옛 글이 '신규'로 잡힌다.
    if not links:
        print("[!] Substack 아카이브 API 결과가 없어 Firecrawl map 으로 대체합니다.", file=sys.stderr)
        for l in firecrawl_map(site_url, firecrawl_key, limit=limit):
            if l not in links:
                links.append(l)

    return links

def firecrawl_scrape(url: str, api_key: str):
    """Firecrawl Scrape API를 호출하여 대상 URL의 마크다운 본문을 추출합니다."""
    endpoint = "https://api.firecrawl.dev/v2/scrape"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"url": url, "formats": ["markdown"]}
    req = urllib.request.Request(endpoint, headers=headers, data=json.dumps(payload).encode("utf-8"))
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            data_obj = data.get("data", {})
            markdown = data_obj.get("markdown", "")
            metadata = data_obj.get("metadata", {})
            return markdown, metadata
    except Exception as e:
        print(f"[!] Firecrawl scrape error for {url}: {e}", file=sys.stderr)
        return None, None

def find_existing_raw(target_url: str):
    """같은 글(slug 기준)의 원문 md가 로컬에 이미 있으면 (경로, 내용)을 반환. 없으면 (None, None).

    요약 실패로 history에 등록되지 않은 글을 다음 실행에서 다시 스크랩하지 않기 위함.
    """
    want = slug_of(target_url)
    if not RAW_DIR.exists():
        return None, None
    for path in RAW_DIR.glob("*.md"):
        try:
            with path.open(encoding="utf-8") as f:
                head = f.read(1000)
        except Exception:
            continue
        m = re.search(r"^source:\s*(\S+)", head, re.M)
        if m and slug_of(m.group(1)) == want:
            return path, path.read_text(encoding="utf-8")
    return None, None


def generate_summary(client, original_content: str, filename: str, system_prompt: str) -> str:
    """Gemini로 요약 노트를 작성합니다 (모델 순서·설정은 summary_prompt.py)."""
    config = summary_prompt.make_config(system_prompt)
    user_prompt = summary_prompt.build_user_prompt(filename, original_content)
    max_attempts = 3  # 모델별 시도 횟수 (일시적 503/504 흡수)
    for model_name in summary_prompt.MODELS:
        for attempt in range(1, max_attempts + 1):
            try:
                res = client.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=config,
                )
                text = (res.text or "").strip()
                if text:
                    return text
                print(f"    [!] {model_name}: 빈 응답 ({attempt}/{max_attempts})", file=sys.stderr)
            except Exception as e:
                print(f"    [!] Summary generation error with {model_name} ({attempt}/{max_attempts}): {e}", file=sys.stderr)
            if attempt < max_attempts:
                time.sleep(5 * attempt)  # 5초, 10초 점증 대기
    return ""

def sync_to_google_drive(raw_path: pathlib.Path, summary_path: pathlib.Path, year_str: str):
    """로컬에 쓴 원문·요약을 Drive(config.yaml storage.drive_dir)로 즉시 복사 (구조는 scripts/storage.py 참조)"""
    if storage.drive_dir() is None:
        print("    [-] Google Drive 경로가 연결되지 않아 로컬 저장을 유지합니다. (나중에 scripts/sync_to_gdrive.py 로 맞춥니다)")
        return

    try:
        storage.mirror_file(raw_path, "raw")
        storage.mirror_file(summary_path, "summary")
        print(f"    [V] Google Drive 동기화 완료: {year_str}/{summary_path.name}")
    except Exception as e:
        print(f"    [!] Google Drive sync error: {e}", file=sys.stderr)

def trigger_catalog_rebuild():
    """웹 대시보드 색인 카탈로그(docs/index.json) 및 index.html용 data.js 자동 갱신"""
    try:
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
        from scripts.build_catalog import build_catalog
        print("    [*] 웹 대시보드 색인 카탈로그(docs/index.json) 자동 갱신 중...")
        build_catalog()

        # build_site.py 실행하여 index.html 및 assets/data.js 동기화
        build_site_script = BASE_DIR / "build_site.py"
        if build_site_script.exists():
            subprocess.run([sys.executable, str(build_site_script)], cwd=str(BASE_DIR), check=True)
            print("    [*] index.html 및 assets/data.js 동기화 완료.")
    except Exception as e:
        print(f"    [!] Catalog/Site rebuild error: {e}", file=sys.stderr)

def append_to_pending(title: str, summary: str, url: str):
    """이메일 발송 대기열(docs/pending.json)에 신규 항목 추가"""
    try:
        pending = []
        if PENDING_FILE.exists():
            try:
                pending = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
            except Exception:
                pending = []
        
        pending.append({
            "title": title,
            "summary": summary[:300],
            "url": url,
            "source": "https://newsletter.semianalysis.com",
            "detected_at": datetime.now(timezone.utc).isoformat()
        })
        PENDING_FILE.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    [V] 이메일 발송 대기열(docs/pending.json) 등록 완료")
    except Exception as e:
        print(f"    [!] Pending queue error: {e}", file=sys.stderr)

def update_results_json(title: str, summary: str, url: str, slug: str, pub_time: str, paywalled: bool):
    """output/results.json에 신규 아티클 정보를 등록하여 웹 대시보드(index.html)에서 즉시 반영되도록 연동"""
    results_file = BASE_DIR / "output" / "results.json"
    results = []
    if results_file.exists():
        try:
            results = json.loads(results_file.read_text(encoding="utf-8"))
        except Exception:
            results = []

    # slug 또는 url 기준 기존 항목 검색
    existing = next((r for r in results if slug_of(r.get("slug") or r.get("url") or "") == slug or r.get("url") == url), None)

    if existing:
        existing["title"] = title
        if summary and (not existing.get("summary") or len(summary) > len(existing.get("summary", ""))):
            existing["summary"] = summary[:300]
        existing["paywalled"] = paywalled
        if pub_time and not existing.get("published_at"):
            existing["published_at"] = pub_time
    else:
        new_entry = {
            "title": title,
            "summary": summary[:300] if summary else "",
            "url": url,
            "source": "https://newsletter.semianalysis.com",
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "slug": slug,
            "paywalled": paywalled,
            "published_at": pub_time
        }
        results.insert(0, new_entry)

    results_file.parent.mkdir(parents=True, exist_ok=True)
    results_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    [V] 웹 데이터 원본(output/results.json) 등록/갱신 완료: {slug}")

def process_target_url(target_url: str, firecrawl_key: str, gemini_client, system_prompt: str, history: set) -> bool:
    """단일 신규 URL 파이프라인 수행"""
    if target_url in history:
        print(f"[-] 이미 처리된 URL: {target_url} (Skip)")
        return True

    print(f"\n========================================================")
    print(f"[*] 신규 아티클 감지: {target_url}")
    print(f"========================================================")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    slug = slug_of(target_url)

    # 1. 원문 확보: 이전 실행에서 저장한 원문이 있으면 재사용(요약만 실패했던 경우), 없으면 Firecrawl 스크랩
    raw_path, raw_content = find_existing_raw(target_url)
    if raw_path is not None:
        fm = summary_prompt.frontmatter(raw_content)
        title = fm.get("title") or "Untitled"
        pub_time = ARCHIVE_POST_DATES.get(slug) or fm.get("date") or raw_path.name[:10]
        raw_filename = raw_path.name
        print(f"    1~2. 기존 원문 재사용 (스크랩 생략): {raw_filename}")
    else:
        print("    1. Firecrawl로 원문 스크랩 중...")
        markdown, metadata = firecrawl_scrape(target_url, firecrawl_key)
        if not markdown:
            print(f"    [X] 원문 스크랩 실패: {target_url}")
            return False

        metadata = metadata or {}
        title = metadata.get("title") or "Untitled"
        # 발행일 우선순위: Substack 아카이브 post_date > Firecrawl publishedTime > 오늘(최후 수단)
        pub_time = (
            ARCHIVE_POST_DATES.get(slug)
            or metadata.get("publishedTime")
            or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        date_str = pub_time[:10] if len(pub_time) >= 10 else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        raw_filename = f"{date_str}_{sanitize_filename(title)}.md"
        raw_path = RAW_DIR / raw_filename

        # 수집 범위 판정용: 메타데이터에 audience가 있으면 frontmatter에 남긴다 (없으면 paywall 문구로 판정)
        audience = metadata.get("audience")
        audience_line = f"\naudience: {audience}" if isinstance(audience, str) and audience else ""

        frontmatter = f"""---
title: "{title}"
date: {date_str}
source: {target_url}
scraped_at: {datetime.now(timezone.utc).isoformat()}{audience_line}
---

"""
        raw_content = frontmatter + markdown
        raw_path.write_text(raw_content, encoding="utf-8")
        print(f"    2. 원문 마크다운 저장 완료: {raw_filename} ({len(raw_content):,} bytes)")

    year_str = raw_filename[:4]
    summary_filename = f"{raw_path.stem}_summary.md"
    summary_path = SUMMARY_DIR / summary_filename

    # 3. Gemini 요약 생성
    print("    3. Gemini 심층 요약 노트 작성 중...")
    summary_text = generate_summary(gemini_client, raw_content, raw_filename, system_prompt)
    if not summary_text:
        print(f"    [X] 요약 생성 실패: {raw_filename}")
        return False

    summary_content = f"<!-- source: {target_url} -->\n" + summary_text
    summary_path.write_text(summary_content, encoding="utf-8")
    print(f"    4. 심층 요약문 저장 완료: {summary_filename} ({len(summary_content):,} chars)")

    # 4. Google Drive 자동 동기화
    print("    5. Google Drive로 실시간 백업 중...")
    sync_to_google_drive(raw_path, summary_path, year_str)

    # 5. 이력 등록
    history.add(target_url)
    save_history(history)

    # 6. 이메일 대기열 추가
    append_to_pending(title, summary_text, target_url)

    # 7. 웹 대시보드 원본(results.json) 등록 (카탈로그·사이트 재빌드는 main에서 한 번)
    update_results_json(title, summary_text, target_url, slug, pub_time,
                        summary_prompt.is_paywalled(raw_content))

    print(f"    [★] 아티클 처리 완료: {slug}")
    return True


def process_urls(urls, firecrawl_key, gemini_client, system_prompt, history):
    """URL 목록을 차례로 처리하고, 하나라도 성공하면 카탈로그·사이트를 한 번 재빌드한다."""
    before = len(history)
    for url in urls:
        process_target_url(url, firecrawl_key, gemini_client, system_prompt, history)
        time.sleep(2)
    done = len(history) - before  # 이번에 새로 완료(history 등록)된 글 수
    if done:
        trigger_catalog_rebuild()
        print(f"[★] 파이프라인 전체 완료! {done}건이 웹 게시판 및 Google Drive에 반영되었습니다.")

def main():
    env = load_env()
    firecrawl_key = env.get("FIRECRAWL_API_KEY")
    gemini_key = env.get("GEMINI_API_KEY")

    if not firecrawl_key or not gemini_key:
        print("ERROR: FIRECRAWL_API_KEY or GEMINI_API_KEY missing in .env", file=sys.stderr)
        sys.exit(1)

    system_prompt = summary_prompt.load_system_prompt(PROMPT_FILE)
    gemini_client = genai.Client(api_key=gemini_key, http_options=types.HttpOptions(timeout=90000))
    history = load_history()

    args = sys.argv[1:]

    # 옵션 1: 최신 아티클 자동 크롤링 모니터링 (--check-latest)
    if not args or "--check-latest" in args:
        print("=== [SemiAnalysis 데일리 자동 모니터링 시작] ===")
        print(f"[*] 기존 처리 완료 아티클 수: {len(history)}건")
        print("[*] Substack 아카이브 및 Firecrawl을 통해 최신 발행 목록 확인 중...")
        latest_links = fetch_latest_links("https://newsletter.semianalysis.com", firecrawl_key, limit=25)
        
        known_slugs = load_known_slugs(history)
        new_links = [l for l in latest_links if l not in history and slug_of(l) not in known_slugs]
        skipped = len(latest_links) - len(new_links)
        if skipped:
            print(f"[*] 이미 수집된 글 {skipped}건은 건너뜁니다 (히스토리·카탈로그·발송 기록 기준).")
        if not new_links:
            print(f"[V] 새로운 신규 아티클이 없습니다. (확인된 {len(latest_links)}개 링크 모두 처리 완료됨)")
            return

        print(f"[*] 발견된 신규 아티클: {len(new_links)}건")
        process_urls(new_links, firecrawl_key, gemini_client, system_prompt, history)
        return

    # 옵션 2: 수동 지정 URL 처리
    process_urls([u for u in args if u.startswith("http")], firecrawl_key, gemini_client, system_prompt, history)

if __name__ == "__main__":
    main()
