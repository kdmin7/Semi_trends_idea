"""
compare_summaries.py
semianalysis_summary_prompt.md 의 프롬프트로 샘플 글 몇 편을 요약해, 공급자(Claude / Gemini)별 결과와 토큰 사용량을 비교한다.

- 샘플은 output/semianalysis 의 파일을 크기순으로 늘어놓고 고르게 뽑는다 (--files 로 직접 지정 가능).
- 결과는 output/summary_compare/<공급자>/<파일명>.md, 토큰·시간 기록은 output/summary_compare/report.csv.
- --dry-run 이면 API 를 부르지 않고 샘플 선정과 전처리 결과(글자 수, 대략의 토큰 수)만 보여 준다.
- API 키: ANTHROPIC_API_KEY / GEMINI_API_KEY (환경변수 또는 .env). summarizer.py 와 같은 방식이다.
- run_daily.py 파이프라인과는 무관하게 단독으로 실행한다.

사용 예
    python scripts/compare_summaries.py --dry-run
    python scripts/compare_summaries.py --providers claude,gemini --n 5
"""
import argparse
import csv
import os
import pathlib
import re
import sys
import time

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "output" / "semianalysis"
OUT_DIR = BASE_DIR / "output" / "summary_compare"
PROMPT_PATH = BASE_DIR / "semianalysis_summary_prompt.md"

CLAUDE_MODEL = "claude-sonnet-5-5"  # summarizer.py 와 같은 값
GEMINI_MODEL = "gemini-2.5-flash"  # 기본값. 현재 쓸 수 있는 모델명은 --gemini-model 로 바꾼다
MAX_INPUT_CHARS = 120_000  # 이보다 긴 글은 잘라서 넣고, 잘랐다고 입력에 표시한다
MAX_OUTPUT_TOKENS = 8000


# ---------------------------------------------------------------- 입력 준비
def load_prompt():
    """프롬프트 파일에서 '## 역할' ~ '## 운영 메모' 앞까지를 시스템 프롬프트로 쓴다."""
    text = PROMPT_PATH.read_text(encoding="utf-8")
    start = text.index("## 역할")
    end = text.find("\n## 운영 메모")
    body = text[start:end] if end != -1 else text[start:]
    return body.rstrip().rstrip("-").rstrip()


def split_frontmatter(text):
    """(frontmatter dict, 본문) 을 돌려준다. frontmatter 가 없으면 ({}, 전체)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, text[end + 4:].lstrip("\n")


NOISE_LINE = re.compile(
    r"^\s*(\d+|Share|PreviousNext|Subscribe|Source:.*|∙ (Paid|Free))\s*$", re.I
)


def clean_body(body):
    """링크 마크다운을 글자만 남기고, 이미지·구독 안내 같은 잡음 줄을 지운다."""
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
    body = re.sub(r"\[([^\]]*)\]\((?:[^()]|\([^)]*\))*\)", r"\1", body)
    lines = [ln for ln in body.splitlines() if not NOISE_LINE.match(ln)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def scope_line(meta, body):
    """수집 범위 한 줄. frontmatter 와 paywall 문구로 코드에서 먼저 판정한다."""
    content = meta.get("content", "")
    paywall = "This post is for paid subscribers" in body
    if "preview" in content or paywall:
        return "수집 범위: 유료 미리보기(일부)"
    if content or meta.get("audience"):
        return "수집 범위: 전문"
    return "수집 범위: 확인 필요"


def build_user_message(path):
    raw = path.read_text(encoding="utf-8")
    meta, body = split_frontmatter(raw)
    scope = scope_line(meta, body)
    cleaned = clean_body(body)
    note = ""
    if len(cleaned) > MAX_INPUT_CHARS:
        cleaned = cleaned[:MAX_INPUT_CHARS]
        note = "\n\n[입력 길이 제한으로 본문 뒷부분이 잘렸다. 수집 범위에 '일부 누락'을 반영할 것]"
    head = "\n".join(
        f"{k}: {meta[k]}" for k in ("title", "date", "source", "audience", "content") if k in meta
    )
    return f"{scope}\n{head}\n\n{cleaned}{note}"


def pick_samples(n, files):
    import storage  # 원문 md 위치(Drive + 로컬) 한곳 관리
    raw = storage.raw_files()
    if files:
        by_name = {p.name: p for p in raw}
        return [pathlib.Path(f) if os.path.isabs(f) else by_name.get(f, SRC_DIR / f) for f in files]
    allf = sorted(raw, key=lambda p: p.stat().st_size)
    if not allf:
        sys.exit(f"[Error] {SRC_DIR} 에 .md 파일이 없습니다")
    step = len(allf) / n
    return [allf[int(step * i + step / 2)] for i in range(n)]


# ---------------------------------------------------------------- API 키
def load_key(name):
    val = os.environ.get(name, "").strip()
    if val:
        return val
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# ---------------------------------------------------------------- 공급자
def run_claude(system, user, model):
    key = load_key("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY 가 없습니다 (.env 에 추가)")
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if resp.stop_reason not in ("end_turn", "stop_sequence"):
        text += f"\n\n<!-- stop_reason={resp.stop_reason}: 출력이 중간에 끊겼을 수 있음 -->"
    return text, resp.usage.input_tokens, resp.usage.output_tokens


def run_gemini(system, user, model):
    key = load_key("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY 가 없습니다 (.env 에 추가)")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model=model,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system, max_output_tokens=MAX_OUTPUT_TOKENS
        ),
    )
    u = resp.usage_metadata
    return (resp.text or "").strip(), u.prompt_token_count, u.candidates_token_count


# ---------------------------------------------------------------- 실행
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--providers", default="claude,gemini", help="쉼표로 구분 (claude, gemini)")
    ap.add_argument("--n", type=int, default=5, help="샘플 수")
    ap.add_argument("--files", nargs="*", help="샘플 파일을 직접 지정 (파일명 또는 절대경로)")
    ap.add_argument("--claude-model", default=CLAUDE_MODEL)
    ap.add_argument("--gemini-model", default=GEMINI_MODEL)
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 샘플과 입력 크기만 확인")
    args = ap.parse_args()

    system = load_prompt()
    samples = pick_samples(args.n, args.files)
    providers = [p.strip() for p in args.providers.split(",") if p.strip()]

    print(f"시스템 프롬프트: {len(system)}자 (대략 {len(system) // 3} 토큰)")
    messages = {}
    for p in samples:
        msg = build_user_message(p)
        messages[p] = msg
        raw_kb = p.stat().st_size / 1024
        print(f"- {p.name[:70]:70} 원본 {raw_kb:7.1f}KB → 전처리 후 {len(msg):7}자 (대략 {len(msg) // 4} 토큰)")

    if args.dry_run:
        print("\n[dry-run] API 를 호출하지 않았습니다.")
        return

    runners = {
        "claude": (run_claude, args.claude_model),
        "gemini": (run_gemini, args.gemini_model),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for prov in providers:
        if prov not in runners:
            print(f"[Warning] 알 수 없는 공급자: {prov}", file=sys.stderr)
            continue
        fn, model = runners[prov]
        (OUT_DIR / prov).mkdir(exist_ok=True)
        for p in samples:
            t0 = time.time()
            try:
                text, tin, tout = fn(system, messages[p], model)
            except Exception as e:
                print(f"[Error] {prov} {p.name[:40]}: {type(e).__name__}: {e}", file=sys.stderr)
                rows.append([prov, model, p.name, "", "", "", f"ERROR {type(e).__name__}"])
                continue
            sec = time.time() - t0
            (OUT_DIR / prov / p.name).write_text(text, encoding="utf-8")
            rows.append([prov, model, p.name, tin, tout, f"{sec:.1f}", "ok"])
            print(f"{prov:7} {p.name[:50]:50} 입력 {tin} / 출력 {tout} 토큰, {sec:.1f}초")

    report = OUT_DIR / "report.csv"
    with report.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["provider", "model", "file", "input_tokens", "output_tokens", "seconds", "status"])
        w.writerows(rows)
    print(f"\n결과: {OUT_DIR}\n기록: {report}")


if __name__ == "__main__":
    main()
