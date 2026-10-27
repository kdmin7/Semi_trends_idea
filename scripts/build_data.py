import glob
import json
import os
import re

import storage  # 요약 md 위치(Drive + 로컬) 한곳 관리

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_PATH = os.path.join(BASE_DIR, "output", "results.json")
ENRICH_PATH = os.path.join(BASE_DIR, "enrich.json")
DATA_JS_PATH = os.path.join(BASE_DIR, "assets", "data.js")
DETAIL_DIR = os.path.join(BASE_DIR, "assets", "summaries")

MAX_POINTS = 5


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _clean(s):
    """마크다운 강조 제거 + 카드 innerHTML에서 태그로 해석되지 않도록 꺾쇠 치환."""
    s = re.sub(r"\*\*|__|`", "", s).strip()
    return s.replace("<", "＜").replace(">", "＞")


def _section(text, name):
    m = re.search(r"^##\s*\d*\.?\s*" + name + r"[^\n]*\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


def _bullets(block):
    return [_clean(l.lstrip()[2:]) for l in block.splitlines() if re.match(r"^- ", l)]


def parse_summary_file(path):
    """요약 md → {summary, points, takeaway}. 섹션이 없으면 해당 키는 비워 둔다."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    execsum = [b for b in _bullets(_section(text, "Executive Summary")) if b]

    insights = _section(text, "Key Insights")
    points = [_clean(h) for h in re.findall(r"^###\s+(.+)$", insights, re.M)]
    if not points:  # 구형 포맷: 최상위 불릿이 곧 항목
        points = [b for b in _bullets(insights) if b]

    takeaway = [b for b in _bullets(_section(text, "Conclusion")) if b]

    return {
        "summary": "\n".join(execsum),
        "points": points[:MAX_POINTS],
        "takeaway": "\n".join(takeaway),
        "_detail": parse_detail(text),
        "_head": text[:3000],
        "_title": (re.search(r"^#\s*(?:\[[^\]]*\]\s*)?(.+)$", text, re.M) or [None, ""])[1],
    }


def _md(s):
    """모달 상세용: 마크다운 강조 기호만 제거한다(이스케이프는 클라이언트가 한다)."""
    return re.sub(r"\*\*|__|`", "", s).strip()


def _split_sections(text):
    """'## N. 제목' 단위로 {번호: 본문} 분리. 본문의 '---' 구분선은 제거한다."""
    parts = re.split(r"^##\s*(\d+)\.[^\n]*\n", text, flags=re.M)
    out = {}
    for i in range(1, len(parts) - 1, 2):
        out[int(parts[i])] = re.sub(r"^\s*---\s*$", "", parts[i + 1], flags=re.M).strip("\n")
    return out


def _table(block):
    """첫 마크다운 표 → {head, rows}. 없으면 None."""
    lines = [l.strip() for l in block.splitlines() if l.strip().startswith("|")]
    if len(lines) < 2:
        return None
    cells = [[_md(c) for c in l.strip("|").split("|")] for l in lines]
    rows = [r for r in cells if not all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in r)]
    return {"head": rows[0], "rows": rows[1:]}


def _items(block):
    """불릿/번호 목록 → [{label?, text}]. 들여쓴 줄(이어쓰기)은 직전 항목에 붙인다."""
    items = []
    for line in block.splitlines():
        m = re.match(r"^\s*(?:[-*]|\d+\.)\s+(.*)$", line)
        if m and not re.match(r"^\s*\|", line):
            body = m.group(1).strip()
            raw = _md(body)
            lm = re.match(r"^\*\*(.+?)\*\*\s*[:：]?\s*(.*)$", body)
            if lm:
                label = _md(lm.group(1)).rstrip(":：").strip()
                rest = lm.group(2)
                if not rest and body.endswith(":**"):
                    rest = ""
                items.append({"label": label, "text": _md(rest), "raw": raw})
            else:
                items.append({"text": raw, "raw": raw})
        elif items and line.strip() and not line.lstrip().startswith(("#", "|", ">")):
            items[-1]["text"] = (items[-1]["text"] + " " + _md(line)).strip()
    return [i for i in items if i["text"] or i.get("label")]


def _strip(items):
    return [{k: v for k, v in i.items() if k != "raw"} for i in items]


def _plain(items):
    return [i["raw"] for i in items]


def parse_detail(text):
    """요약 md 전체(1~10번 + 헤더 표) → 모달 상세용 구조. 키가 비면 생략한다."""
    sec = _split_sections(text)
    head = text.split("\n## ", 1)[0]
    meta_t = _table(head)
    meta = []
    if meta_t:
        meta = [{"k": r[0], "v": r[1]} for r in meta_t["rows"] if len(r) >= 2 and r[0] != "정리 관점"]

    insights = []
    ins_block = sec.get(2, "")
    for h in re.split(r"^###\s+", ins_block, flags=re.M)[1:]:
        title, _, body = h.partition("\n")
        title = _md(title)
        tm = re.match(r"^\[([^\]]+)\]\s*(.*)$", title)
        insights.append({
            "tag": tm.group(1) if tm else "",
            "title": tm.group(2) if tm else title,
            "items": _strip(_items(body)),
        })
    if not insights and ins_block:  # 구형 포맷: 최상위 불릿
        for it in _items(ins_block):
            tm = re.match(r"^\[([^\]]+)\]\s*(.*)$", it.get("label", ""))
            head, sep, judge = it["text"].partition("필자 판단:")
            rows = []
            if head.strip():
                rows.append({"label": "원문 근거" if sep else "", "text": head.strip().rstrip("。 ")})
            if judge.strip():
                rows.append({"label": "필자 판단", "text": judge.strip()})
            insights.append({"tag": tm.group(1) if tm else "", "title": tm.group(2) if tm else it.get("label", ""),
                             "items": [{k: v for k, v in r.items() if v} for r in rows]})

    topics = []
    for h in re.split(r"^###\s+", sec.get(4, ""), flags=re.M)[1:]:
        title, _, body = h.partition("\n")
        topics.append({"title": _md(title), "items": _strip(_items(body))})

    todos = []
    for line in sec.get(8, "").splitlines():
        m = re.match(r"^\s*-\s*\[([ xX])\]\s*(.*)$", line)
        if m:
            todos.append({"done": m.group(1) != " ", "text": _md(m.group(2))})

    detail = {
        "meta": meta,
        "summary": _strip(_items(sec.get(1, ""))),
        "insights": insights,
        "metrics": _table(sec.get(3, "")),
        "topics": topics,
        "views": _strip(_items(sec.get(5, ""))),
        "limits": _plain(_items(sec.get(6, ""))),
        "conclusion": _strip(_items(sec.get(7, ""))),
        "todos": todos,
        "glossary": _table(sec.get(9, "")),
    }
    return {k: v for k, v in detail.items() if v}


def load_summaries():
    """slug / 정규화 제목 → 파싱 결과. 헤더의 /p/<slug> URL을 우선, 없으면 제목으로 매칭."""
    by_slug, by_title = {}, {}
    for path in storage.summary_files():
        path = str(path)
        info = parse_summary_file(path)
        m = re.search(r"/p/([^\s|)>\]/?#]+)", info["_head"])
        if m:
            by_slug[m.group(1)] = info
        stem = re.sub(r"^(\d+_)?\d{4}-\d{2}-\d{2}_", "", os.path.basename(path)).replace("_summary.md", "").replace("_", " ")
        for t in (info["_title"], stem):
            if t:
                by_title.setdefault(_norm(t), info)
    return by_slug, by_title


def merge_summaries(results):
    if not storage.summary_files():
        return 0
    by_slug, by_title = load_summaries()
    merged = 0
    # 이전 빌드 잔재 제거 후 글별 상세 데이터 재생성.
    # file://에서도 읽히도록 JSON이 아닌 <script>로 로드하는 .js(window.POST_DETAIL[slug])로 쓴다.
    if os.path.isdir(DETAIL_DIR):
        for old in glob.glob(os.path.join(DETAIL_DIR, "*.json")) + glob.glob(os.path.join(DETAIL_DIR, "*.js")):
            os.remove(old)
    os.makedirs(DETAIL_DIR, exist_ok=True)
    for r in results:
        info = by_slug.get(r["slug"]) or by_title.get(_norm(r.get("title", "")))
        if not info:
            continue
        if info["summary"]:
            r["feed_summary"] = r.get("summary", "")
            r["summary"] = info["summary"]
        if info["points"] and not r.get("points"):
            r["points"] = info["points"]
        if info["takeaway"] and not r.get("takeaway"):
            r["takeaway"] = info["takeaway"]
        if info.get("_detail"):
            payload = json.dumps(info["_detail"], ensure_ascii=False, separators=(",", ":"))
            with open(os.path.join(DETAIL_DIR, r["slug"] + ".js"), "w", encoding="utf-8") as f:
                f.write("window.POST_DETAIL=window.POST_DETAIL||{};"
                        "window.POST_DETAIL[%s]=%s;\n" % (json.dumps(r["slug"]), payload))
            r["has_detail"] = True
        merged += 1
    return merged


def build_data():
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    enrich = {}
    if os.path.exists(ENRICH_PATH):
        with open(ENRICH_PATH, "r", encoding="utf-8") as f:
            enrich = json.load(f)

    # Ensure enrich fields are merged if missing
    for r in results:
        slug = r.get("slug") or r.get("url", "").rstrip("/").split("/")[-1]
        r["slug"] = slug
        if slug in enrich:
            if not r.get("title_ko"):
                r["title_ko"] = enrich[slug].get("ko")
            if not r.get("category"):
                r["category"] = enrich[slug].get("c")

    merged = merge_summaries(results)
    d = storage.drive_dir()
    print(f"Merged {merged}/{len(results)} posts with {len(storage.summary_files())} summaries (Drive: {d if d else '미연결, 로컬만'})")

    data_json = json.dumps(results, ensure_ascii=False, indent=2)
    js_content = f"/* Auto-generated by scripts/build_data.py — file:// fallback data */\nwindow.POSTS = {data_json};\n"

    os.makedirs(os.path.dirname(DATA_JS_PATH), exist_ok=True)
    with open(DATA_JS_PATH, "w", encoding="utf-8") as f:
        f.write(js_content)

    print(f"Successfully generated {DATA_JS_PATH} with {len(results)} records.")

if __name__ == "__main__":
    build_data()
