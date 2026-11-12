#!/usr/bin/env python3
"""
scripts/run_on_startup.py
=========================
PC 부팅(로그온) 시 자동 실행되는 모니터링 가드 스크립트.

동작 방식:
1. logs/startup_last_run.txt 확인
2. 마지막 실행으로부터 지정된 시간(기본 4시간)이 지났는지 검사
3. 충족 시 url_monitor_pipeline.py --check-latest 실행
4. 신규 아티클 감지 및 빌드 변경 사항이 있을 경우:
   -> 자동으로 git commit 및 git push 실행하여 GitHub Pages 최신화!
5. 실행 결과 및 시간을 logs/startup_run.log 에 기록 (표준 logging, 레벨별 구분)
"""

import logging
import os
import sys
import subprocess
import pathlib
import time
from datetime import datetime, timedelta

# UTF-8 출력 보정 (Windows)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"
LAST_RUN_FILE = LOGS_DIR / "startup_last_run.txt"
LOG_FILE = LOGS_DIR / "startup_run.log"

MIN_INTERVAL_HOURS = 4  # 최소 실행 간격 (시간 단위)

log = logging.getLogger("run_on_startup")


# 하위 Python 프로세스가 cp949 가 아닌 UTF-8 로 출력하도록 강제한다 (로그 한글 깨짐 방지)
CHILD_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")


def _run(cmd, **kwargs):
    """BASE_DIR 에서 실행하고 stdout/stderr 를 UTF-8 텍스트로 캡처한다."""
    return subprocess.run(
        cmd,
        cwd=str(BASE_DIR),
        env=CHILD_ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )


def setup_logging() -> None:
    """콘솔 + logs/startup_run.log(UTF-8, 추가 기록) 양쪽에 같은 형식으로 남긴다."""
    log.setLevel(logging.INFO)
    log.propagate = False
    if log.handlers:  # 중복 호출 방지
        return

    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    log.addHandler(console)

    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(fmt)
        log.addHandler(file_handler)
    except OSError as e:
        # 파일 로그를 못 열어도 콘솔 로그로 계속 실행한다
        log.warning("로그 파일 기록 실패: %s", e)


def should_run() -> bool:
    if not LAST_RUN_FILE.exists():
        log.info("이전 실행 기록이 없습니다. 최초 실행으로 판정합니다.")
        return True

    try:
        content = LAST_RUN_FILE.read_text(encoding="utf-8").strip()
        if not content:
            return True
        last_run = datetime.fromisoformat(content)
        elapsed = datetime.now() - last_run
        threshold = timedelta(hours=MIN_INTERVAL_HOURS)

        if elapsed >= threshold:
            log.info("마지막 실행 후 %.1f시간 경과 (기준: %d시간). 실행 진행.",
                     elapsed.total_seconds() / 3600, MIN_INTERVAL_HOURS)
            return True
        remaining = threshold - elapsed
        log.info("마지막 실행 후 %.1f시간 경과. 아직 %.1f시간 남았습니다. 스킵합니다.",
                 elapsed.total_seconds() / 3600, remaining.total_seconds() / 3600)
        return False
    except Exception as e:
        log.warning("이전 실행 기록 파싱 오류 (%s). 안전을 위해 실행합니다.", e)
        return True


def update_last_run():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    LAST_RUN_FILE.write_text(datetime.now().isoformat(), encoding="utf-8")


def send_pending_email():
    """pending.json 의 미발송 항목을 메일로 보낸다. 실패해도 이후 단계(git 동기화)는 막지 않는다."""
    script = BASE_DIR / "scripts" / "send_email.py"
    if not script.exists():
        log.warning("send_email.py 를 찾을 수 없어 메일 발송을 건너뜁니다: %s", script)
        return
    try:
        res = _run([sys.executable, str(script)])
        if res.stdout.strip():
            log.info("[MAIL stdout]\n%s", res.stdout.rstrip())
        if res.returncode == 0:
            log.info("[MAIL] 메일 발송 단계 완료")
        else:
            log.error("[MAIL] 메일 발송 실패 (종료 코드 %d)\n%s", res.returncode, res.stderr.rstrip())
    except Exception:
        log.exception("[MAIL] 메일 발송 중 오류 발생")


def auto_git_sync():
    """크롤링 및 빌드 결과물이 변경되었을 경우 GitHub에 자동 커밋 & 푸시"""
    try:
        # 1. git add — 먼저 스테이징한 뒤, 실제로 커밋될 대상 기준으로 변경 여부를 판단한다
        #    (logs/ 는 .gitignore 로 이미 제외되므로 별도 필터가 필요 없다)
        _run(["git", "add", "."], check=True)

        staged = _run(["git", "diff", "--cached", "--name-only"], check=True)
        staged_files = [line for line in staged.stdout.splitlines() if line.strip()]

        if not staged_files:
            log.info("[GIT] 새로 추가된 아티클이나 사이트 변경 사항이 없어 푸시를 건너뜁니다.")
            return

        log.info("[GIT] 커밋 대상 파일 %d개. GitHub 자동 동기화를 시작합니다...", len(staged_files))

        # 2. git commit
        today_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        commit_msg = f"auto: 신규 아티클 크롤링 및 사이트 자동 업데이트 ({today_str})"
        _run(["git", "commit", "-m", commit_msg], check=True)
        log.info("[GIT] 커밋 완료: '%s'", commit_msg)

        # 3. git push
        push_res = _run(["git", "push", "origin", "public:main"])
        if push_res.returncode == 0:
            log.info("[GIT] GitHub(main 브랜치)에 자동 업로드 완료!")
        else:
            log.error("[GIT] GitHub 푸시 실패 (종료 코드 %d):\n%s",
                      push_res.returncode, push_res.stderr.rstrip())

    except Exception:
        # 스택 트레이스까지 함께 기록
        log.exception("[GIT] 자동 Git 동기화 중 오류 발생")


def main():
    setup_logging()
    log.info("=== PC 시작 자동 크롤링 가드 실행 ===")

    if not should_run():
        log.info("실행 요건 미충족으로 작업을 마칩니다.")
        return

    # 네트워크 대기 (부팅 직후 인터넷 연결 안정화 대기) — 실제로 실행할 때만 기다린다
    log.info("인터넷 연결 안정화를 위해 10초 대기...")
    time.sleep(10)

    pipeline_script = BASE_DIR / "scripts" / "url_monitor_pipeline.py"
    if not pipeline_script.exists():
        log.error("파이프라인 스크립트를 찾을 수 없음: %s", pipeline_script)
        sys.exit(1)

    log.info("크롤링 파이프라인 시작: %s", pipeline_script)
    try:
        cmd = [sys.executable, str(pipeline_script), "--check-latest"]
        res = _run(cmd)

        if res.stdout.strip():
            log.info("[pipeline stdout]\n%s", res.stdout.rstrip())
        if res.stderr.strip():
            # 성공(0)이어도 경고성 stderr 가 있을 수 있으므로 항상 남긴다
            log.warning("[pipeline stderr]\n%s", res.stderr.rstrip())

        if res.returncode == 0:
            update_last_run()
            log.info("크롤링 파이프라인 성공 완료. 마지막 실행 시각 갱신됨.")

            # 신규 글 알림 메일 발송 (pending.json 갱신분이 커밋에 함께 들어가도록 git 동기화보다 먼저)
            send_pending_email()

            # 신규 글이 있을 때 GitHub에 자동 커밋 & 푸시
            auto_git_sync()
        else:
            log.error("파이프라인 실행 중 오류 발생 (종료 코드 %d)", res.returncode)
    except Exception:
        log.exception("파이프라인 실행 실패")


if __name__ == "__main__":
    main()
