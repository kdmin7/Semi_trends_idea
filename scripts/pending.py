import json
import pathlib
import sys

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

items_path = BASE_DIR / "docs/items.json"
sent_path = BASE_DIR / "data/state/sent.json"
pending_path = BASE_DIR / "docs/pending.json"

items = json.loads(items_path.read_text(encoding="utf-8")) if items_path.exists() else []
sent = set(json.loads(sent_path.read_text(encoding="utf-8"))) if sent_path.exists() else set()

pending = [i for i in items if i.get("url") not in sent]
pending_path.parent.mkdir(parents=True, exist_ok=True)
pending_path.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"미발송 항목: {len(pending)}개")
