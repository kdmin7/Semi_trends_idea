#!/usr/bin/env python3
"""
scripts/agy_session_start.py
============================
Antigravity(agy) 세션 시작(SessionStart) 시 자동으로 최신 아티클을 크롤링하고
결과를 Antigravity 세션의 systemMessage로 주입하는 훅 스크립트.
"""

import sys
import json
import pathlib
import subprocess

# 윈도우 UTF-8 입출력 보정
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
PIPELINE_SCRIPT = BASE_DIR / "scripts" / "url_monitor_pipeline.py"


def main():
    output_lines = []
    return_code = 0

    try:
        if not PIPELINE_SCRIPT.exists():
            msg = f"[!] 파이프라인 스크립트를 찾을 수 없습니다: {PIPELINE_SCRIPT}"
            output_lines.append(msg)
        else:
            proc = subprocess.run(
                [sys.executable, str(PIPELINE_SCRIPT), "--check-latest"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180
            )
            return_code = proc.returncode
            if proc.stdout:
                output_lines.append(proc.stdout.strip())
            if proc.stderr:
                output_lines.append(f"[stderr]\n{proc.stderr.strip()}")
    except subprocess.TimeoutExpired:
        output_lines.append("[!] 크롤링 파이프라인 실행 시간 초과 (180초)")
        return_code = 1
    except Exception as e:
        output_lines.append(f"[!] 세션 시작 크롤링 실행 중 예외 발생: {e}")
        return_code = 1

    combined_text = "\n".join(output_lines).strip()
    if not combined_text:
        combined_text = "[V] 최신 아티클 확인 완료 (신규 글 없음)"

    # Antigravity SessionStart 훅 규격(JSON)에 맞게 stdout 출력
    result_payload = {
        "injectSteps": [
            {
                "systemMessage": {
                    "systemMessage": f"[agy SessionStart 자동 크롤링]\n{combined_text}"
                }
            }
        ]
    }

    print(json.dumps(result_payload, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
