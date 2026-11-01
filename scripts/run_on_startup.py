#!/usr/bin/env python3
"""
scripts/run_on_startup.py
=========================
PC 부팅(로그온) 시 자동 실행되는 모니터링 가드 스크립트.

동작 방식:
1. logs/startup_last_run.txt 확인
2. 마지막 실행으로부터 지정된 시간(기본 4시간)이 지났는지 검사
3. 충족 시 url_monitor_pipeline.py --check-latest 실행
4. 실행 결과 및 시간을 기록
"""

import os
import sys
import subprocess
import pathlib
from datetime import datetime, timedelta

# UTF-8 출력 보정 (Windows)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"
LAST_RUN_FILE = LOGS_DIR / "startup_last_run.txt"
LOG_FILE = LOGS_DIR / "startup_run.log"

MIN_INTERVAL_HOURS = 4  # 최소 실행 간격 (시간 단위, 필요 시 변경 가능)


def log_message(msg: str):
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{timestamp} {msg}"
    print(line)
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"[WARN] 로그 파일 기록 실패: {e}", file=sys.stderr)


def should_run() -> bool:
    if not LAST_RUN_FILE.exists():
        log_message("이전 실행 기록이 없습니다. 최초 실행으로 판정합니다.")
        return True

    try:
        content = LAST_RUN_FILE.read_text(encoding="utf-8").strip()
        if not content:
            return True
        last_run = datetime.fromisoformat(content)
        elapsed = datetime.now() - last_run
        threshold = timedelta(hours=MIN_INTERVAL_HOURS)

        if elapsed >= threshold:
            log_message(f"마지막 실행 후 {elapsed.total_seconds() / 3600:.1f}시간 경과 (기준: {MIN_INTERVAL_HOURS}시간). 실행 진행.")
            return True
        else:
            remaining = threshold - elapsed
            log_message(f"마지막 실행 후 {elapsed.total_seconds() / 3600:.1f}시간 경과. 아직 {remaining.total_seconds() / 3600:.1f}시간 남았습니다. 스킵합니다.")
            return False
    except Exception as e:
        log_message(f"[WARN] 이전 실행 기록 파싱 오류 ({e}). 안전을 위해 실행합니다.")
        return True


def update_last_run():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    LAST_RUN_FILE.write_text(datetime.now().isoformat(), encoding="utf-8")


def main():
    log_message("=== PC 시작 자동 크롤링 가드 실행 ===")

    # 네트워크 대기 (부팅 직후 인터넷 연결 안정화 대기)
    log_message("인터넷 연결 안정화를 위해 10초 대기...")
    import time
    time.sleep(10)

    if not should_run():
        log_message("실행 요건 미충족으로 작업을 마칩니다.")
        return

    pipeline_script = BASE_DIR / "scripts" / "url_monitor_pipeline.py"
    if not pipeline_script.exists():
        log_message(f"[ERROR] 파이프라인 스크립트를 찾을 수 없음: {pipeline_script}")
        sys.exit(1)

    log_message(f"크롤링 파이프라인 시작: {pipeline_script}")
    try:
        cmd = [sys.executable, str(pipeline_script), "--check-latest"]
        res = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace")

        if res.stdout:
            print(res.stdout)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(res.stdout + "\n")

        if res.returncode == 0:
            update_last_run()
            log_message("[SUCCESS] 크롤링 파이프라인 성공 완료. 마지막 실행 시각 갱신됨.")
        else:
            log_message(f"[ERROR] 파이프라인 실행 중 오류 발생 (종료 코드 {res.returncode}):\n{res.stderr}")
    except Exception as e:
        log_message(f"[EXCEPTION] 실행 실패: {e}")


if __name__ == "__main__":
    main()
