"""
数据质量检查：查看 raw_records 的覆盖、摘要率、关键词命中情况。
用法: python scripts/inspect_raw.py JOB
"""

import os
import sys
import json
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from sqlalchemy import select, func

from src.db.schema import get_session, RawRecord, SourceRun
from src.utils.keywords import count_hits

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "./data/papers.db")


def inspect(abbr: str, sample_n: int = 5):
    session = get_session(DB_PATH)

    runs = session.execute(
        select(SourceRun).where(SourceRun.journal_abbr == abbr)
    ).scalars().all()

    print(f"=== 期刊 {abbr} 数据质量报告 ===\n")
    print(f"共 {len(runs)} 次抓取:")
    for r in runs:
        print(f"  [{r.source}] {r.started_at} - {r.status} - {r.records_fetched} 条")

    # 按 source 分别统计
    for src in ["openalex", "crossref"]:
        run_ids = [r.id for r in runs if r.source == src]
        if not run_ids:
            continue
        records = session.execute(
            select(RawRecord).where(RawRecord.run_id.in_(run_ids))
        ).scalars().all()

        print(f"\n--- {src.upper()} ({len(records)} 条) ---")
        if not records:
            continue

        with_doi = sum(1 for r in records if r.payload.get("doi"))
        with_abs = sum(1 for r in records if r.payload.get("abstract"))
        years = Counter(r.payload.get("publication_year") for r in records)

        print(f"  有 DOI:     {with_doi}/{len(records)} ({with_doi*100//len(records)}%)")
        print(f"  有摘要:     {with_abs}/{len(records)} ({with_abs*100//len(records)}%)")
        print(f"  年份分布:   {dict(sorted(years.items()))}")

        # 关键词命中（仅作参考，不作筛选闸门）
        l1_hit = l2_hit = l3_hit = 0
        for r in records:
            text = (r.payload.get("title") or "") + " " + (r.payload.get("abstract") or "")
            hits = count_hits(text, "en")
            if hits["l1"] > 0: l1_hit += 1
            if hits["l2"] > 0: l2_hit += 1
            if hits["l3"] > 0: l3_hit += 1
        print(f"  L1(AI泛指)命中: {l1_hit}/{len(records)}")
        print(f"  L2(人-AI)命中:  {l2_hit}/{len(records)}")
        print(f"  L3(GenAI)命中:  {l3_hit}/{len(records)}")

        # 抽样
        print(f"\n  样本（前 {sample_n} 条）:")
        for r in records[:sample_n]:
            p = r.payload
            title = (p.get("title") or "")[:90]
            doi = p.get("doi", "")
            year = p.get("publication_year", "?")
            has_abs = "✓" if p.get("abstract") else "✗"
            print(f"    [{year}] abs:{has_abs} {title}")
            print(f"           {doi}")

    # 命中 AI 关键词的样本
    print(f"\n--- AI 相关样本 (任一层命中) ---")
    all_records = session.execute(
        select(RawRecord).where(RawRecord.source == "openalex")
        .join(SourceRun).where(SourceRun.journal_abbr == abbr)
    ).scalars().all()

    ai_hits = []
    for r in all_records:
        text = (r.payload.get("title") or "") + " " + (r.payload.get("abstract") or "")
        hits = count_hits(text, "en")
        if hits["l1"] + hits["l2"] + hits["l3"] > 0:
            ai_hits.append((r, hits))

    print(f"共 {len(ai_hits)} 条命中关键词")
    for r, hits in ai_hits[:10]:
        p = r.payload
        title = (p.get("title") or "")[:100]
        print(f"  L1={hits['l1']} L2={hits['l2']} L3={hits['l3']}  {title}")

    session.close()


if __name__ == "__main__":
    abbr = sys.argv[1] if len(sys.argv) > 1 else "JOB"
    inspect(abbr)
