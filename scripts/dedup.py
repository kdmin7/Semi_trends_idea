import json
import os
import pathlib
import sys

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent

def dedup_jsonl_items():
    """Deduplicates items in data/items/*.jsonl and synchronizes data/state/seen.json."""
    items_dir = BASE_DIR / "data" / "items"
    state_path = BASE_DIR / "data" / "state" / "seen.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)

    seen = set()
    total_scanned = 0
    total_duplicates = 0

    if items_dir.exists():
        for file in sorted(items_dir.glob("*.jsonl")):
            lines = file.read_text(encoding="utf-8").splitlines()
            kept = []
            for line in lines:
                if not line.strip():
                    continue
                total_scanned += 1
                try:
                    item = json.loads(line)
                    url = item.get("url")
                except json.JSONDecodeError:
                    url = None

                if not url or url in seen:
                    total_duplicates += 1
                    continue

                seen.add(url)
                kept.append(line)

            file.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")

    state_path.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2), encoding="utf-8")
    return len(seen), total_scanned, total_duplicates

def dedup_results():
    """Deduplicates items in output/results.json."""
    results_path = BASE_DIR / "output" / "results.json"

    if not results_path.exists():
        return 0, 0, 0

    try:
        results = json.loads(results_path.read_text(encoding="utf-8"))
    except Exception:
        results = []

    seen_urls = set()
    unique_results = []
    duplicates = 0

    for item in results:
        url = item.get("url")
        if not url or url in seen_urls:
            duplicates += 1
            continue
        seen_urls.add(url)
        unique_results.append(item)

    results_path.write_text(json.dumps(unique_results, ensure_ascii=False, indent=2), encoding="utf-8")

    return len(unique_results), len(results), duplicates

def main():
    print("=" * 50)
    print("URL Monitor 중복 제거 (dedup.py)")
    print("=" * 50)

    # 1. data/items/*.jsonl & data/state/seen.json
    unique_items, total_scanned, dup_items = dedup_jsonl_items()
    print(f"[JSONL 항목] 검사: {total_scanned}건 | 중복 제거: {dup_items}건 | 고유 항목(seen.json): {unique_items}건")

    # 2. output/results.json
    unique_res, total_res, dup_res = dedup_results()
    print(f"[Results] 검사: {total_res}건 | 중복 제거: {dup_res}건 | 고유 항목: {unique_res}건")

    print("-" * 50)
    print("중복 제거 및 상태 파일 동기화 완료.")

if __name__ == "__main__":
    main()
