"""
run_daily.py
하루 한 번 실행되는 전체 파이프라인 (Windows 작업 스케줄러에서 호출).

  1. monitor.py                 config/config.yaml 의 sources 에서 새 글 수집 → output/results.json,
                                 data/items/*.jsonl, data/state/seen.json
  1a. scripts/fetch_archive_md.py    최근 14일 신규 글의 원문 md 를 Firecrawl 로 받아 저장 (글당 약 1크레딧, 이미 있는 글은 건너뜀).
                                 저장 위치는 scripts/storage.py (원문 전용 Drive 폴더 → 없으면 Drive 연도 폴더, 로컬에도 사본). 실패해도 이후 단계는 계속한다.
  1b. scripts/backfill_summaries.py  수집 때 요약에 실패한 글을 Claude API 로 다시 요약 (키가 없으면 건너뜀, 하루 최대 20건)
  2. scripts/dedup.py           data/items/*.jsonl 중복 URL 제거
  3. scripts/build_dashboard.py docs/items.json 갱신
  3b. build_site.py              assets/data.js 생성 + HTML·assets 를 docs/ 로 동기화 (사이트 빌드는 여기서 한 번만)
  4. scripts/pending.py         sent.json 에 없는 항목만 docs/pending.json 으로
  5. scripts/send_email.py      Resend 로 다이제스트 발송 (성공 시 sent.json 갱신, 새 항목 없으면 건너뜀)
  6. scripts/sync_to_gdrive.py  로컬에만 있는 원문·요약 md 를 Drive 로 보충 (Drive 가 아직 연결 전이었던 날을 메운다). 실패해도 무방.

실행 로그는 logs/daily_YYYY-MM-DD.log 에 남는다. 한 단계라도 실패하면 이후 단계는 실행하지 않는다.
"""
import os
import subprocess
import sys
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")

# (이름, 명령, 실패해도 계속할지) — 세 번째 값이 True 인 단계는 실패해도 이후 단계를 막지 않는다
STEPS = [
    ("수집", "monitor.py"),
    ("원문 md 수집", os.path.join("scripts", "fetch_archive_md.py") + " --days 14", True),
    ("요약 보강", os.path.join("scripts", "backfill_summaries.py") + " --limit 20 --no-build"),
    ("중복 제거", os.path.join("scripts", "dedup.py")),
    ("대시보드 데이터", os.path.join("scripts", "build_dashboard.py")),
    ("사이트 빌드", "build_site.py"),
    ("미발송 목록", os.path.join("scripts", "pending.py")),
    ("메일 발송", os.path.join("scripts", "send_email.py")),
    ("Drive 동기화", os.path.join("scripts", "sync_to_gdrive.py"), True),
]


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"daily_{datetime.now():%Y-%m-%d}.log")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

    with open(log_path, "a", encoding="utf-8") as log:
        def write(msg):
            line = f"[{datetime.now():%H:%M:%S}] {msg}"
            print(line)
            log.write(line + "\n")
            log.flush()

        write("===== 일일 실행 시작 =====")
        for name, script, *opt in STEPS:
            soft = bool(opt and opt[0])
            write(f"▶ {name} ({script})")
            proc = subprocess.run(
                [sys.executable, *script.split()],
                cwd=BASE_DIR, env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            for stream in (proc.stdout, proc.stderr):
                if stream.strip():
                    log.write(stream.rstrip() + "\n")
            if proc.returncode != 0 and soft:
                write(f"⚠ {name} 실패 (exit {proc.returncode}) — 계속 진행")
                continue
            if proc.returncode != 0:
                write(f"✖ {name} 실패 (exit {proc.returncode}) — 이후 단계 중단")
                if proc.stderr.strip():
                    print(proc.stderr.rstrip(), file=sys.stderr)
                return proc.returncode
            write(f"✔ {name} 완료")
        write("===== 일일 실행 완료 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
