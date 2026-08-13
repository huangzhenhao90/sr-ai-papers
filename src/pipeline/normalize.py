"""
将 raw_records 标准化为 Paper + PaperSource 实体。

策略：
- DOI 是去重的金标准；DOI 一致 = 同一篇
- 无 DOI 时用指纹: norm(title) + first_author_surname + year + volume + issue + first_page
- OpenAlex 与 Crossref 字段差异由本层吸收
- 字段冲突时合并策略：
    * abstract: 谁有用谁，长度更长的优先
    * cited_by_count: OpenAlex 优先
    * pdf_url / OA: 任一有即用
    * authors / venue: 优先 OpenAlex（结构化更全）
"""

import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from sqlalchemy import select

from src.db.schema import get_session, RawRecord, Paper, PaperSource, SourceRun
from src.utils.journals import by_issn, load_journals

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "./data/papers.db")


# ---------- 标题规范化 / 指纹 ----------
def norm_title(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = d.lstrip("/")
    return d or None


def make_fingerprint(title: str, first_author: str, year: int | None,
                     volume: str | None, issue: str | None, first_page: str | None) -> str:
    """无 DOI 时的去重指纹。"""
    parts = [
        norm_title(title)[:80],
        (first_author or "").lower().split()[-1] if first_author else "",
        str(year or ""),
        (volume or "").strip(),
        (issue or "").strip(),
        (first_page or "").strip(),
    ]
    return "|".join(parts)


# ---------- OpenAlex / Crossref 解析 ----------
def parse_openalex(p: dict) -> dict:
    """OpenAlex slim_record -> Paper 字段 dict。"""
    src = p.get("source") or {}
    biblio = p.get("biblio") or {}
    issn_l = src.get("issn_l")
    journal = by_issn(issn_l) if issn_l else None

    authors = p.get("authorships") or []
    first_author = authors[0].get("name") if authors else None

    return {
        "doi": norm_doi(p.get("doi")),
        "title": p.get("title"),
        "abstract": p.get("abstract"),
        "authors": [
            {
                "name": a.get("name"),
                "orcid": a.get("orcid"),
                "affiliation": a.get("affiliations", []),
                "position": a.get("position"),
            }
            for a in authors
        ],
        "first_author": first_author,
        "journal_abbr": journal["abbr"] if journal else None,
        "journal_name": src.get("display_name"),
        "pub_year": p.get("publication_year"),
        "pub_date": p.get("publication_date"),
        "volume": biblio.get("volume"),
        "issue": biblio.get("issue"),
        "first_page": biblio.get("first_page"),
        "pages": (
            f"{biblio.get('first_page')}-{biblio.get('last_page')}"
            if biblio.get("first_page") and biblio.get("last_page")
            else biblio.get("first_page")
        ),
        "cited_by_count": p.get("cited_by_count") or 0,
        "open_access_url": (p.get("open_access") or {}).get("oa_url"),
        "pdf_url": p.get("pdf_url") or p.get("best_oa_url"),
        "landing_page_url": p.get("landing_page_url"),
        "lang": p.get("language"),
        "type": p.get("type"),
        "is_retracted": p.get("is_retracted", False),
    }


def parse_crossref(p: dict) -> dict:
    issns = p.get("issn") or []
    journal = None
    for issn in issns:
        journal = by_issn(issn)
        if journal:
            break

    authors = p.get("authors") or []
    first_author = authors[0].get("name") if authors else None
    page = p.get("page") or ""
    first_page = page.split("-")[0] if page else None

    return {
        "doi": norm_doi(p.get("doi")),
        "title": p.get("title"),
        "abstract": p.get("abstract"),
        "authors": authors,
        "first_author": first_author,
        "journal_abbr": journal["abbr"] if journal else None,
        "journal_name": p.get("container_title"),
        "pub_year": p.get("publication_year"),
        "pub_date": p.get("publication_date"),
        "volume": p.get("volume"),
        "issue": p.get("issue"),
        "first_page": first_page,
        "pages": page,
        "cited_by_count": p.get("is_referenced_by_count") or 0,
        "open_access_url": None,
        "pdf_url": None,
        "landing_page_url": p.get("url"),
        "lang": p.get("language"),
        "type": p.get("type"),
        "is_retracted": False,
    }


# ---------- 合并策略 ----------
def merge_into(existing: Paper, new_data: dict, source: str):
    """把 new_data 合并到已有 Paper，仅在已有为空或新值更优时覆盖。"""
    # abstract: 取更长的
    new_abs = new_data.get("abstract")
    if new_abs and (not existing.abstract or len(new_abs) > len(existing.abstract)):
        existing.abstract = new_abs

    # cited_by_count: OpenAlex 优先
    if source == "openalex":
        existing.cited_by_count = new_data.get("cited_by_count") or existing.cited_by_count
    else:
        existing.cited_by_count = max(existing.cited_by_count or 0, new_data.get("cited_by_count") or 0)

    # 其它字段：空则填
    for f in ("title", "pub_date", "pub_year", "volume", "issue", "pages",
              "open_access_url", "pdf_url", "landing_page_url", "lang", "journal_abbr", "journal_name"):
        v = new_data.get(f)
        if v and not getattr(existing, f, None):
            setattr(existing, f, v)

    # authors: OpenAlex 优先（结构化更全）
    if source == "openalex" and new_data.get("authors"):
        existing.authors = new_data["authors"]
    elif not existing.authors and new_data.get("authors"):
        existing.authors = new_data["authors"]


# ---------- 主流程 ----------
def normalize(journal_abbr: str | None = None, only_new: bool = True):
    session = get_session(DB_PATH)
    try:
        # 取所有未处理的 raw_records（通过 paper_sources 反查）
        # 简化：每次重新跑，直接清空 paper_sources/papers 与 abbr 相关的；或者按 raw_id 做幂等
        # 这里采用：检查 raw_record_id 是否已经在 paper_sources 里出现过
        existing_raw_ids = set(
            session.execute(select(PaperSource.raw_record_id)).scalars().all()
        )

        q = select(RawRecord).where(RawRecord.source.in_(["openalex", "crossref"]))
        if journal_abbr:
            q = q.join(SourceRun, RawRecord.run_id == SourceRun.id).where(SourceRun.journal_abbr == journal_abbr)

        raws = session.execute(q).scalars().all()
        raws = [r for r in raws if r.id not in existing_raw_ids] if only_new else raws

        print(f"待处理 raw_records: {len(raws)}")

        n_new = 0
        n_merged = 0
        n_skipped = 0

        for r in raws:
            payload = r.payload
            try:
                if r.source == "openalex":
                    data = parse_openalex(payload)
                elif r.source == "crossref":
                    data = parse_crossref(payload)
                else:
                    continue
            except Exception as e:
                print(f"  ! 解析失败 raw_id={r.id}: {e}")
                continue

            # 跳过非 journal-article 类型（editorial、erratum 等）
            if data.get("type") and data["type"] not in (
                "journal-article", "article", "review-article", "research-article",
            ):
                n_skipped += 1
                continue

            # 找已有 Paper：先 DOI，再 fingerprint
            paper = None
            doi = data.get("doi")
            if doi:
                paper = session.execute(select(Paper).where(Paper.doi == doi)).scalar_one_or_none()

            if not paper:
                fp = make_fingerprint(
                    data.get("title") or "",
                    data.get("first_author") or "",
                    data.get("pub_year"),
                    data.get("volume"),
                    data.get("issue"),
                    data.get("first_page"),
                )
                if fp.replace("|", "").strip():
                    paper = session.execute(select(Paper).where(Paper.fingerprint == fp)).scalar_one_or_none()
            else:
                fp = None

            if paper:
                merge_into(paper, data, r.source)
                n_merged += 1
                is_primary = False
            else:
                paper = Paper(
                    doi=doi,
                    fingerprint=fp,
                    title=data.get("title") or "(无标题)",
                    abstract=data.get("abstract"),
                    authors=data.get("authors"),
                    journal_abbr=data.get("journal_abbr"),
                    journal_name=data.get("journal_name"),
                    pub_year=data.get("pub_year"),
                    pub_date=data.get("pub_date"),
                    volume=data.get("volume"),
                    issue=data.get("issue"),
                    pages=data.get("pages"),
                    cited_by_count=data.get("cited_by_count") or 0,
                    open_access_url=data.get("open_access_url"),
                    pdf_url=data.get("pdf_url"),
                    landing_page_url=data.get("landing_page_url"),
                    lang=data.get("lang"),
                    is_arxiv=False,
                )
                session.add(paper)
                session.flush()
                n_new += 1
                is_primary = True

            session.add(PaperSource(
                paper_id=paper.id,
                raw_record_id=r.id,
                source=r.source,
                is_primary=is_primary,
            ))

            if (n_new + n_merged) % 100 == 0:
                session.commit()

        session.commit()
        print(f"完成: 新增 {n_new} 篇 / 合并 {n_merged} 条到已有 / 跳过非 article {n_skipped} 条")
    finally:
        session.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--journal", default=None, help="仅处理某本期刊（abbr），默认全部")
    p.add_argument("--all", action="store_true", help="重新处理所有 raw（含已处理）")
    args = p.parse_args()
    normalize(journal_abbr=args.journal, only_new=not args.all)
