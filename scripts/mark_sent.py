import json
import pathlib
import sys

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

pending_path = BASE_DIR / "docs/pending.json"
sent_path = BASE_DIR / "data/state/sent.json"
sent_path.parent.mkdir(parents=True, exist_ok=True)

pending = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.exists() else []
sent = set(json.loads(sent_path.read_text(encoding="utf-8"))) if sent_path.exists() else set()
for item in pending:
    if item.get("url"):
        sent.add(item["url"])

sent_path.write_text(json.dumps(sorted(sent), ensure_ascii=False, indent=2), encoding="utf-8")
print(f"발송 기록 갱신: 총 {len(sent)}개 URL")
