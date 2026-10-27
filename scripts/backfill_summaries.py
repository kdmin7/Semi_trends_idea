"""
scripts/backfill_summaries.py
한국어 요약이 아직 없는 글(data/summaries.json 캐시에 slug 가 없는 글)을 Claude API 로 요약해
output/results.json, data/items/*.jsonl, data/summaries.json 을 갱신하고 사이트 데이터를 다시 만든다.

  python scripts/backfill_summaries.py --dry-run     # 대상 건수와 예상 비용만 출력 (API 호출 없음)
  python scripts/backfill_summaries.py --limit 5     # 최신 5건만
  python scripts/backfill_summaries.py               # 전체 (끝나면 사이트 데이터도 다시 만든다. --no-build 로 생략)

ANTHROPIC_API_KEY 가 없으면 아무것도 바꾸지 않고 정상 종료한다 (run_daily 가 멈추지 않도록).
이미 발송한 글의 요약이 바뀌어도 sent.json 은 URL 기준이라 다시 발송되지 않는다.
"""
import argparse
import json
import pathlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import summarizer  # noqa: E402

PAYWALL_NOTE = " (상세 본문은 유료 구독 필요, 원문 확인 필요)"
RESULTS_PATH = BASE_DIR / "output" / "results.json"
ITEMS_DIR = BASE_DIR / "data" / "items"


def slug_of(item):
    return item.get("slug") or item.get("url", "").rstrip("/").split("/")[-1]


def base_text(item):
    """저장된 summary 에서 유료 안내 문구를 뗀 것 = 아직 요약되지 않은 영어 부제."""
    text = item.get("summary", "")
    return text[: -len(PAYWALL_NOTE)] if text.endswith(PAYWALL_NOTE) else text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="최신 글부터 N건만 (0 = 전부)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-build", action="store_true", help="사이트 데이터 재생성을 건너뜀 (run_daily 가 뒤 단계에서 한 번에 만든다)")
    args = ap.parse_args()

    if not RESULTS_PATH.exists():
        print("[Info] output/results.json 이 없습니다.")
        return 0
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    cache = summarizer.load_cache()

    targets = [r for r in results if slug_of(r) not in cache]
    if args.limit:
        targets = targets[: args.limit]
    print(f"요약 필요: {len(targets)}건 (캐시 {len(cache)}건 / 전체 {len(results)}건)")
    if not targets:
        return 0
    if args.dry_run:
        # 글당 입력 약 700 / 출력 약 250 토큰 가정, Sonnet 5.5 ($2 / $10 per 1M)
        cost = len(targets) * (700 * 2 + 250 * 10) / 1_000_000
        print(f"[dry-run] 예상 비용 약 ${cost:.2f}. API 는 호출하지 않았습니다.")
        return 0
    if summarizer._get_client() is None:
        print("[Notice] ANTHROPIC_API_KEY 가 없어 요약을 건너뜁니다. .env 에 키를 넣으면 다음 실행부터 채워집니다.")
        return 0

    def work(item):
        return item["url"], summarizer.generate_summary(item.get("title", ""), base_text(item))

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        generated = dict(pool.map(work, targets))

    by_url = {r["url"]: r for r in targets}
    new_summary = {}  # url -> 최종 summary (유료 안내 포함)
    done = failed = 0
    for url, text in generated.items():
        item = by_url[url]
        if not text:
            failed += 1
            continue
        cache[slug_of(item)] = text
        new_summary[url] = text + (PAYWALL_NOTE if item.get("paywalled") else "")
        done += 1

    if not done:
        print(f"[Warning] 성공 0건 / 실패 {failed}건 — 아무것도 바꾸지 않았습니다.", file=sys.stderr)
        return 0

    summarizer.save_cache(cache)

    for r in results:
        if r["url"] in new_summary:
            r["summary"] = new_summary[r["url"]]
    RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    for f in sorted(ITEMS_DIR.glob("*.jsonl")):
        lines, changed = [], False
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                obj = json.loads(line)
                if obj.get("url") in new_summary:
                    obj["summary"] = new_summary[obj["url"]]
                    changed = True
                line = json.dumps(obj, ensure_ascii=False)
                lines.append(line)
        if changed:
            f.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if not args.no_build:
        # 사이트 데이터 재생성: assets/data.js + docs/ (build_site), docs/items.json (scripts/build_dashboard)
        from build_site import build_all
        build_all()
        subprocess.run([sys.executable, str(BASE_DIR / "scripts" / "build_dashboard.py")], cwd=BASE_DIR, check=True)

    print(f"요약 완료 {done}건 / 실패 {failed}건 (실패분은 다음 실행에서 다시 시도)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
