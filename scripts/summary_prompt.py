"""
summary_prompt.py
요약 프롬프트(semianalysis_summary_prompt.md) 로딩과 수집 범위 판정을 한곳에서 관리합니다.
url_monitor_pipeline.py, batch_summarize.py 가 함께 사용합니다.
"""

import pathlib
import re

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
PROMPT_FILE = BASE_DIR / "semianalysis_summary_prompt.md"

PAYWALL_MARKERS = ("This post is for paid subscribers",)


def load_system_prompt(path: pathlib.Path = PROMPT_FILE) -> str:
    """프롬프트 파일에서 첫 번째 `---` 줄과 마지막 `---` 줄 사이(시스템 프롬프트 본문)만 반환한다.

    파일 앞의 안내문과 맨 뒤 "운영 메모"는 모델에 보내지 않는다.
    구분선이 2개 미만이면 파일 전체를 그대로 쓴다.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    seps = [i for i, line in enumerate(lines) if line.strip() == "---"]
    if len(seps) < 2:
        return text.strip()
    return "\n".join(lines[seps[0] + 1:seps[-1]]).strip()


def _frontmatter(text: str) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}
    fields = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip().strip('"')
    return fields


def collection_scope(text: str) -> str:
    """원문 md(frontmatter 포함)에서 수집 범위를 판정한다: 전문 / 유료 미리보기(일부) / 확인 필요."""
    fm = _frontmatter(text)
    content = fm.get("content", "")
    audience = fm.get("audience", "")
    if content == "paid-preview" or audience == "only_paid" or any(m in text for m in PAYWALL_MARKERS):
        return "유료 미리보기(일부)"
    if content == "free" or audience == "everyone":
        return "전문"
    return "확인 필요"


def scope_line(text: str) -> str:
    """입력 앞에 붙일 수집 범위 안내 한 줄."""
    return f"[수집 범위]: {collection_scope(text)} (frontmatter·paywall 문구로 코드가 판정한 값)"
