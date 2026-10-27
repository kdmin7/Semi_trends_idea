#!/usr/bin/env python3
"""
scripts/storage.py
==================
SemiAnalysis 원문 md / 요약 md 의 저장 위치를 한곳에서 정한다.

  기본 저장소  config/config.yaml 의 storage.drive_dir (Google Drive, 예: G:/내 드라이브/SemiAnalysis)
  로컬 사본    output/semianalysis/ (원문), output/semianalysis/summary/ (요약)  ← 지우지 않고 유지

Drive 폴더 구조 (기존 sync_to_gdrive.py 와 같은 구조):
  <drive_dir>/<YYYY>/<이름>.md            원문 (연도별, 같은 폴더에 요약도 나란히 둔다)
  <drive_dir>/<YYYY>/<이름>_summary.md    요약
  <drive_dir>/00_핵심요약_전체/<이름>_summary.md   요약 통합 폴더 (사이트 빌드가 읽는 곳)

규칙
  - 저장은 Drive 와 로컬 양쪽에 쓴다. Drive 가 연결돼 있지 않으면 로컬만 쓰고 경고한다(실행은 멈추지 않는다).
  - 읽기는 Drive 와 로컬을 합쳐서(같은 이름이면 Drive 우선) 본다.
  - 파일을 지우지 않는다. 이미 있는 파일은 덮어쓰지 않는다(요약은 save_summary(overwrite=True)로만).
  - 환경변수 SEMIANALYSIS_DRIVE_DIR 이 있으면 config 보다 우선한다. 빈 값이면 Drive 를 쓰지 않는다.

원문 전용 폴더(storage.raw_dir, 환경변수 SEMIANALYSIS_RAW_DIR)
  - 설정돼 있고 연결돼 있으면 새 원문 md 는 <raw_dir>/<YYYY-MM-DD_제목>.md (연도 폴더 없이 평평하게)로 저장한다.
  - 설정이 없거나 연결돼 있지 않으면 위의 Drive 연도 폴더로 폴백한다 (경고 출력).
  - 새 원문 이름은 YYYY-MM-DD_slug.md (예: 2026-10-15_asml-is-building-a-hybrid-bonding.md). 번호 접두어는 쓰지 않는다.
    옛 NNN_YYYY-MM-DD_slug.md 이름도 같은 글로 인식한다(로컬 사본 등).
  - 이 폴더에는 같은 이름 형식의 기존 카탈로그 메모 파일(약 2KB)이 함께 있어 이름만으로는 원문과 구분되지 않는다.
    그래서 이 폴더는 '저장 대상'으로만 쓰고, 원문 입력(raw_files)은 로컬 output/semianalysis 사본을 본다.
"""
import os
import pathlib
import re
import shutil
import sys

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
LOCAL_RAW_DIR = BASE_DIR / "output" / "semianalysis"
LOCAL_SUMMARY_DIR = LOCAL_RAW_DIR / "summary"
SUMMARY_FOLDER = "00_핵심요약_전체"
SUMMARY_SUFFIX = "_summary.md"
DATED_RE = re.compile(r"^(?:\d+_)?(\d{4}-\d{2}-\d{2})_(.+)\.md$")  # YYYY-MM-DD_slug.md (옛 NNN_ 접두어는 있어도 무시)
DATE_RE = re.compile(r"(\d{4})-\d{2}-\d{2}")
STUB_MAX_BYTES = 4000  # 원문 전용 폴더의 기존 카탈로그 메모 파일(약 2KB)은 원문이 아니다. 이보다 작은 파일은 '이미 받은 원문'으로 세지 않는다


def _warn(msg):
    print(f"[storage] {msg}", file=sys.stderr)


def _config_value(key):
    """config.yaml 의 storage 블록에서 key 값을 읽는다 (PyYAML 없이)."""
    if not CONFIG_PATH.exists():
        return ""
    section = None
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            if not raw.startswith((" ", "\t")):
                section = line.strip().rstrip(":") if line.strip().endswith(":") else None
                continue
            body = line.strip()
            if section == "storage" and body.startswith(key + ":"):
                return body.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def drive_dir():
    """연결된 Drive 저장 폴더(Path). 설정이 없거나 연결돼 있지 않으면 None."""
    value = os.environ.get("SEMIANALYSIS_DRIVE_DIR")
    if value is None:
        value = _config_value("drive_dir")
    if not value:
        return None
    path = pathlib.Path(value)
    return path if path.is_dir() else None


def raw_archive_dir():
    """원문 전용 폴더(Path). 설정이 없거나 연결돼 있지 않으면 None."""
    value = os.environ.get("SEMIANALYSIS_RAW_DIR")
    if value is None:
        value = _config_value("raw_dir")
    if not value:
        return None
    path = pathlib.Path(value)
    return path if path.is_dir() else None


def raw_dir_configured():
    value = os.environ.get("SEMIANALYSIS_RAW_DIR")
    if value is None:
        value = _config_value("raw_dir")
    return bool(value)


def year_of(filename):
    """파일 이름의 발행 연도 (YYYY-MM-DD_… 와 옛 NNN_YYYY-MM-DD_… 둘 다)."""
    m = DATE_RE.search(pathlib.Path(filename).name)
    return m.group(1) if m else "기타"


def _names_in(*dirs):
    for d in dirs:
        if d and pathlib.Path(d).is_dir():
            for p in pathlib.Path(d).iterdir():
                yield p.name


def raw_key_exists(date, slug):
    """같은 날짜·slug 의 원문이 이미 저장돼 있는지 (옛 NNN_ 접두어가 있어도 같은 글로 본다).
    카탈로그 메모 파일(STUB_MAX_BYTES 이하)은 원문이 아니므로 제외한다."""
    d = drive_dir()
    year_dir = (d / date[:4]) if d else None
    for folder in (raw_archive_dir(), LOCAL_RAW_DIR, year_dir):
        if not folder or not pathlib.Path(folder).is_dir():
            continue
        for p in pathlib.Path(folder).iterdir():
            m = DATED_RE.match(p.name)
            if m and m.group(1) == date and m.group(2) == slug:
                try:
                    if p.stat().st_size > STUB_MAX_BYTES:
                        return True
                except OSError:
                    continue
    return False


def is_summary(filename):
    return pathlib.Path(filename).name.endswith(SUMMARY_SUFFIX)


# ---------------------------------------------------------------- 쓰기
def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def raw_in_drive(filename):
    """원문이 Drive(원문 전용 폴더 또는 연도 폴더)에 이미 있는지."""
    ra = raw_archive_dir()
    if ra and (ra / filename).exists():
        return True
    d = drive_dir()
    return bool(d and (d / year_of(filename) / filename).exists())


def raw_exists(filename):
    return raw_in_drive(filename) or (LOCAL_RAW_DIR / filename).exists()


def summary_exists(filename, min_size=0):
    cands = [LOCAL_SUMMARY_DIR / filename]
    d = drive_dir()
    if d:
        cands.append(d / SUMMARY_FOLDER / filename)
    return any(p.exists() and p.stat().st_size > min_size for p in cands)


def save_raw(filename, text):
    """원문 md 를 Drive(연도 폴더)와 로컬에 저장한다. 이미 있으면 덮어쓰지 않는다. 쓴 경로 목록을 돌려준다."""
    written = []
    local = LOCAL_RAW_DIR / filename
    if not local.exists():
        _write(local, text)
        written.append(local)
    ra = raw_archive_dir()
    d = drive_dir()
    if ra is not None:
        target = ra / filename
    elif d is not None:
        if raw_dir_configured():
            _warn("원문 전용 폴더(storage.raw_dir)가 연결돼 있지 않아 Drive 연도 폴더로 저장합니다.")
        target = d / year_of(filename) / filename
    else:
        _warn("Drive 가 연결돼 있지 않아 로컬에만 저장했습니다 (나중에 sync_to_gdrive.py 로 맞춥니다).")
        return written
    try:
        if not target.exists():
            _write(target, text)
            written.append(target)
    except OSError as e:
        _warn(f"Drive 저장 실패, 로컬 저장은 유지: {e}")
    return written


def save_summary(filename, text, overwrite=False):
    """요약 md 를 Drive(연도 폴더 + 통합 폴더)와 로컬에 저장한다."""
    written = []
    d = drive_dir()
    targets = [LOCAL_SUMMARY_DIR / filename]
    if d is not None:
        targets += [d / year_of(filename) / filename, d / SUMMARY_FOLDER / filename]
    else:
        _warn("Drive 가 연결돼 있지 않아 로컬에만 저장했습니다 (나중에 sync_to_gdrive.py 로 맞춥니다).")
    for t in targets:
        try:
            if overwrite or not t.exists():
                _write(t, text)
                written.append(t)
        except OSError as e:
            _warn(f"저장 실패 {t}: {e}")
    return written


def mirror_file(src, kind):
    """이미 로컬에 쓴 파일(src)을 Drive 로 복사한다 (kind: 'raw' | 'summary'). 복사했으면 True."""
    d = drive_dir()
    ra = raw_archive_dir()
    src = pathlib.Path(src)
    if kind == "raw" and ra is not None:
        dests = [ra / src.name]
    elif d is None:
        return False
    else:
        dests = [d / year_of(src.name) / src.name]
        if kind == "summary":
            dests.append(d / SUMMARY_FOLDER / src.name)
    copied = False
    for dest in dests:
        try:
            if not dest.exists() or dest.stat().st_size != src.stat().st_size:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                copied = True
        except OSError as e:
            _warn(f"Drive 복사 실패 {dest}: {e}")
    return copied


# ---------------------------------------------------------------- 읽기
def raw_files():
    """원문 md 경로 목록 (Drive + 로컬, 이름이 같으면 Drive 우선). 이름순."""
    found = {}
    for p in sorted(LOCAL_RAW_DIR.glob("*.md")):
        found[p.name] = p
    d = drive_dir()
    if d:
        for year_dir in sorted(d.iterdir()):
            if not year_dir.is_dir() or year_dir.name == SUMMARY_FOLDER:
                continue
            for p in year_dir.glob("*.md"):
                if not is_summary(p.name):
                    found[p.name] = p
    # 원문 전용 폴더(raw_dir)는 읽지 않는다: 기존 카탈로그 메모 파일과 구분이 안 되고, 저장할 때 로컬에도 사본을 두므로 로컬이 입력이다.
    return [found[k] for k in sorted(found)]


def summary_files():
    """요약 md 경로 목록 (Drive 통합 폴더 + 로컬, 이름이 같으면 Drive 우선). 이름순."""
    found = {}
    for p in sorted(LOCAL_SUMMARY_DIR.glob("*.md")) if LOCAL_SUMMARY_DIR.is_dir() else []:
        found[p.name] = p
    d = drive_dir()
    if d and (d / SUMMARY_FOLDER).is_dir():
        for p in (d / SUMMARY_FOLDER).glob("*.md"):
            found[p.name] = p
    return [found[k] for k in sorted(found)]


if __name__ == "__main__":
    d = drive_dir()
    print(f"Drive 폴더 : {d if d else '(연결 안 됨 — 로컬만 사용)'}")
    ra = raw_archive_dir()
    print(f"원문 폴더  : {ra if ra else ('(설정됐지만 연결 안 됨)' if raw_dir_configured() else '(미설정 — Drive 연도 폴더 사용)')}")
    print(f"원문 md    : {len(raw_files())}개")
    print(f"요약 md    : {len(summary_files())}개")
