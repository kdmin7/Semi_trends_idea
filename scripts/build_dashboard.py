import json
import pathlib
import sys

# Ensure UTF-8 output on Windows
sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
items_dir = BASE_DIR / "data" / "items"
out_path = BASE_DIR / "docs" / "items.json"

out_path.parent.mkdir(parents=True, exist_ok=True)

all_items = []
for file in sorted(items_dir.glob("*.jsonl")):
    for line in file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            all_items.append(json.loads(line))

all_items.sort(key=lambda x: x.get("detected_at", ""), reverse=True)

out_path.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"docs/items.json 갱신 완료: {len(all_items)}개 항목")
