# -*- coding: utf-8 -*-
"""
scripts/send_email.py
Reads docs/pending.json and config/email.yml, formats a clean [제목]·[핵심 요약]·[URL] email,
and sends it via Resend API (or configured Resend MCP).
Updates data/state/sent.json and docs/pending.json upon success.
"""
import os
import sys
import json
import html
import pathlib
import urllib.request
import urllib.error

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent

def load_yaml_config(path: pathlib.Path):
    """Simple parser for basic YAML key-value config."""
    config = {}
    if not path.exists():
        return config
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            val = val.strip().strip('"').strip("'")
            config[key.strip()] = val
    return config

def get_env_value(name: str) -> str:
    """환경변수, 없으면 .env 파일에서 name 값을 찾는다 (없으면 빈 문자열)."""
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

def get_resend_api_key():
    """Look for RESEND_API_KEY in environment or .env or mcp_config.json."""
    key = os.environ.get("RESEND_API_KEY")
    if key and key.strip():
        return key.strip()

    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("RESEND_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key:
                    return key

    mcp_path = BASE_DIR / "mcp_config.json"
    if mcp_path.exists():
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
            key = data.get("mcpServers", {}).get("resend", {}).get("env", {}).get("RESEND_API_KEY", "")
            if key and key.strip():
                return key.strip()
        except Exception:
            pass

    return None

DEFAULT_MAX_ITEMS = 30


def safe_url(url):
    """http(s) 링크만 허용한다. 그 밖의 스킴(javascript: 등)은 '#' 로 바꾼다."""
    return html.escape(url, quote=True) if str(url).startswith(("http://", "https://")) else "#"


def build_email_contents(pending_items, subject_prefix, remaining=0):
    count = len(pending_items)
    subject = f"{subject_prefix} 신규 업데이트 알림 ({count}건)"

    # Text version
    text_lines = [
        f"== {subject_prefix} 신규 모니터링 업데이트 ==",
        f"총 {count}건의 미발송 신규 항목이 감지되었습니다.\n"
    ]
    for idx, item in enumerate(pending_items, 1):
        title = item.get("title", "제목 없음")
        summary = item.get("summary", "")
        url = item.get("url", "")
        text_lines.append(f"[{idx}] {title}")
        text_lines.append(f"• 요약: {summary}")
        text_lines.append(f"• 원문 URL: {url}\n")

    if remaining:
        text_lines.append(f"※ 이번 메일에 담지 못한 {remaining}건은 다음 발송에 이어서 보냅니다.")
    plain_text = "\n".join(text_lines)

    # HTML version
    items_html = ""
    for idx, item in enumerate(pending_items, 1):
        title = html.escape(item.get("title", "제목 없음"))
        summary = html.escape(item.get("summary", ""))
        url = safe_url(item.get("url", "#"))
        source = html.escape(item.get("source", "SemiAnalysis"))
        detected_at = html.escape(item.get("detected_at", ""))

        items_html += f"""
        <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px 20px; margin-bottom: 16px;">
          <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase; margin-bottom: 6px;">
            #{idx} · {source} {f'· {detected_at}' if detected_at else ''}
          </div>
          <h3 style="margin: 0 0 10px 0; font-size: 16px; color: #0f172a; line-height: 1.4;">
            <a href="{url}" style="color: #2563eb; text-decoration: none;">{title}</a>
          </h3>
          <p style="margin: 0 0 12px 0; font-size: 14px; color: #334155; line-height: 1.6;">
            {summary}
          </p>
          <div>
            <a href="{url}" style="display: inline-block; font-size: 12px; font-weight: 600; color: #2563eb; background: #eff6ff; padding: 6px 12px; border-radius: 6px; text-decoration: none;">
              원문 확인하기 &rarr;
            </a>
          </div>
        </div>
        """

    remaining_html = (
        f'<p style="margin: 0; font-size: 13px; color: #64748b; text-align: center;">'
        f'이번 메일에 담지 못한 {remaining}건은 다음 발송에 이어서 보냅니다.</p>'
        if remaining else ""
    )

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #f8fafc; color: #0f172a; padding: 24px; margin: 0;">
      <div style="max-width: 680px; margin: 0 auto;">
        <div style="background: #0f172a; color: #ffffff; padding: 20px 24px; border-radius: 10px 10px 0 0;">
          <h1 style="margin: 0; font-size: 20px; font-weight: 700;">{html.escape(subject_prefix)} 신규 업데이트</h1>
          <p style="margin: 6px 0 0 0; font-size: 13px; color: #94a3b8;">미발송 신규 업데이트 총 {count}건 요약 보고서입니다.</p>
        </div>
        <div style="background: #f1f5f9; padding: 20px; border-radius: 0 0 10px 10px;">
          {items_html}
          {remaining_html}
        </div>
        <div style="text-align: center; margin-top: 20px; font-size: 12px; color: #94a3b8;">
          URL Monitor 자동 알림 시스템 · 본 메일은 원문 요약 규칙에 따라 생성되었습니다.
        </div>
      </div>
    </body>
    </html>
    """

    return subject, plain_text, html_content

def send_via_resend(api_key: str, recipient: str, subject: str, text: str, html: str, from_addr="onboarding@resend.dev"):
    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "url-monitor/1.0"
    }
    payload = {
        "from": from_addr,
        "to": [recipient],
        "subject": subject,
        "text": text,
        "html": html
    }

    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)

def main():
    email_cfg_path = BASE_DIR / "config" / "email.yml"
    pending_path = BASE_DIR / "docs" / "pending.json"
    sent_path = BASE_DIR / "data" / "state" / "sent.json"

    email_cfg = load_yaml_config(email_cfg_path)
    # 수신자는 공개 저장소에 올리지 않도록 .env 의 EMAIL_RECIPIENT 를 우선한다 (config/email.yml 은 자리표시자)
    recipient = get_env_value("EMAIL_RECIPIENT") or email_cfg.get("recipient", "")
    if not recipient or recipient.endswith("@example.com"):
        print("[Error] 수신자가 없습니다. .env 에 EMAIL_RECIPIENT=주소 를 설정하세요.", file=sys.stderr)
        return 1
    subject_prefix = email_cfg.get("subject_prefix", "[모니터링]")
    try:
        max_items = int(email_cfg.get("max_items", DEFAULT_MAX_ITEMS))
    except ValueError:
        max_items = DEFAULT_MAX_ITEMS

    if not pending_path.exists():
        print("[Info] docs/pending.json 파일이 존재하지 않습니다.")
        return

    try:
        pending_items = json.loads(pending_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Error] pending.json 파싱 실패: {e}", file=sys.stderr)
        return 1

    if not pending_items:
        print("[Info] 발송할 미발송 항목이 없습니다.")
        return

    # 한 통에 담는 건수를 제한한다 (Gmail 은 약 102KB 를 넘으면 본문을 잘라 보여준다). 나머지는 다음 실행에서 발송.
    batch = pending_items[:max_items] if max_items > 0 else pending_items
    remaining_items = pending_items[len(batch):]

    print(f"발송 대상: {recipient}")
    print(f"발송 항목 수: {len(batch)}건" + (f" (대기 {len(remaining_items)}건은 다음 발송)" if remaining_items else ""))

    api_key = get_resend_api_key()
    if not api_key:
        print("\n[Notice] RESEND_API_KEY가 설정되지 않았습니다.")
        print("Resend(https://resend.com) API 키(re_...)를 .env 또는 mcp_config.json에 설정해 주세요.")
        print("예시: .env 파일에 RESEND_API_KEY=re_your_api_key 입력")
        return 1

    subject, plain_text, html_content = build_email_contents(batch, subject_prefix, len(remaining_items))

    print(f"메일 발송 중... (제목: {subject})")
    try:
        res = send_via_resend(api_key, recipient, subject, plain_text, html_content)
        email_id = res.get("id", "알 수 없음")
        print(f"이메일 발송 성공! (ID: {email_id})")

        # Update sent.json
        sent = set(json.loads(sent_path.read_text(encoding="utf-8"))) if sent_path.exists() else set()
        for it in batch:
            u = it.get("url")
            if u:
                sent.add(u)
        sent_path.parent.mkdir(parents=True, exist_ok=True)
        sent_path.write_text(json.dumps(sorted(sent), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"data/state/sent.json 업데이트 완료: 총 {len(sent)}개 발송 기록")

        # 보낸 항목만 pending.json 에서 뺀다
        pending_path.write_text(json.dumps(remaining_items, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"docs/pending.json 갱신 완료 (남은 항목 {len(remaining_items)}건).")

    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        print(f"[Error] Resend API 호출 실패 (HTTP {e.code}): {err_msg}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[Error] 발송 실패: {e}", file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
