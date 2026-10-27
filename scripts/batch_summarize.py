"""
batch_summarize_semianalysis.py
SemiAnalysis 포스트 MD 파일 전체를 읽어 semianalysis_summary_prompt.md 규칙에 따라
Gemini 3.8 Flash 모델로 고품질 요약 노트를 생성하고 output/semianalysis/summary 에 저장합니다.

[무료 티어 제어 원칙]:
1. 모델: gemini-3.8-flash (일일 1,500회 / 분당 15회 / 분당 100만 TPM)
2. 분당 요청 수(RPM): 엄격히 분당 4회 이하로 제한 (요청 1회당 15초 강제 쿨다운)
3. 롤링 윈도우 슬라이딩 레이트 리미터: 60초 내 요청 횟수 4회 이하, 토큰 150,000 이하 유지
4. 429 감지 시: 지수 백오프(60초~120초) 대기 후 재시도
"""

import os
import sys
import time
import glob
import pathlib
import collections
import traceback
import argparse
import json
import datetime
from google import genai
from google.genai import types

import storage  # 원문·요약 저장 위치(Drive + 로컬) 한곳 관리

# Windows cp949 콘솔 인코딩 에러 방지
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
SEMI_DIR = BASE_DIR / "output" / "semianalysis"
SUMMARY_DIR = SEMI_DIR / "summary"
PROMPT_FILE = BASE_DIR / "semianalysis_summary_prompt.md"
ENV_FILE = BASE_DIR / ".env"

PRIMARY_MODEL = "gemini-3.8-flash"
FALLBACK_MODEL = "gemini-3.7-flash"

# 유료 티어(Pay-as-you-go) 고속 처리 설정 (1,000 RPM / 4M TPM 대비 넉넉하고 안전한 속도)
SAFE_MAX_RPM = 60
SAFE_MAX_TPM = 2000000

class StrictRateLimiter:
    """무료 티어 60초 슬라이딩 윈도우 내 요청 수(RPM) 및 토큰 수(TPM)를 사전 통제하는 클래스"""
    def __init__(self, max_rpm=SAFE_MAX_RPM, max_tpm=SAFE_MAX_TPM):
        self.max_rpm = max_rpm
        self.max_tpm = max_tpm
        self.history = collections.deque() # (timestamp, est_tokens)

    def _cleanup(self, now):
        while self.history and (now - self.history[0][0]) > 60.0:
            self.history.popleft()

    def wait_if_needed(self, est_tokens):
        while True:
            now = time.time()
            self._cleanup(now)
            current_reqs = len(self.history)
            current_tokens = sum(t for _, t in self.history)

            # 분당 한도 초과 위험이 있을 경우 슬라이딩 윈도우가 풀릴 때까지 대기
            if (current_reqs + 1 > self.max_rpm) or (current_tokens + est_tokens > self.max_tpm):
                oldest_time = self.history[0][0]
                sleep_sec = max(2.0, 60.5 - (now - oldest_time))
                print(f"    [RateLimiter] 무료 티어 한도 사전 방어: 60초 윈도우 내 {current_reqs}회/{self.max_rpm} 요청, {current_tokens:,}/{self.max_tpm:,} 토큰 점유 -> {sleep_sec:.1f}초 안전 대기...", flush=True)
                time.sleep(sleep_sec)
            else:
                break

    def record(self, est_tokens):
        now = time.time()
        self.history.append((now, est_tokens))

def load_env_api_key():
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

def load_system_prompt():
    return PROMPT_FILE.read_text(encoding="utf-8")

def main():
    parser = argparse.ArgumentParser(description="SemiAnalysis batch summarizer")
    parser.add_argument("--stop-on-429", action="store_true", help="Stop immediately and alert if 429 rate limit is encountered")
    parser.add_argument("--status-file", type=str, default=str(BASE_DIR / "data" / "state" / "batch_status.json"), help="Status file path")
    args = parser.parse_args()

    api_key = load_env_api_key()
    if not api_key:
        print("ERROR: GEMINI_API_KEY not found in .env or environment.", file=sys.stderr, flush=True)
        sys.exit(1)

    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    status_file_path = pathlib.Path(args.status_file)
    status_file_path.parent.mkdir(parents=True, exist_ok=True)

    def write_status(data):
        try:
            status_file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    system_instruction = load_system_prompt()
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=60000))

    # 311개 대상 파일 목록 (최신순)
    all_files = sorted(storage.raw_files(), key=lambda p: p.name, reverse=True)  # Drive + 로컬
    total_count = len(all_files)
    print(f"[*] Found {total_count} files (Drive: {storage.drive_dir() or '미연결, 로컬만'})", flush=True)

    success_count = 0
    skip_count = 0
    fail_count = 0

    rate_limiter = StrictRateLimiter(max_rpm=SAFE_MAX_RPM, max_tpm=SAFE_MAX_TPM)

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        thinking_config=types.ThinkingConfig(thinking_budget=2048),
        temperature=0.2,
    )

    for idx, file_path in enumerate(all_files, 1):
        target_name = f"{file_path.stem}_summary.md"
        if storage.summary_exists(target_name, 1000):
            print(f"[{idx}/{total_count}] Already summarized: {target_name} (Skipping)", flush=True)
            skip_count += 1
            continue

        content = file_path.read_text(encoding="utf-8", errors="replace")

        # 토큰 사전 추정 (영문 4자당 1토큰 + 사고 버짓 2048 + 프롬프트 1000)
        est_tokens = int(len(content) / 3.5) + 3500
        print(f"[{idx}/{total_count}] Processing: {file_path.name} (추정 토큰: {est_tokens:,}) ...", flush=True)

        prompt = f"""다음 SemiAnalysis 게시물 원본 전문을 읽고 시스템 지침(작성 규칙 및 10대 출력 형식)에 따라 정확한 읽기 노트를 작성하라.

[입력 파일명]: {file_path.name}

--- 원문 내용 ---
{content}
"""

        success = False
        models_to_try = [PRIMARY_MODEL, FALLBACK_MODEL]

        for model_candidate in models_to_try:
            if success:
                break
            for attempt in range(1, 4):
                # 호출 전 반드시 무료 티어 한도 사전 통제
                rate_limiter.wait_if_needed(est_tokens)

                try:
                    response = client.models.generate_content(
                        model=model_candidate,
                        contents=prompt,
                        config=config,
                    )
                    summary_text = response.text.strip()
                    if summary_text:
                        storage.save_summary(target_name, summary_text, overwrite=True)  # Drive + 로컬
                        print(f"    -> Saved ({len(summary_text)} chars) via {model_candidate} to {target_name}", flush=True)
                        rate_limiter.record(est_tokens)
                        success = True
                        success_count += 1
                        write_status({
                            "status": "RUNNING",
                            "last_completed_index": idx,
                            "total_files": total_count,
                            "success_count": success_count,
                            "skip_count": skip_count,
                            "last_file": file_path.name,
                            "timestamp": datetime.datetime.now().isoformat()
                        })
                        # 유료 티어 최적 간격 (1초)
                        time.sleep(1.0)
                        break
                    else:
                        print(f"    [!] Empty response from {model_candidate} (attempt {attempt})", flush=True)
                        time.sleep(5)
                except Exception as e:
                    err_str = str(e)
                    print(f"    [!] {model_candidate} attempt {attempt} error: {err_str[:120]}...", flush=True)
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                        alert_msg = (
                            f"\n{'='*60}\n"
                            f"[ALERT] Gemini 무료 티어 분당 한도 초과 감지 (429 RESOURCE_EXHAUSTED)!\n"
                            f"- 대상 파일: [{idx}/{total_count}] {file_path.name}\n"
                            f"- 사용 모델: {model_candidate} (시도 {attempt}/3)\n"
                            f"- 상세 오류: {err_str[:200]}\n"
                            f"- 시각: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"{'='*60}\n"
                        )
                        print(alert_msg, flush=True)
                        write_status({
                            "status": "RATE_LIMIT_EXCEEDED",
                            "file_index": idx,
                            "total_files": total_count,
                            "file_name": file_path.name,
                            "model": model_candidate,
                            "attempt": attempt,
                            "error": err_str,
                            "timestamp": datetime.datetime.now().isoformat()
                        })
                        if args.stop_on_429:
                            print("[*] --stop-on-429 설정에 따라 분당 한도 도달 즉시 작업을 일시 중지하고 알림을 위해 종료합니다.", flush=True)
                            sys.exit(42)
                        print("    [!] 429 감지: 분당 쿼터 리셋을 위해 60초 대기...", flush=True)
                        time.sleep(60.0)
                    elif "503" in err_str:
                        print(f"    [!] 503 서버 과부하 감지 (시도 {attempt}/3): 15초 대기 후 재시도...", flush=True)
                        time.sleep(15.0)
                    elif "timeout" in err_str.lower() or "timed out" in err_str.lower():
                        print(f"    [!] 60초 타임아웃 감지 (시도 {attempt}/3): 10초 대기 후 재시도...", flush=True)
                        time.sleep(10.0)
                    else:
                        time.sleep(5.0)

        if not success:
            print(f"    [X] Failed to summarize {file_path.name}", flush=True)
            fail_count += 1

    print("\n" + "="*50, flush=True)
    print(f"Batch completed! Total: {total_count}, Success: {success_count}, Skipped: {skip_count}, Failed: {fail_count}", flush=True)
    print("="*50, flush=True)
    write_status({
        "status": "COMPLETED",
        "total_files": total_count,
        "success_count": success_count,
        "skip_count": skip_count,
        "fail_count": fail_count,
        "timestamp": datetime.datetime.now().isoformat()
    })

if __name__ == "__main__":
    main()
