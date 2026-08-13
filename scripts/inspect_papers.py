"""
检查 papers 表（normalize 之后）的数据质量。
用法: python scripts/inspect_papers.py JOB
"""

import os
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from sqlalchemy import select, func

from src.db.schema import get_session, Paper, PaperSource
from src.utils.keywords import count_hits, matched_keywords

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "./data/papers.db")


def inspect(abbr: str):
    session = get_session(DB_PATH)
    papers = session.execute(
        select(Paper).where(Paper.journal_abbr == abbr)
    ).scalars().all()

    print(f"=== {abbr} 去重后报告 ===\n")
    print(f"唯一论文数: {len(papers)}")
    if not papers:
        return

    with_doi = sum(1 for p in papers if p.doi)
    with_abs = sum(1 for p in papers if p.abstract)
    with_pdf = sum(1 for p in papers if p.pdf_url or p.open_access_url)
    years = Counter(p.pub_year for p in papers)
    langs = Counter(p.lang for p in papers)

    print(f"  有 DOI:     {with_doi}/{len(papers)} ({with_doi*100//len(papers)}%)")
    print(f"  有摘要:     {with_abs}/{len(papers)} ({with_abs*100//len(papers)}%)")
    print(f"  有 PDF/OA:  {with_pdf}/{len(papers)} ({with_pdf*100//len(papers)}%)")
    print(f"  年份:       {dict(sorted(years.items()))}")
    print(f"  语言:       {dict(langs)}")

    # 双源覆盖统计
    multi_source_count = 0
    single_oa = 0
    single_cr = 0
    for p in papers:
        srcs = set(s.source for s in p.sources)
        if len(srcs) > 1:
            multi_source_count += 1
        elif "openalex" in srcs:
            single_oa += 1
        elif "crossref" in srcs:
            single_cr += 1
    print(f"\n  双源对账:")
    print(f"    OpenAlex + Crossref 都有: {multi_source_count}")
    print(f"    仅 OpenAlex:               {single_oa}")
    print(f"    仅 Crossref:               {single_cr}")

    # 关键词命中
    l1 = l2 = l3 = 0
    ai_papers = []
    for p in papers:
        text = (p.title or "") + " " + (p.abstract or "")
        h = count_hits(text, "en")
        if h["l1"] > 0: l1 += 1
        if h["l2"] > 0: l2 += 1
        if h["l3"] > 0: l3 += 1
        if h["l1"] + h["l2"] + h["l3"] > 0:
            ai_papers.append((p, h))

    print(f"\n  关键词命中（仅参考）:")
    print(f"    L1 AI 泛指: {l1}")
    print(f"    L2 人-AI:   {l2}")
    print(f"    L3 GenAI:   {l3}")
    print(f"    任一命中:   {len(ai_papers)}")

    # 样本：L3 命中的（最像 GenAI 论文）
    l3_papers = [(p, h) for p, h in ai_papers if h["l3"] > 0]
    print(f"\n  L3（GenAI/LLM）命中样本，共 {len(l3_papers)} 篇:")
    for p, h in sorted(l3_papers, key=lambda x: -x[1]["l3"])[:15]:
        m = matched_keywords((p.title or "") + " " + (p.abstract or ""), "en")
        print(f"    [{p.pub_year}] {(p.title or '')[:90]}")
        print(f"           hits: L3={m['l3']}")

    session.close()


if __name__ == "__main__":
    abbr = sys.argv[1] if len(sys.argv) > 1 else "JOB"
    inspect(abbr)
