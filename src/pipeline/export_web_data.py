"""
从 SQLite 导出前端用的 JSON 数据。

输出到 web/public/data/:
  papers.json   ── 默认展示的论文（双≥3），精简字段，用于列表
  papers_full.json ── 全量论文（含 TL;DR），用于详情页
  coverage.json ── 覆盖率审计
  meta.json     ── 期刊清单 + 全局统计

输出到 web/public/:
  rss.xml      ── RSS 2.0 feed（最近 30 篇双≥3 论文）
"""

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from sqlalchemy import select, text

from src.db.schema import get_session, Paper, PaperScore, LlmOutput, CoverageGap
from src.utils.journals import load_journals

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "./data/papers.db")
OUT_DIR = Path(__file__).resolve().parents[2] / "web" / "public" / "data"
WEB_PUBLIC_DIR = Path(__file__).resolve().parents[2] / "web" / "public"
SITE_TITLE = "SR × AI Papers"
SITE_DESC = "AI 社交关系 / 人机关系领域论文索引 — 双 LLM 打分 + 中文标题 + TL;DR"
SITE_URL = os.getenv("SITE_URL", "https://sr-ai-papers.vercel.app").rstrip("/")


def _rss_date(d) -> str | None:
    """'2026-06-14' → 'Sun, 14 Jun 2026 00:00:00 +0000' (RFC 822)。"""
    if not d:
        return None
    try:
        dt = datetime.fromisoformat(str(d)[:10])
        return dt.strftime("%a, %d %b %Y 00:00:00 +0000")
    except (ValueError, TypeError):
        return None


def export_rss(slim_list: list[dict]):
    """生成 RSS 2.0 feed。

    RSS 排序按 (ingested_at desc, pub_date desc)：先按"首次入库时间"倒序，
    保证新加入的论文一定在 feed 顶部。

    关键设计：用 Paper.created_at（首次入库时间），不用 scored_at。
    因为重打分会更新 scored_at（导致 RSS 被一次性污染一大批老论文），
    但 created_at 只在论文首次被抓取入库时设置一次，永远不会因为重打分变化。
    """
    rss_list = sorted(
        slim_list,
        key=lambda x: (x.get("ingested_at") or "", x.get("date") or ""),
        reverse=True,
    )
    items = []
    now_rfc = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    for x in rss_list[:30]:
        title = x.get("title_zh") or x.get("title") or "(无标题)"
        title_en = x.get("title") or ""
        tldr = x.get("tldr") or ""
        link = f"{SITE_URL}/papers/{x['id']}"
        # pubDate 用 ingested_at（首次入库时间），fallback 到出版日期
        pub = _rss_date((x.get("ingested_at") or "")[:10]) or _rss_date(x.get("date"))
        authors = ", ".join(x.get("authors") or []) or "Unknown"
        tags = (x.get("topic_tags") or []) + (x.get("ai_type_tags") or [])
        categories_xml = "".join(f"<category>{escape(t)}</category>" for t in tags[:8])

        # 描述：中文标题 + 英文原标题 + TL;DR + 元数据
        desc_parts = [f"📝 {escape(title)}"]
        if title_en and title_en != title:
            desc_parts.append(f"EN: {escape(title_en)}")
        if tldr:
            desc_parts.append(f"\n💡 {escape(tldr)}")
        desc_parts.append(f"\n📚 {escape(x.get('journal') or 'arXiv')} · {x.get('year') or ''} · 引用 {x.get('cited_by') or 0}")
        desc_parts.append(f"👤 {escape(authors)}")
        desc_parts.append(f"\nAI 分: {x.get('ai_score')} / 领域分: {x.get('domain_score')}")
        if x.get("pdf_url"):
            desc_parts.append(f'\n📄 PDF: {escape(x["pdf_url"])}')
        description = "\n".join(desc_parts)

        item = f"""    <item>
      <title>{escape(title)}</title>
      <link>{link}</link>
      <guid isPermaLink="true">{link}</guid>
      <description>{escape(description)}</description>
      {f"<pubDate>{pub}</pubDate>" if pub else ""}
      {categories_xml}
    </item>"""
        items.append(item)

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{SITE_TITLE}</title>
    <link>{SITE_URL}</link>
    <atom:link href="{SITE_URL}/rss.xml" rel="self" type="application/rss+xml" />
    <description>{SITE_DESC}</description>
    <language>zh-CN</language>
    <lastBuildDate>{now_rfc}</lastBuildDate>
    <generator>sr-ai-papers export pipeline</generator>
{''.join(items)}
  </channel>
</rss>
"""
    out = WEB_PUBLIC_DIR / "rss.xml"
    out.write_text(rss, encoding="utf-8")
    print(f"  → rss.xml: {min(len(rss_list), 30)} 条 ({out.stat().st_size//1024} KB)")


def slim_paper(p, score, llm) -> dict:
    """前端列表用的精简记录。"""
    # arXiv 论文没有期刊缩写，用 "arXiv" 或具体子分类
    journal = p.journal_abbr
    if not journal and p.is_arxiv:
        cats = p.arxiv_categories or []
        primary = cats[0] if cats else None
        journal = f"arXiv:{primary}" if primary else "arXiv"
    return {
        "id": p.id,
        "doi": p.doi,
        "ingested_at": p.created_at.isoformat() if p.created_at else None,
        "title": p.title,
        "title_zh": p.title_zh,
        "journal": journal,
        "year": p.pub_year,
        "date": p.pub_date,
        "volume": p.volume,
        "issue": p.issue,
        "authors": [a.get("name") for a in (p.authors or []) if a.get("name")][:5],
        "url": p.landing_page_url or (f"https://doi.org/{p.doi}" if p.doi else None),
        "pdf_url": p.pdf_url or p.open_access_url,
        "cited_by": p.cited_by_count or 0,
        "ai_score": score.ai_relevance,
        "domain_score": score.domain_relevance,
        "ai_reason": score.rationale,
        "scored_at": score.scored_at.isoformat() if score.scored_at else None,
        "tldr": (llm.tldr_zh if llm else None),
        "topic_tags": (llm.topic_tags if llm else []) or [],
        "ai_type_tags": (llm.ai_type_tags if llm else []) or [],
    }


def full_paper(p, score, llm) -> dict:
    """详情页用的完整记录（带摘要+作者全字段）。"""
    base = slim_paper(p, score, llm)
    base["abstract"] = p.abstract
    base["authors_full"] = p.authors or []
    return base


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    session = get_session(DB_PATH)
    try:
        print("加载论文 + 打分 + LLM 输出 …")
        # 一次性 join
        rows = session.execute(text("""
            SELECT p.id
            FROM papers p
            JOIN paper_scores s ON s.paper_id = p.id
            WHERE s.ai_relevance IS NOT NULL
        """)).all()
        ids = [r[0] for r in rows]
        print(f"  总打分论文: {len(ids)}")

        # 分批 in-memory join
        papers = {p.id: p for p in session.execute(select(Paper)).scalars().all()}
        scores = {s.paper_id: s for s in session.execute(select(PaperScore)).scalars().all()}
        llms   = {l.paper_id: l for l in session.execute(select(LlmOutput)).scalars().all()}

        # papers.json: 默认展示用（双≥3），含 TL;DR
        slim_list = []
        for pid in ids:
            s = scores.get(pid); p = papers.get(pid)
            if not s or not p: continue
            if (s.ai_relevance or 0) < 3 or (s.domain_relevance or 0) < 3:
                continue
            l = llms.get(pid)
            slim_list.append(slim_paper(p, s, l))
        # 按年份倒序、AI 分倒序
        slim_list.sort(key=lambda x: (-(x["year"] or 0), -(x["ai_score"] or 0), x["journal"] or ""))
        out_papers = OUT_DIR / "papers.json"
        out_papers.write_text(json.dumps(slim_list, ensure_ascii=False))
        print(f"  → papers.json: {len(slim_list)} 条 ({out_papers.stat().st_size//1024} KB)")

        # papers_full.json: 含摘要的完整版（同样只双≥3，详情页用）
        full_list = []
        for pid in ids:
            s = scores.get(pid); p = papers.get(pid)
            if not s or not p: continue
            if (s.ai_relevance or 0) < 3 or (s.domain_relevance or 0) < 3:
                continue
            l = llms.get(pid)
            full_list.append(full_paper(p, s, l))
        out_full = OUT_DIR / "papers_full.json"
        out_full.write_text(json.dumps(full_list, ensure_ascii=False))
        print(f"  → papers_full.json: {len(full_list)} 条 ({out_full.stat().st_size//1024} KB)")

        # coverage.json
        gaps = session.execute(select(CoverageGap)).scalars().all()
        cov = [{
            "journal": g.journal_abbr,
            "year": g.year,
            "volume": g.volume,
            "issue": g.issue,
            "crossref": g.crossref_count,
            "openalex": g.openalex_count,
            "notes": g.notes,
        } for g in gaps]
        cov.sort(key=lambda x: (x["journal"] or "", -(x["year"] or 0), x["volume"] or "", x["issue"] or ""))
        (OUT_DIR / "coverage.json").write_text(json.dumps(cov, ensure_ascii=False))
        print(f"  → coverage.json: {len(cov)} 条")

        # meta.json: 期刊信息 + 全局统计
        journals = load_journals()
        # 每本期刊统计
        per_journal = Counter()
        per_year = Counter()
        per_topic = Counter()
        per_aitype = Counter()
        for x in slim_list:
            per_journal[x["journal"]] += 1
            per_year[x["year"]] += 1
            for t in x["topic_tags"]: per_topic[t] += 1
            for t in x["ai_type_tags"]: per_aitype[t] += 1

        # 同时统计「全部已抓取论文」（用于审计页"分母"）
        per_journal_all = Counter()
        arxiv_total = 0
        for p in papers.values():
            if p.journal_abbr:
                per_journal_all[p.journal_abbr] += 1
            elif p.is_arxiv:
                arxiv_total += 1

        meta = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "totals": {
                "papers_indexed": len(papers),
                "papers_scored": len(ids),
                "papers_ai_relevant": len(slim_list),  # 双≥3
            },
            "journals": [
                {
                    "abbr": j["abbr"],
                    "name_en": j["name_en"],
                    "name_zh": j["name_zh"],
                    "publisher": j.get("publisher", ""),
                    "tier": j.get("tier", ""),
                    "domain": j.get("domain", []),
                    "lang": j.get("lang", "en"),
                    "publisher_toc": j.get("publisher_toc", ""),
                    "papers_indexed": per_journal_all.get(j["abbr"], 0),
                    "papers_ai_relevant": per_journal.get(j["abbr"], 0),
                }
                for j in journals
            ] + [
                # arXiv 伪期刊条目（统一展示）
                {
                    "abbr": "arXiv",
                    "name_en": "arXiv preprints",
                    "name_zh": "arXiv 预印本",
                    "publisher": "arXiv",
                    "tier": "—",
                    "domain": ["AI", "preprint"],
                    "lang": "en",
                    "publisher_toc": "https://arxiv.org/",
                    "papers_indexed": arxiv_total,
                    "papers_ai_relevant": sum(v for k, v in per_journal.items() if k and k.startswith("arXiv")),
                }
            ],
            "facets": {
                "years": dict(sorted(per_year.items())),
                "journals": dict(per_journal.most_common()),
                "topic_tags": dict(per_topic.most_common(50)),
                "ai_type_tags": dict(per_aitype.most_common(30)),
            },
        }
        (OUT_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        print(f"  → meta.json")

        # rss.xml: 最近 30 篇双≥3 论文
        export_rss(slim_list)

        print(f"\n全部导出到: {OUT_DIR}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
