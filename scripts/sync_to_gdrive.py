#!/usr/bin/env python3
"""
scripts/sync_to_gdrive.py
=========================
원문·요약 md 를 로컬(output/semianalysis)과 Google Drive(config.yaml storage.drive_dir)에 맞춘다.
저장 위치 규칙은 scripts/storage.py 참조.

  python scripts/sync_to_gdrive.py --dry-run   # 무엇을 복사할지만 출력
  python scripts/sync_to_gdrive.py             # 한쪽에만 있는 파일을 반대쪽으로 복사

- 양방향이지만 추가만 한다. 어느 쪽 파일도 지우거나 덮어쓰지 않는다 (이름이 같으면 그대로 둔다).
- 로컬 → Drive: 원문은 원문 전용 폴더(storage.raw_dir, 연결 안 됐으면 <Drive>/<연도>/), 요약은 <Drive>/<연도>/ 와 <Drive>/00_핵심요약_전체/
- Drive → 로컬: 원문은 output/semianalysis/, 요약은 output/semianalysis/summary/
- docs/index.json(색인 카탈로그)이 있으면 Drive 루트 index.json 으로 복사한다 (이건 덮어쓴다).
"""
import argparse
import pathlib
import shutil
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import storage

BASE_DIR = storage.BASE_DIR
LOCAL_INDEX_JSON = BASE_DIR / "docs" / "index.json"


def _copy(src, dest, dry):
    if not dry:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def sync(dry=False):
    drive = storage.drive_dir()
    if drive is None:
        print("[!] Google Drive 폴더가 연결돼 있지 않습니다 (config/config.yaml storage.drive_dir 확인).")
        print("    Google Drive 데스크톱 클라이언트 실행과 드라이브 문자를 확인하세요.")
        return 1
    print(f"=== [원문·요약 md 동기화{' (dry-run)' if dry else ''}] Drive: {drive} ===")

    to_drive = {"원문": 0, "요약": 0}
    to_local = {"원문": 0, "요약": 0}

    # ---- 로컬 -> Drive (없는 것만)
    for p in sorted(storage.LOCAL_RAW_DIR.glob("*.md")):
        if not storage.raw_in_drive(p.name):  # 원문 전용 폴더나 연도 폴더 어디에든 있으면 건너뜀
            if dry or storage.mirror_file(p, "raw"):  # 원문 전용 폴더가 연결돼 있으면 거기로, 아니면 연도 폴더로
                to_drive["원문"] += 1
    if storage.LOCAL_SUMMARY_DIR.is_dir():
        for p in sorted(storage.LOCAL_SUMMARY_DIR.glob("*.md")):
            for dest in (drive / storage.year_of(p.name) / p.name, drive / storage.SUMMARY_FOLDER / p.name):
                if not dest.exists():
                    _copy(p, dest, dry); to_drive["요약"] += 1

    # ---- Drive -> 로컬 (없는 것만)
    local_raw = {p.name for p in storage.LOCAL_RAW_DIR.glob("*.md")}
    for p in storage.raw_files():
        if p.name not in local_raw and storage.drive_dir() and drive in p.parents:
            _copy(p, storage.LOCAL_RAW_DIR / p.name, dry); to_local["원문"] += 1
    local_sum = {p.name for p in storage.LOCAL_SUMMARY_DIR.glob("*.md")} if storage.LOCAL_SUMMARY_DIR.is_dir() else set()
    sum_dir = drive / storage.SUMMARY_FOLDER
    if sum_dir.is_dir():
        for p in sorted(sum_dir.glob("*.md")):
            if p.name not in local_sum:
                _copy(p, storage.LOCAL_SUMMARY_DIR / p.name, dry); to_local["요약"] += 1

    verb = "복사 예정" if dry else "복사 완료"
    print(f"[V] 로컬 -> Drive {verb}: 원문 {to_drive['원문']}개, 요약 {to_drive['요약']}개 (연도 폴더·통합 폴더 합산)")
    print(f"[V] Drive -> 로컬 {verb}: 원문 {to_local['원문']}개, 요약 {to_local['요약']}개")

    if LOCAL_INDEX_JSON.exists() and not dry:
        shutil.copy2(LOCAL_INDEX_JSON, drive / "index.json")
        print("[V] 색인 카탈로그 index.json 복사")
    print(f"[=] 현재: 원문 {len(storage.raw_files())}개, 요약 {len(storage.summary_files())}개")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="복사하지 않고 대상만 출력")
    sys.exit(sync(ap.parse_args().dry_run))
