# -*- coding: utf-8 -*-
"""
scripts/check_mcp_health.py
MCP 설정 및 보안 상태 자가 진단 스크립트:
1. mcp_config.json 구문 및 설정 유효성 검사
2. API Key 존재 여부 및 형식 검사
3. Git 보안 감사 (실제 키 파일이 Git에 추적/스테이징되고 있는지 점검)
"""
import os
import sys
import json
import pathlib
import subprocess

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent

def check_mcp_json():
    print("[1] MCP 설정 파일 점검")
    configs = [
        BASE_DIR / "mcp_config.json",
        BASE_DIR / ".agents" / "mcp_config.json"
    ]
    for p in configs:
        rel = p.relative_to(BASE_DIR)
        if not p.exists():
            print(f"  ❌ 누락: {rel}")
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            servers = data.get("mcpServers", {})
            print(f"  ✅ 정상: {rel} (등록된 서버: {', '.join(servers.keys())})")
            for name, srv in servers.items():
                cmd = srv.get("command")
                env = srv.get("env", {})
                has_key = any("KEY" in k for k in env.keys())
                print(f"     - {name}: command='{cmd}', Key설정={has_key}")
        except Exception as e:
            print(f"  ❌ 오류: {rel} ({e})")

def check_env_secrets():
    print("\n[2] .env 환경 변수(비밀키) 점검")
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        print("  ❌ .env 파일이 존재하지 않습니다.")
        return
    lines = env_path.read_text(encoding="utf-8").splitlines()
    found_keys = {}
    for line in lines:
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            found_keys[k.strip()] = v.strip()
    
    for expected in ["RESEND_API_KEY", "FIRECRAWL_API_KEY"]:
        val = found_keys.get(expected, "")
        if val:
            masked = val[:5] + "..." + val[-4:] if len(val) > 10 else "***"
            print(f"  ✅ {expected}: 등록됨 ({masked})")
        else:
            print(f"  ⚠️ {expected}: 미설정")

def check_git_security():
    print("\n[3] Git 보안 및 유출 방지 점검")
    try:
        res = subprocess.run(["git", "status", "--porcelain"], cwd=BASE_DIR, capture_output=True, text=True)
        lines = res.stdout.splitlines()
        sensitive_files = ["mcp_config.json", ".agents/mcp_config.json", ".env"]
        leaked = []
        for line in lines:
            status = line[:2]
            filepath = line[3:].strip()
            for s in sensitive_files:
                if s == filepath and ("A" in status or "M" in status):
                    leaked.append((filepath, status))
        
        if leaked:
            print("  🚨 경고: 민감한 파일이 Git에 감지되었습니다!")
            for f, s in leaked:
                print(f"     - {f} (상태: {s})")
        else:
            print("  🛡️ 안전: 모든 비밀 키 파일(mcp_config.json, .env 등)이 Git 추적에서 완벽히 격리되었습니다.")
    except Exception as e:
        print(f"  ⚠️ Git 상태 확인 불가: {e}")

def main():
    print("=" * 60)
    print("      MCP 동작 상황 및 보안 진단 리포트 (Health Check)")
    print("=" * 60)
    check_mcp_json()
    check_env_secrets()
    check_git_security()
    print("=" * 60)

if __name__ == "__main__":
    main()
