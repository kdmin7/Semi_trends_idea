#!/usr/bin/env python3
"""
scripts/url_monitor_pipeline.py
================================
SemiAnalysis 신규 아티클 실시간 모니터링 & 자동화 파이프라인.

기능:
1. Firecrawl API를 통해 최신 게시물 링크 탐색 (--check-latest 또는 수동 URL 지정)
2. data/pipeline_history.json 대조로 신규 항목만 선별 (중복 처리 방지)
3. Firecrawl로 원문 마크다운 스크랩
4. Gemini 3.8 Flash (Thinking Budget 2048) 모델로 10대 항목 심층 요약 노트 자동 작성
5. 원문 및 요약본 로컬 저장 (output/semianalysis/)
6. Google Drive (G:\\내 드라이브\\SemiAnalysis) 연도별 & 요약 통합 폴더로 자동 백업
7. docs/index.json 및 docs/summaries/ 웹 대시보드 색인 카탈로그 실시간 자동 재빌드
8. 이메일 발송 대기열(docs/pending.json) 연동
"""

import os
import sys
import re
import json
import time
import shutil
import pathlib
import urllib.request
import urllib.error
from datetime import datetime, timezone
from google import genai
from google.genai import types

import storage  # 원문·요약 저장 위치(Drive + 로컬) 한곳 관리

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

def firecrawl_map(site_url: str, api_key: str, limit: int = 25) -> list[str]:
    """Firecrawl Map API를 호출하여 사이트의 최신 링크 목록을 반환합니다."""
    endpoint = "https://api.firecrawl.dev/v1/map"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"url": site_url, "limit": limit}
    req = urllib.request.Request(endpoint, headers=headers, data=json.dumps(payload).encode("utf-8"))
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            links = data.get("links", [])
            # /p/ 가 포함된 아티클 링크만 필터링
            article_links = [l for l in links if "/p/" in l and not l.endswith("/comments")]
            return article_links
    except Exception as e:
        print(f"[!] Firecrawl map error: {e}", file=sys.stderr)
        return []

def firecrawl_scrape(url: str, api_key: str):
    """Firecrawl Scrape API를 호출하여 대상 URL의 마크다운 본문을 추출합니다."""
    endpoint = "https://api.firecrawl.dev/v1/scrape"
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

def generate_summary(client, original_content: str, filename: str, system_prompt: str) -> str:
    """Gemini 3.8 Flash 모델로 고품질 요약 노트를 작성합니다."""
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        thinking_config=types.ThinkingConfig(thinking_budget=2048),
        temperature=0.2,
    )
    user_prompt = f"""다음 웹사이트 게시물 전문을 읽고 시스템 지침(작성 규칙 및 10대 출력 형식)에 따라 정확한 읽기 노트를 작성하라.

[입력 파일명]: {filename}

--- 원문 내용 ---
{original_content}
"""
    # gemini-3.8-flash 우선 호출
    models_to_try = ["gemini-3.8-flash", "gemini-3-flash-preview"]
    for model_name in models_to_try:
        try:
            res = client.models.generate_content(
                model=model_name,
                contents=user_prompt,
                config=config,
            )
            text = res.text.strip()
            if text:
                return text
        except Exception as e:
            print(f"    [!] Summary generation error with {model_name}: {e}", file=sys.stderr)
            time.sleep(2)
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
    """웹 대시보드 색인 카탈로그(docs/index.json) 자동 갱신"""
    try:
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
        from scripts.build_catalog import build_catalog
        print("    [*] 웹 대시보드 색인 카탈로그(docs/index.json) 자동 갱신 중...")
        build_catalog()
    except Exception as e:
        print(f"    [!] Catalog rebuild error: {e}", file=sys.stderr)

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

def process_target_url(target_url: str, firecrawl_key: str, gemini_client, system_prompt: str, history: set) -> bool:
    """단일 신규 URL 파이프라인 수행"""
    if target_url in history:
        print(f"[-] 이미 처리된 URL: {target_url} (Skip)")
        return True

    print(f"\n========================================================")
    print(f"[*] 신규 아티클 감지: {target_url}")
    print(f"========================================================")

    # 1. Firecrawl 스크랩
    print("    1. Firecrawl로 원문 스크랩 중...")
    markdown, metadata = firecrawl_scrape(target_url, firecrawl_key)
    if not markdown:
        print(f"    [X] 원문 스크랩 실패: {target_url}")
        return False

    title = metadata.get("title") or "Untitled"
    pub_time = metadata.get("publishedTime") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_str = pub_time[:10] if len(pub_time) >= 10 else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    year_str = date_str[:4]

    clean_title_str = sanitize_filename(title)
    base_stem = f"{date_str}_{clean_title_str}"
    raw_filename = f"{base_stem}.md"
    summary_filename = f"{base_stem}_summary.md"

    # 2. 로컬 디렉토리 저장
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)

    raw_path = RAW_DIR / raw_filename
    summary_path = SUMMARY_DIR / summary_filename

    frontmatter = f"""---
title: "{title}"
date: {date_str}
source: {target_url}
scraped_at: {datetime.now(timezone.utc).isoformat()}
---

"""
    raw_content = frontmatter + markdown
    raw_path.write_text(raw_content, encoding="utf-8")
    print(f"    2. 원문 마크다운 저장 완료: {raw_filename} ({len(raw_content):,} bytes)")

    # 3. Gemini 3.8 Flash 요약 생성
    print("    3. Gemini 3.8 Flash 심층 요약 노트 작성 중 (Thinking Budget: 2048)...")
    summary_text = generate_summary(gemini_client, raw_content, raw_filename, system_prompt)
    if not summary_text:
        print(f"    [X] 요약 생성 실패: {raw_filename}")
        return False

    summary_path.write_text(summary_text, encoding="utf-8")
    print(f"    4. 심층 요약문 저장 완료: {summary_filename} ({len(summary_text):,} chars)")

    # 4. Google Drive 자동 동기화
    print("    5. Google Drive로 실시간 백업 중...")
    sync_to_google_drive(raw_path, summary_path, year_str)

    # 5. 이력 등록
    history.add(target_url)
    save_history(history)

    # 6. 이메일 대기열 추가
    append_to_pending(title, summary_text, target_url)

    # 7. 웹 대시보드 색인 카탈로그 자동 갱신
    trigger_catalog_rebuild()

    print(f"    [★] 파이프라인 전체 완료! 웹 게시판 및 Google Drive에 반영되었습니다.")
    return True

def main():
    env = load_env()
    firecrawl_key = env.get("FIRECRAWL_API_KEY")
    gemini_key = env.get("GEMINI_API_KEY")

    if not firecrawl_key or not gemini_key:
        print("ERROR: FIRECRAWL_API_KEY or GEMINI_API_KEY missing in .env", file=sys.stderr)
        sys.exit(1)

    system_prompt = PROMPT_FILE.read_text(encoding="utf-8")
    gemini_client = genai.Client(api_key=gemini_key)
    history = load_history()

    args = sys.argv[1:]

    # 옵션 1: 최신 아티클 자동 크롤링 모니터링 (--check-latest)
    if not args or "--check-latest" in args:
        print("=== [SemiAnalysis 데일리 자동 모니터링 시작] ===")
        print(f"[*] 기존 처리 완료 아티클 수: {len(history)}건")
        print("[*] Firecrawl을 통해 최신 발행 목록 확인 중...")
        latest_links = firecrawl_map("https://newsletter.semianalysis.com", firecrawl_key, limit=20)
        
        new_links = [l for l in latest_links if l not in history]
        if not new_links:
            print(f"[V] 새로운 신규 아티클이 없습니다. (확인된 {len(latest_links)}개 링크 모두 처리 완료됨)")
            return

        print(f"[*] 발견된 신규 아티클: {len(new_links)}건")
        for link in new_links:
            process_target_url(link, firecrawl_key, gemini_client, system_prompt, history)
            time.sleep(2)
        return

    # 옵션 2: 수동 지정 URL 처리
    for url in args:
        if url.startswith("http"):
            process_target_url(url, firecrawl_key, gemini_client, system_prompt, history)
            time.sleep(2)

if __name__ == "__main__":
    main()
