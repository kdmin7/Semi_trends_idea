import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

from summarizer import get_summary

sys.stdout.reconfigure(encoding='utf-8')

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config", "config.yaml")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
SEEN_FILE = os.path.join(DATA_DIR, "state", "seen.json")
RESULTS_FILE = os.path.join(OUTPUT_DIR, "results.json")
PAYWALL_NOTE = " (상세 본문은 유료 구독 필요, 원문 확인 필요)"

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 1. Load seen state (Principle 1: Only handle new items)
#    data/state/seen.json 이 '이미 처리한 URL' 의 단일 기준이다. 파일이 없거나 깨졌으면 output/results.json 에서 복구한다.
def load_seen():
    for path in (SEEN_FILE, RESULTS_FILE):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return set(data if path == SEEN_FILE else (r["url"] for r in data if r.get("url")))
        except Exception:
            continue
    return set()

processed_urls = load_seen()

# Helper to fetch Substack archive
def fetch_substack_posts(base_url, since=None, page_size=25, max_pages=40):
    """최신순으로 페이지를 넘기며 가져온다. since 보다 오래된 글이 나오면 멈춘다."""
    posts = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    for page in range(max_pages):
        offset = page * page_size
        api_url = f"{base_url.rstrip('/')}/api/v1/archive?sort=new&limit={page_size}&offset={offset}"
        try:
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if page == 0:
                # 아무것도 못 가져왔다면 '새 글 없음'과 구분되도록 실패로 끝낸다 (run_daily 가 중단·기록)
                raise RuntimeError(f"아카이브 수집 실패 (첫 페이지): {e}") from e
            print(f"[Error] Failed to fetch archive at offset {offset}: {e}", file=sys.stderr)
            break
        if not data:
            break
        posts.extend(data)
        oldest = datetime.fromisoformat(data[-1]["post_date"].replace("Z", "+00:00"))
        if since and oldest < since:
            break
    return posts

def load_config():
    """config/config.yaml 에서 period 블록과 sources 목록을 읽는다 (PyYAML 없이 이 파일 형식만 해석).
    end 가 비어 있으면 '지금'까지 추적한다."""
    period, sources = {}, []
    section = None
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.split("#", 1)[0].rstrip()
                if not line.strip():
                    continue
                if not raw.startswith((" ", "	")):
                    section = line.strip().rstrip(":") if line.strip().endswith(":") else None
                    continue
                body = line.strip()
                if section == "period" and ":" in body:
                    k, v = body.split(":", 1)
                    period[k.strip()] = v.strip().strip('"').strip("'")
                elif section == "sources":
                    if body.startswith("- "):
                        sources.append({})
                        body = body[2:].strip()
                    if sources and ":" in body:
                        k, v = body.split(":", 1)
                        sources[-1][k.strip()] = v.strip().strip('"').strip("'")

    start = period.get("start") or "2026-05-28"
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end = period.get("end")
    if end:
        end_dt = datetime.fromisoformat(end).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    else:
        end_dt = datetime.now(timezone.utc)
    return start_dt, end_dt, sources

def make_summary(slug, title, subtitle, desc, audience):
    """요약은 summarizer(Claude API, data/summaries.json 캐시)가 만든다. 실패하면 부제로 대신하고
    scripts/backfill_summaries.py 가 다음에 다시 시도한다."""
    summary_text = get_summary(slug, title, subtitle, desc)
    if not summary_text:
        summary_text = (subtitle or desc or title).strip()

    # 유료 글은 본문을 볼 수 없으므로 그 사실을 명시한다 (paywalled 판정과 같은 조건)
    if audience != "everyone":
        summary_text += PAYWALL_NOTE
    return summary_text

def run_monitor():
    now_iso = datetime.now(timezone.utc).isoformat()
    # Filter period: config/config.yaml 의 period.start ~ period.end (end 가 비어 있으면 실행 시점까지)
    start_dt, end_dt, sources = load_config()

    new_results = []
    failed_items = []

    # config.yaml 의 sources 마다 Substack 아카이브(archive 주소)를 수집한다
    raw_posts, source_errors = [], []
    for src in sources:
        label = src.get("label") or src.get("url", "?")
        if not src.get("archive"):
            failed_items.append({"title": label, "reason": "config.yaml 에 archive 주소가 없어 수집하지 않음"})
            continue
        try:
            for post in fetch_substack_posts(src["archive"], since=start_dt):
                post["_source"] = src.get("url") or src["archive"]
                raw_posts.append(post)
        except RuntimeError as e:
            source_errors.append(f"{label}: {e}")
            failed_items.append({"title": label, "reason": str(e)})
    if not sources:
        raise RuntimeError("config/config.yaml 에 sources 가 없습니다.")
    if source_errors and len(source_errors) == len(sources):
        # 모든 소스가 실패했다면 '새 글 없음'과 구분되도록 실패로 끝낸다
        raise RuntimeError("; ".join(source_errors))

    # Deduplicate
    unique_posts = {}
    for p in raw_posts:
        unique_posts.setdefault((p["_source"], p["id"]), p)

    # Sort descending by date
    sorted_posts = sorted(unique_posts.values(), key=lambda x: x["post_date"], reverse=True)

    for p in sorted_posts:
        url = p.get("canonical_url")
        # Principle 3: Must include URL. If missing, fail with reason.
        if not url:
            failed_items.append({"title": p.get("title"), "reason": "URL 누락으로 제외됨"})
            continue

        # Principle 1: Only new items
        if url in processed_urls:
            continue

        dt = datetime.fromisoformat(p["post_date"].replace("Z", "+00:00"))
        if not (start_dt <= dt <= end_dt):
            continue

        slug = p.get("slug", "")
        title = p.get("title", "").strip()
        subtitle = p.get("subtitle", "")
        desc = p.get("description", "")
        audience = p.get("audience", "everyone")

        # Principle 2 & 4: Facts only, rephrased, max 3 sentences, no speculation
        summary = make_summary(slug, title, subtitle, desc, audience)

        item = {
            "title": title,
            "summary": summary,
            "url": url,
            "source": p["_source"],
            "detected_at": now_iso,
            "slug": slug,
            "paywalled": (audience != "everyone"),
            "published_at": p.get("post_date")
        }
        new_results.append(item)
        processed_urls.add(url)

    # Save results
    existing_results = []
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                existing_results = json.load(f)
        except Exception:
            existing_results = []

    all_results = new_results + existing_results
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    # Sync to data/items/ and data/state/seen.json (processed_urls 의 단일 저장소)
    if new_results:
        import pathlib
        curr_month = datetime.now(timezone.utc).strftime("%Y-%m")
        items_month_file = pathlib.Path(DATA_DIR) / "items" / f"{curr_month}.jsonl"
        items_month_file.parent.mkdir(parents=True, exist_ok=True)
        with open(items_month_file, "a", encoding="utf-8") as f:
            for item in new_results:
                jsonl_item = {
                    "title": item["title"],
                    "summary": item["summary"],
                    "url": item["url"],
                    "source": item["source"],
                    "detected_at": item["detected_at"][:10]
                }
                f.write(json.dumps(jsonl_item, ensure_ascii=False) + "\n")

        state_seen_path = pathlib.Path(SEEN_FILE)
        state_seen_path.parent.mkdir(parents=True, exist_ok=True)
        state_seen_path.write_text(json.dumps(sorted(processed_urls), ensure_ascii=False, indent=2), encoding="utf-8")

    return new_results, failed_items

if __name__ == "__main__":
    new_items, failures = run_monitor()
    print(f"Newly processed items: {len(new_items)}")
    if failures:
        print(f"Failures ({len(failures)}):")
        for f in failures:
            print(f"- {f['title']}: {f['reason']}")
    print(json.dumps(new_items[:5], ensure_ascii=False, indent=2))
