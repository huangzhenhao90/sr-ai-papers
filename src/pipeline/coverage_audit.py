"""
覆盖率审计：按期刊 × 年 × 卷 × 期 对账双源结果。

输出 coverage_gaps 表，每行 = 一个 (journal, year, volume, issue) 的对账记录。

可疑期判定（标 notes）：
- 总篇数 < 3 且非 ROB/AOM-Annals 等年刊 → 可能漏期
- 某来源占比 < 50% → 该来源漏收
- volume/issue 缺失（NULL）的论文 → "网络首发未定卷期"
"""

import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from sqlalchemy import select, delete

from src.db.schema import get_session, Paper, PaperSource, CoverageGap
from src.utils.journals import by_abbr, english_journals

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "./data/papers.db")

# 年刊（每年只出一卷一期，少篇正常）
YEARLY_JOURNALS = {"ROB", "AOM-Annals"}


def audit():
    session = get_session(DB_PATH)
    try:
        # 清空旧 coverage_gaps，重算
        session.execute(delete(CoverageGap))
        session.commit()

        papers = session.execute(
            select(Paper).where(Paper.journal_abbr.isnot(None))
        ).scalars().all()

        # 聚合: (abbr, year, volume, issue) -> {paper_ids, sources_per_paper}
        bucket = defaultdict(lambda: {"paper_ids": set(), "sources": defaultdict(set)})
        for p in papers:
            key = (p.journal_abbr, p.pub_year, p.volume or "", p.issue or "")
            bucket[key]["paper_ids"].add(p.id)
            for ps in p.sources:
                bucket[key]["sources"][p.id].add(ps.source)

        n_gaps = 0
        n_suspicious = 0
        for (abbr, year, vol, issue), agg in bucket.items():
            paper_ids = agg["paper_ids"]
            total = len(paper_ids)

            # 按来源分别计数
            cr_count = sum(1 for pid in paper_ids if "crossref" in agg["sources"][pid])
            oa_count = sum(1 for pid in paper_ids if "openalex" in agg["sources"][pid])

            # 判定可疑
            notes = []
            if not vol and not issue:
                notes.append("无卷期：可能为网络首发/OnlineFirst")
            elif total < 3 and abbr not in YEARLY_JOURNALS:
                notes.append(f"仅 {total} 篇，疑似漏期")
            if total >= 5:
                if oa_count * 100 // total < 50:
                    notes.append(f"OpenAlex 仅覆盖 {oa_count}/{total}")
                if cr_count * 100 // total < 50:
                    notes.append(f"Crossref 仅覆盖 {cr_count}/{total}")

            if notes:
                n_suspicious += 1

            gap = CoverageGap(
                journal_abbr=abbr,
                year=year or 0,
                volume=vol or None,
                issue=issue or None,
                expected_count=None,
                crossref_count=cr_count,
                openalex_count=oa_count,
                last_audit_at=datetime.utcnow(),
                notes="; ".join(notes) if notes else None,
            )
            session.add(gap)
            n_gaps += 1

        session.commit()
        print(f"已审计 {n_gaps} 个 (期刊×年×卷×期) 单元，其中 {n_suspicious} 个标记可疑")
    finally:
        session.close()


def report():
    """打印可疑列表，方便人工核查。"""
    session = get_session(DB_PATH)
    try:
        suspicious = session.execute(
            select(CoverageGap).where(CoverageGap.notes.isnot(None))
            .order_by(CoverageGap.journal_abbr, CoverageGap.year, CoverageGap.volume, CoverageGap.issue)
        ).scalars().all()

        print(f"\n=== 可疑期清单 ({len(suspicious)} 条) ===\n")
        if not suspicious:
            print("(无)")
            return

        # 按"无卷期"和"真可疑"分两类
        no_vi = [g for g in suspicious if g.notes and "无卷期" in g.notes]
        real_susp = [g for g in suspicious if g not in no_vi]

        print(f"-- 无卷期（网络首发，正常）: {len(no_vi)} 条 --")
        agg = defaultdict(lambda: {"oa": 0, "cr": 0})
        for g in no_vi:
            agg[g.journal_abbr]["oa"] += g.openalex_count
            agg[g.journal_abbr]["cr"] += g.crossref_count
        for abbr in sorted(agg):
            print(f"  {abbr:<15}  OpenAlex={agg[abbr]['oa']}  Crossref={agg[abbr]['cr']}")

        print(f"\n-- 真·可疑期: {len(real_susp)} 条 --")
        for g in real_susp:
            j = by_abbr(g.journal_abbr)
            toc = j.get("publisher_toc", "") if j else ""
            print(f"  {g.journal_abbr} {g.year} V{g.volume or '?'} I{g.issue or '?'}  CR={g.crossref_count} OA={g.openalex_count}")
            print(f"    notes: {g.notes}")
            if toc:
                print(f"    手工核查: {toc}")
    finally:
        session.close()


def journal_summary():
    """每本期刊一行汇总。"""
    session = get_session(DB_PATH)
    try:
        papers = session.execute(
            select(Paper).where(Paper.journal_abbr.isnot(None))
        ).scalars().all()

        agg = defaultdict(lambda: {
            "n": 0, "with_abs": 0, "with_oa": 0,
            "src_oa": 0, "src_cr": 0, "both": 0, "issues": set(),
        })
        for p in papers:
            a = agg[p.journal_abbr]
            a["n"] += 1
            if p.abstract and len(p.abstract) > 50:
                a["with_abs"] += 1
            if p.open_access_url or p.pdf_url:
                a["with_oa"] += 1
            srcs = set(s.source for s in p.sources)
            if "openalex" in srcs:
                a["src_oa"] += 1
            if "crossref" in srcs:
                a["src_cr"] += 1
            if len(srcs) >= 2:
                a["both"] += 1
            if p.volume:
                a["issues"].add((p.pub_year, p.volume, p.issue))

        print(f"\n=== 期刊汇总 ===")
        print(f"{'abbr':<16}{'papers':>7}{'abs%':>6}{'OA%':>6}{'both%':>7}{'OA-only':>9}{'CR-only':>9}{'issues':>8}")
        for abbr in sorted(agg, key=lambda k: -agg[k]["n"]):
            a = agg[abbr]
            n = a["n"]
            print(
                f"{abbr:<16}{n:>7}"
                f"{a['with_abs']*100//n:>5}%"
                f"{a['with_oa']*100//n:>5}%"
                f"{a['both']*100//n:>6}%"
                f"{a['src_oa']-a['both']:>9}"
                f"{a['src_cr']-a['both']:>9}"
                f"{len(a['issues']):>8}"
            )
    finally:
        session.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("cmd", nargs="?", default="all", choices=["audit", "report", "summary", "all"])
    args = p.parse_args()
    if args.cmd in ("audit", "all"):
        audit()
    if args.cmd in ("summary", "all"):
        journal_summary()
    if args.cmd in ("report", "all"):
        report()
