import os
import sys
import subprocess
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
DOCS_DIR = os.path.join(BASE_DIR, "docs")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

def build_all():
    # 1. Build assets/data.js from output/results.json + enrich.json
    build_data_script = os.path.join(SCRIPTS_DIR, "build_data.py")
    if os.path.exists(build_data_script):
        subprocess.run([sys.executable, build_data_script], check=True)

    # 2. Sync HTML pages and assets to docs/ for static hosting (e.g. GitHub Pages)
    pages = ["index.html"]
    for p in pages:
        src = os.path.join(BASE_DIR, p)
        if os.path.exists(src):
            dst = os.path.join(DOCS_DIR, p)
            shutil.copy2(src, dst)

    # Copy assets to docs/assets
    docs_assets = os.path.join(DOCS_DIR, "assets")
    if os.path.exists(ASSETS_DIR):
        if os.path.exists(docs_assets):
            shutil.rmtree(docs_assets)
        # 글별 상세 요약(assets/summaries)은 비공개: 공개 사이트(docs/)로 복사하지 않는다
        shutil.copytree(ASSETS_DIR, docs_assets, ignore=shutil.ignore_patterns("summaries"))


    print("All site pages and assets successfully built and synced to docs/!")

if __name__ == "__main__":
    build_all()
