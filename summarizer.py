"""
summarizer.py
Claude API 로 글 1편을 '사실만 담은 한국어 3문장 이내' 요약으로 만든다.

- 입력은 Substack 아카이브가 주는 제목·부제·설명뿐이다 (유료 글은 본문을 볼 수 없다).
- 결과는 data/summaries.json 에 slug 별로 캐시해 같은 글을 다시 요약하지 않는다.
- ANTHROPIC_API_KEY 가 없거나 호출이 실패하면 None 을 돌려준다 (호출한 쪽이 대체 문구를 쓴다).
"""
import json
import os
import pathlib
import sys

BASE_DIR = pathlib.Path(__file__).resolve().parent
CACHE_PATH = BASE_DIR / "data" / "summaries.json"
MODEL = "claude-sonnet-5-5"

SYSTEM_PROMPT = """당신은 테크/반도체/AI 인프라 뉴스레터의 신규 글을 정리하는 요약 담당입니다.
입력으로 글의 제목, 부제, 설명만 주어집니다. 본문은 주어지지 않습니다.

규칙:
1. 한국어로 최대 3문장. 한두 문장이면 충분하면 짧게 씁니다.
2. 주어진 제목·부제·설명에 담긴 사실만 씁니다. 추측, 평가, 전망, 배경지식으로 내용을 보태지 않습니다.
3. 원문 문장을 그대로 옮기지 말고 자기 말로 다시 씁니다. 고유명사와 제품명은 원문 표기를 유지합니다.
4. 정보가 제목 정도뿐이면, 제목이 말해 주는 주제만 한 문장으로 씁니다.
5. 요약문만 출력합니다. 머리말, 따옴표, 목록 기호, 설명을 붙이지 않습니다."""


def _load_env_key():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def load_cache():
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_cache(cache):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(CACHE_PATH)


_client = None


def _get_client():
    """API 키가 있고 SDK 가 설치돼 있으면 클라이언트를, 아니면 None 을 돌려준다."""
    global _client
    if _client is not None:
        return _client
    key = _load_env_key()
    if not key:
        return None
    try:
        import anthropic
    except ImportError:
        print("[Warning] anthropic 패키지가 없습니다. pip install anthropic", file=sys.stderr)
        return None
    _client = anthropic.Anthropic(api_key=key)
    return _client


def generate_summary(title, subtitle="", description=""):
    """Claude 로 요약을 생성한다. 실패하면 None."""
    client = _get_client()
    if client is None:
        return None

    parts = [f"제목: {title}"]
    if subtitle:
        parts.append(f"부제: {subtitle}")
    if description and description != subtitle:
        parts.append(f"설명: {description}")

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": "\n".join(parts)}],
        )
    except Exception as e:
        print(f"[Warning] 요약 생성 실패 ({title[:40]}): {type(e).__name__}: {e}", file=sys.stderr)
        return None

    if resp.stop_reason not in ("end_turn", "stop_sequence"):
        print(f"[Warning] 요약 중단 ({title[:40]}): stop_reason={resp.stop_reason}", file=sys.stderr)
        return None
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return text or None


def get_summary(slug, title, subtitle="", description="", cache=None):
    """캐시에 있으면 그대로, 없으면 생성해서 캐시에 저장하고 돌려준다. 실패하면 None."""
    own_cache = cache is None
    if own_cache:
        cache = load_cache()
    if slug and slug in cache:
        return cache[slug]
    text = generate_summary(title, subtitle, description)
    if text and slug:
        cache[slug] = text
        if own_cache:
            save_cache(cache)
    return text
