#!/usr/bin/env python3
"""
scripts/build_catalog.py
SemiAnalysis 요약본 311개 색인 카탈로그(docs/index.json) 및 자산 빌드 스크립트.

기능:
1. output/semianalysis/summary/*.md 파일 311개를 정밀 파싱
2. 메타데이터(제목, 발행일, 저자, 원문 URL, 한줄 요약, 태그, 카테고리, 통계) 추출
3. docs/index.json 및 docs/assets/catalog.js (로컬 file:// 프로토콜 호환용) 생성
4. docs/summaries/ 폴더로 311개 마크다운 파일 자동 동기화 (GitHub Pages 완벽 호환)
"""

import os
import re
import json
import shutil
import glob
from datetime import datetime

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 패키지로 불러와도 storage 를 찾도록
import storage  # 요약 md 위치(Drive + 로컬) 한곳 관리

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_SUMMARY_DIR = os.path.join(BASE_DIR, "output", "semianalysis", "summary")
DOCS_DIR = os.path.join(BASE_DIR, "docs")
DOCS_SUMMARIES_DIR = os.path.join(DOCS_DIR, "summaries")
INDEX_JSON_PATH = os.path.join(DOCS_DIR, "index.json")
CATALOG_JS_PATH = os.path.join(DOCS_DIR, "assets", "catalog.js")

# 태그 매칭 규칙
TAG_PATTERNS = {
    "Nvidia": r"\b(Nvidia|NVDA|엔비디아)\b",
    "Blackwell": r"\b(Blackwell|B200|GB200|B100)\b",
    "Rubin": r"\b(Rubin|Vera Rubin)\b",
    "Hopper": r"\b(Hopper|H100|H200)\b",
    "TSMC": r"\b(TSMC|대만반도체)\b",
    "Intel": r"\b(Intel|인텔|18A)\b",
    "AMD": r"\b(AMD|MI300|MI325|MI355)\b",
    "Apple": r"\b(Apple|애플|M-series|A18)\b",
    "Samsung": r"\b(Samsung|삼성|삼성전자)\b",
    "SK Hynix": r"\b(SK\s*Hynix|SK하이닉스|하이닉스)\b",
    "Micron": r"\b(Micron|마이크론)\b",
    "Broadcom": r"\b(Broadcom|브로드컴)\b",
    "ASML": r"\b(ASML|노광)\b",
    "Google": r"\b(Google|TPU|구글)\b",
    "Amazon": r"\b(Amazon|AWS|Trainium|Inferentia)\b",
    "Microsoft": r"\b(Microsoft|MSFT|마이크로소프트|Azure)\b",
    "Meta": r"\b(Meta|메타|MTIA)\b",
    "OpenAI": r"\b(OpenAI|오픈AI|ChatGPT)\b",
    "DeepSeek": r"\b(DeepSeek|딥시크)\b",
    "HBM": r"\b(HBM|HBM3|HBM3e|HBM4)\b",
    "CoWoS": r"\b(CoWoS|InFO|SoIC|패키징|Packaging)\b",
    "CPO": r"\b(CPO|Optical|광통신|Silicon Photonics)\b",
    "Datacenter": r"\b(Datacenter|데이터센터|Data Center)\b",
    "Power": r"\b(Power|전력|원전|Nuclear|800V)\b",
    "EUV": r"\b(EUV|High-NA|리소그래피|Lithography)\b",
    "Foundry": r"\b(Foundry|파운드리|팹|Fab)\b",
    "LLM": r"\b(LLM|추론|Inference|MoE|생성형 AI)\b",
    "Qualcomm": r"\b(Qualcomm|스냅드래곤|Snapdragon)\b",
    "Arm": r"\b(ARM|Arm|암)\b"
}

def clean_title(title_raw: str, filename: str) -> str:
    """제목 정제: 접두사 및 마크다운 특수문자 제거"""
    title = title_raw.strip()
    # Remove # [SemiAnalysis 분석] or similar
    title = re.sub(r"^#+\s*(?:\[SemiAnalysis\s*분석\]\s*)?", "", title)
    title = title.strip()
    if not title:
        # Fallback to filename without date and summary
        base = os.path.basename(filename)
        base = re.sub(r"^(?:\d+_)?\d{4}-\d{2}-\d{2}_", "", base)
        base = re.sub(r"_summary\.md$", "", base)
        title = base.replace("_", " ")
    return title

def parse_date(content: str, filename: str) -> tuple[str, int]:
    """날짜 파싱 및 YYYY-MM-DD 정규화"""
    m_date = re.search(r"\|\s*발행일\s*\|\s*([^|\n]+)\|", content)
    d_raw = m_date.group(1).strip() if m_date else ""
    
    # Check YYYY-MM-DD or YYYY년 M월 D일 or YYYY. M. D.
    m_iso = re.search(r"(\d{4})[-./년\s]+(\d{1,2})[-./월\s]+(\d{1,2})", d_raw)
    if m_iso:
        y, m, d = m_iso.groups()
        iso_str = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        return iso_str, int(y)
        
    # Filename fallback
    m_fn = re.search(r"(\d{4})-(\d{2})-(\d{2})", filename)
    if m_fn:
        y, m, d = m_fn.groups()
        return f"{y}-{m}-{d}", int(y)
        
    return "2024-01-01", 2024

def parse_url(content: str, slug: str) -> str:
    """출처 URL 추출"""
    m_row = re.search(r"\|\s*출처\s*\|\s*([^|\n]+)\|", content)
    if m_row:
        m_link = re.search(r"https?://[^\s)\]\"\'\|]+", m_row.group(1))
        if m_link:
            return m_link.group(0).rstrip(".,;")
            
    # Whole doc URL search
    m_any = re.search(r"https://newsletter\.semianalysis\.com/p/[a-zA-Z0-9_\-]+", content)
    if m_any:
        return m_any.group(0)
        
    return f"https://newsletter.semianalysis.com/p/{slug}"

def parse_author(content: str) -> str:
    """저자 정보 추출"""
    m_auth = re.search(r"\|\s*저자\s*\|\s*([^|\n]+)\|", content)
    if m_auth:
        auth = m_auth.group(1).strip()
        if auth and auth != "-":
            return auth
    return "Dylan Patel"

def parse_one_line_summary(content: str) -> str:
    """Executive Summary에서 핵심 팩트 추출"""
    m_exec = re.search(r"## 1\.\s*Executive Summary(.*?)(?=\n##\s|\Z)", content, re.DOTALL)
    if not m_exec:
        return "본문 분석 리포트 참조."
        
    exec_text = m_exec.group(1).strip()
    
    # 1. 핵심 팩트 패턴
    m_fact = re.search(r"-\s*\*{0,2}핵심\s*팩트\*{0,2}\s*[:：]\s*(.+)", exec_text)
    if m_fact:
        raw = m_fact.group(1).strip().split("\n")[0].strip()
    else:
        # 2. 첫 번째 불릿
        bullets = re.findall(r"-\s*(.+)", exec_text)
        if bullets:
            raw = bullets[0].strip().split("\n")[0].strip()
        else:
            lines = [l.strip() for l in exec_text.split("\n") if l.strip() and not l.startswith("#")]
            raw = lines[0] if lines else "SemiAnalysis 상세 인텔리전스 분석."
            
    # Clean leading bullets, colon labels, and bold markers
    cleaned = re.sub(r"^[-*\s]+", "", raw)
    cleaned = re.sub(r"^(?:\*{0,2})[^:*]+?(?:\*{0,2})\s*[:：]\s*", "", cleaned)
    cleaned = cleaned.replace("**", "").strip()
    return cleaned if cleaned else "SemiAnalysis 상세 인텔리전스 분석."

def parse_key_insights(content: str) -> list[str]:
    """Key Insights 항목 제목 추출 (최대 3개)"""
    insights = []
    m_sec = re.search(r"## 2\.\s*Key Insights(.*?)(?=\n##\s|\Z)", content, re.DOTALL)
    if m_sec:
        titles = re.findall(r"###\s*(?:\[[^\]]+\]\s*)?([^\n]+)", m_sec.group(1))
        for t in titles[:3]:
            t_clean = t.replace("**", "").strip()
            if t_clean:
                insights.append(t_clean)
    return insights

def extract_tags(text: str) -> list[str]:
    """정규식 패턴 기반 태그 추출"""
    matched = []
    for tag, pat in TAG_PATTERNS.items():
        if re.search(pat, text, re.IGNORECASE):
            matched.append(tag)
    # 태그가 없으면 일반 반도체 태그 부여
    if not matched:
        matched = ["Semiconductor", "SemiAnalysis"]
    return matched[:8]

def classify_category(title: str, text: str) -> str:
    """카테고리 5분류"""
    # 1. 제목 우선 매칭
    s_acc = len(re.findall(r"(GPU|가속기|Blackwell|Rubin|Hopper|B200|GB200|MI300|TPU|Gaudi|Nvidia|AMD)", title, re.I)) * 3
    s_pack = len(re.findall(r"(HBM|DRAM|NAND|Memory|메모리|CoWoS|패키징|Packaging|Foundry|파운드리|TSMC|Intel|EUV|ASML|Lithography|노광|Substrate)", title, re.I)) * 3
    s_dc = len(re.findall(r"(Datacenter|데이터센터|Data Center|Power|전력|Cooling|냉각|CPO|Optical|광통신|Network|네트워크|Rack|800V)", title, re.I)) * 3
    s_llm = len(re.findall(r"(LLM|Inference|추론|Training|학습|MoE|DeepSeek|OpenAI|ChatGPT|Claude|Model|모델)", title, re.I)) * 3
    s_geo = len(re.findall(r"(China|중국|규제|Sanction|수출|매출|Cost|TCO|Capex|투자|전략|Business|M&A|IPO|Valuation)", title, re.I)) * 3
    
    scores = {
        "accelerator": s_acc,
        "packaging-memory": s_pack,
        "datacenter-infra": s_dc,
        "llm-economics": s_llm,
        "strategy-geo": s_geo
    }
    
    best = max(scores, key=scores.get)
    if scores[best] > 0:
        return best
        
    # 2. 본문 검색 매칭
    s_acc = len(re.findall(r"(GPU|가속기|Blackwell|B200|TPU)", text, re.I))
    s_pack = len(re.findall(r"(HBM|DRAM|CoWoS|패키징|Foundry|파운드리|TSMC|EUV)", text, re.I))
    s_dc = len(re.findall(r"(Datacenter|데이터센터|전력|CPO|Optical)", text, re.I))
    s_llm = len(re.findall(r"(LLM|Inference|추론|MoE)", text, re.I))
    s_geo = len(re.findall(r"(중국|규제|Sanction|Capex|투자)", text, re.I))
    scores = {
        "accelerator": s_acc,
        "packaging-memory": s_pack,
        "datacenter-infra": s_dc,
        "llm-economics": s_llm,
        "strategy-geo": s_geo
    }
    return max(scores, key=scores.get)

def build_catalog():
    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(DOCS_SUMMARIES_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(CATALOG_JS_PATH), exist_ok=True)

    files = [str(p) for p in storage.summary_files()]  # Drive 통합 폴더 + 로컬
    total_files = len(files)
    print(f"=== [SemiAnalysis 색인 카탈로그 빌더] ===")
    print(f"총 요약 파일 수: {total_files}개 (Drive: {storage.drive_dir() or '미연결, 로컬만'})")

    catalog = []
    category_counts = {}
    tag_counts = {}

    for idx, filepath in enumerate(files, 1):
        filename = os.path.basename(filepath)
        slug = re.sub(r"^(?:\d+_)?\d{4}-\d{2}-\d{2}_", "", filename)
        slug = re.sub(r"_summary\.md$", "", slug)

        with open(filepath, "r", encoding="utf-8") as fp:
            content = fp.read()

        # 1. 제목
        first_line = content.split("\n")[0]
        title = clean_title(first_line, filename)

        # 2. 날짜
        date_str, year = parse_date(content, filename)

        # 3. 출처 URL
        source_url = parse_url(content, slug)

        # 4. 저자
        author = parse_author(content)

        # 5. 한줄 요약
        one_line = parse_one_line_summary(content)

        # 6. 핵심 인사이트 헤드라인
        insights = parse_key_insights(content)

        # 7. 태그
        tags = extract_tags(content)
        for t in tags:
            tag_counts[t] = tag_counts.get(t, 0) + 1

        # 8. 카테고리
        category = classify_category(title, content)
        category_counts[category] = category_counts.get(category, 0) + 1

        # 9. 텍스트 통계
        char_count = len(content)
        word_count = len(re.findall(r"\w+", content))
        read_time_min = max(2, round(word_count / 180))

        # 10. 마크다운 파일 복사 (docs/summaries/<filename>)
        dest_summary_path = os.path.join(DOCS_SUMMARIES_DIR, filename)
        if not os.path.exists(dest_summary_path) or os.path.getsize(dest_summary_path) != os.path.getsize(filepath):
            shutil.copy2(filepath, dest_summary_path)

        catalog.append({
            "id": f"{date_str}_{slug}",
            "slug": slug,
            "title": title,
            "date": date_str,
            "year": year,
            "category": category,
            "author": author,
            "source_url": source_url,
            "one_line_summary": one_line,
            "insights": insights,
            "tags": tags,
            "summary_file": f"summaries/{filename}",
            "source_file": f"output/semianalysis/{filename.replace('_summary.md', '.md')}",
            "stats": {
                "chars": char_count,
                "words": word_count,
                "read_time_min": read_time_min
            }
        })

    # 최신순 정렬 (Date DESC)
    catalog.sort(key=lambda x: (x["date"], x["id"]), reverse=True)

    # 1. docs/index.json 저장
    with open(INDEX_JSON_PATH, "w", encoding="utf-8") as fp:
        json.dump(catalog, fp, ensure_ascii=False, indent=2)

    # 2. docs/assets/catalog.js 저장 (file:// 프로토콜 완벽 호환용)
    with open(CATALOG_JS_PATH, "w", encoding="utf-8") as fp:
        fp.write("/* Auto-generated by scripts/build_catalog.py */\n")
        fp.write("window.CATALOG = ")
        json.dump(catalog, fp, ensure_ascii=False)
        fp.write(";\n")

    json_size_kb = os.path.getsize(INDEX_JSON_PATH) / 1024
    print(f"\n[빌드 완료]")
    print(f"- 생성된 색인: {INDEX_JSON_PATH} ({json_size_kb:.1f} KB, {len(catalog)}건)")
    print(f"- 로컬 지원 JS: {CATALOG_JS_PATH}")
    print(f"- 마크다운 동기화: {DOCS_SUMMARIES_DIR} ({len(glob.glob(os.path.join(DOCS_SUMMARIES_DIR, '*.md')))}개)")
    print(f"- 기간 범위: {catalog[-1]['date']} ~ {catalog[0]['date']}")
    print(f"- 카테고리 분포: {category_counts}")
    print(f"- 상위 태그 TOP 10: {dict(sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10])}")

if __name__ == "__main__":
    build_catalog()
