# -*- coding: utf-8 -*-
"""
scripts/fetch_archive_md.py
SemiAnalysis 전체 글 목록(Substack archive API)을 받아, 아직 저장하지 않은 글만
Firecrawl로 스크랩해 YYYY-MM-DD_제목.md 로 저장한다.
저장 위치와 이름은 scripts/storage.py 규칙을 따른다: YYYY-MM-DD_slug.md,
Drive 원문 전용 폴더(없으면 Drive 연도 폴더) + 로컬 output/semianalysis/. 이미 저장한 글(옛 이름 포함)은 건너뛴다.
(--out 폴더를 직접 주면 옛 이름 YYYY-MM-DD_제목.md 로 그 폴더에만 저장)

run_daily.py 파이프라인과는 무관한 수동 실행용이다.

예)
  python scripts/fetch_archive_md.py --dry-run            # 목록만 확인 (크레딧 0)
  python scripts/fetch_archive_md.py --limit 5            # 최신 5개만
  python scripts/fetch_archive_md.py --since 2026-09-01   # 이 날짜 이후 글만
  python scripts/fetch_archive_md.py --skip-paid          # 유료 글 제외
"""
import argparse
import json
import os
import pathlib
import re
import sys
import time
import datetime
import urllib.error
import urllib.request

import storage  # 저장 위치(Drive + 로컬) 한곳 관리

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
SITE = "https://newsletter.semianalysis.com"
ARCHIVE_API = SITE + "/api/v1/archive?sort=new&limit={limit}&offset={offset}"
FIRECRAWL_API = "https://api.firecrawl.dev/v1/scrape"
PAGE = 25  # archive API 는 한 번에 최대 25건만 돌려준다. 이보다 크게 잡고 offset 을 그만큼 넘기면 중간 글이 빠진다(50이면 314/341건만 보였음)
UA = "Mozilla/5.0 (url-monitor archive fetcher)"


def get_api_key():
    """FIRECRAWL_API_KEY를 환경변수 또는 .env에서 찾는다."""
    key = os.environ.get("FIRECRAWL_API_KEY")
    if key and key.strip():
        return key.strip()
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("FIRECRAWL_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key:
                    return key
    return None


def http_json(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    if data is not None:
        req.data = json.dumps(data).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_posts(max_posts=None):
    """archive API를 빈 응답이 나올 때까지 넘기며 전체 목록(최신순)을 모은다."""
    posts, offset = [], 0
    while True:
        batch = http_json(ARCHIVE_API.format(limit=PAGE, offset=offset))
        if not batch:
            break
        posts.extend(batch)
        offset += PAGE
        if max_posts and len(posts) >= max_posts:
            break
        time.sleep(0.5)
    return posts[:max_posts] if max_posts else posts


def safe_name(title):
    """Windows 파일명에 쓸 수 없는 문자와 연속 공백을 정리한다."""
    name = re.sub(r'[\\/:*?"<>|]', "_", title)
    name = re.sub(r"\s+", "_", name).strip("._")
    return name[:120]


def clean_markdown(md, title, keep_images=False):
    """이미지 링크를 걷어내고, 제목 앞의 사이트 메뉴 부분을 잘라낸다."""
    if not keep_images:
        md = re.sub(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)", "", md)  # 링크 걸린 이미지
        md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)               # 일반 이미지
    lines = md.splitlines()
    for i, line in enumerate(lines):
        if line.lstrip("# ").strip() == title.strip():
            lines = lines[i:]
            break
    md = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


def scrape(url, api_key):
    resp = http_json(
        FIRECRAWL_API,
        data={"url": url, "formats": ["markdown"], "onlyMainContent": True},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=120,
    )
    if not resp.get("success"):
        raise RuntimeError(resp.get("error") or "scrape failed")
    return resp["data"]["markdown"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None, help="저장 폴더 (미지정 시 Drive + 로컬 기본 저장소)")
    ap.add_argument("--limit", type=int, help="최신 글 N개만 처리")
    ap.add_argument("--since", help="YYYY-MM-DD 이후 글만 처리")
    ap.add_argument("--until", help="YYYY-MM-DD 이전(당일 포함) 글만 처리")
    ap.add_argument("--slug", help="이 slug 의 글만 처리 (쉼표로 여러 개)")
    ap.add_argument("--days", type=int, help="최근 N일 이내 글만 처리 (일일 실행용, --since 가 없을 때)")
    ap.add_argument("--skip-paid", action="store_true", help="유료 글(only_paid) 제외")
    ap.add_argument("--keep-images", action="store_true", help="이미지 링크 유지")
    ap.add_argument("--delay", type=float, default=1.0, help="스크랩 사이 대기(초)")
    ap.add_argument("--dry-run", action="store_true", help="목록만 출력하고 스크랩하지 않음")
    args = ap.parse_args()
    if args.days and not args.since:
        args.since = (datetime.date.today() - datetime.timedelta(days=args.days)).isoformat()

    custom_out = args.out is not None
    out_dir = pathlib.Path(args.out) if custom_out else storage.LOCAL_RAW_DIR
    posts = list_posts()
    print(f"목록 {len(posts)}개 수집")

    todo = []
    for p in posts:
        date = (p.get("post_date") or "")[:10]
        title = p.get("title") or p.get("slug")
        paid = p.get("audience") == "only_paid"
        if args.since and date < args.since:
            continue
        if args.until and date > args.until:
            continue
        if args.skip_paid and paid:
            continue
        if args.slug and p.get("slug") not in {x.strip() for x in args.slug.split(",")}:
            continue
        path = out_dir / f"{date}_{safe_name(title)}.md"
        if custom_out:
            if path.exists():
                continue
        elif storage.raw_exists(path.name) or storage.raw_key_exists(date, p["slug"]):
            continue  # 옛 이름(YYYY-MM-DD_제목.md) 또는 날짜+slug 이름으로 이미 저장됨
        todo.append((p, date, title, paid, path))
        if args.limit and len(todo) >= args.limit:
            break

    todo.reverse()  # 오래된 글부터 처리해 번호가 발행 순서대로 이어지게 한다
    print(f"새로 저장할 글 {len(todo)}개 (예상 크레딧 {len(todo)})")
    if args.dry_run:
        for _, date, title, paid, _ in reversed(todo):
            print(f"  {date}  {'[유료] ' if paid else ''}{title}")
        return 0
    if not todo:
        return 0

    api_key = get_api_key()
    if not api_key:
        print("FIRECRAWL_API_KEY가 없습니다 (환경변수 또는 .env).", file=sys.stderr)
        return 1

    if custom_out:
        out_dir.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    for p, date, title, paid, path in todo:
        url = p.get("canonical_url") or f"{SITE}/p/{p['slug']}"
        try:
            body = clean_markdown(scrape(url, api_key), title, args.keep_images)
        except (urllib.error.URLError, RuntimeError, KeyError, json.JSONDecodeError) as e:
            fail += 1
            print(f"실패  {date} {title}: {e}", file=sys.stderr)
            continue
        status = "paid-preview" if paid else "free"
        header = (
            "---\n"
            f"title: {json.dumps(title, ensure_ascii=False)}\n"
            f"date: {date}\n"
            f"source: {url}\n"
            f"audience: {p.get('audience')}\n"
            f"content: {status}\n"
            "---\n\n"
        )
        if custom_out:
            path.write_text(header + body, encoding="utf-8")
        else:
            name = f"{date}_{p['slug']}.md"
            storage.save_raw(name, header + body)
            path = pathlib.Path(name)
        ok += 1
        print(f"저장  {path.name}{'  (프리뷰만)' if paid else ''}")
        time.sleep(args.delay)

    print(f"완료: 성공 {ok}, 실패 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
