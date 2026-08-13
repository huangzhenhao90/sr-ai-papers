"""
arXiv connector：用 query API 按 [分类 + 用户研究/HCI/UX/CX 关键词] 历史回溯。

设计要点：
- arXiv 没有 user research 子分类，必须用关键词预过滤（与期刊抓取不同）
- query 语法: cat:cs.HC AND (abs:usability OR abs:"user experience" OR ...)
- arXiv API 返回 Atom XML，每次最多 2000 条
- 限速：3 秒/请求（官方建议）
- 时间过滤：用 submittedDate:[20230101 TO 99991231]

输出 slim_record 格式与 OpenAlex/Crossref 对齐，便于 normalizer 复用。
"""

import os
import time
import re
from typing import Iterator
import feedparser
from src.connectors.openalex import reconstruct_abstract
from src.utils.http import make_client, get_with_retry

ARXIV_API = "https://export.arxiv.org/api/query"
OPENALEX_WORKS = "https://api.openalex.org/works"
OPENALEX_ARXIV_SOURCE_ID = "S4306400194"
PER_PAGE = 200       # 实际每次拉 200，太大易超时
RATE_SLEEP = 3.0     # arXiv 官方限速建议
OPENALEX_RATE_SLEEP = 0.2

SYNTHETIC_SOCIAL_KEYWORDS = [
    "virtual companion", "virtual companions",
    "AI companion", "AI companions",
    "chatbot companion", "chatbot companions",
    "social chatbot", "social chatbots",
    "digital companion", "digital companions",
    "companion chatbot", "companion chatbots",
]

# AI 社交关系强信号关键词（arXiv 召回层）
# 砍掉被滥用的词：user、model、performance、social（太宽）。
# 这套词强调「人与 AI / 人与人之间的社会关系、情感连接、互动」。
UR_KEYWORDS = [
    # 拟社会关系与亲密关系（核心强信号）
    "parasocial relationship", "parasocial interaction", "parasocial",
    "romantic relationship", "romantic partner", "intimate relationship",
    "intimacy", "companionship",
    "relationship formation", "relationship maintenance",
    "interpersonal relationship", "interpersonal attraction",
    "social relationship", "friendship", "social bond",
    "emotional bond", "emotional connection", "emotional attachment",
    "attachment theory", "loneliness", "social isolation",
    "social support", "emotional support", "empathy", "empathetic",
    "trust formation", "interpersonal trust",
    "trust in AI", "trust in chatbot", "trust in robot",
    *SYNTHETIC_SOCIAL_KEYWORDS,
    # 拟人化 / 社会临场
    "anthropomorphism", "anthropomorphic", "personification",
    "humanlikeness", "humanness", "social presence", "co-presence",
    "social cues", "social actor", "social robot", "companion robot",
    # 人与 AI 的关系
    "human-AI relationship", "human-AI relationships",
    "human-robot relationship", "human-machine relationship",
    "human-AI friendship", "human-AI romantic", "human-AI attachment",
    "human-AI trust", "relational agent", "relational chatbot",
    "relational AI", "chatbot relationship", "chatbot relationships",
    "AI partner", "AI friend", "AI girlfriend", "AI boyfriend",
    "virtual partner", "virtual friend", "AI therapist",
    # 互动与情感
    "social interaction", "social interactions", "social behavior",
    "social behaviour", "social dynamics", "social cognition",
    "sense of connection", "belonging", "falling in love", "dating",
    "grief", "bereavement", "griefbot",
    "conversational agent", "conversational AI", "embodied conversational agent",
    "AI-mediated communication", "computer-mediated communication",
]

_TEXT_HYPHEN_RE = re.compile(r"[-‐‑‒–—]")
_UR_KEYWORD_PATTERNS = [
    re.compile(
        r"(?<![a-z0-9])" + re.escape(keyword.lower()) + r"(?![a-z0-9])"
    )
    for keyword in UR_KEYWORDS
]
_ARXIV_DOI_RE = re.compile(
    r"10\.48550/arxiv\.([a-z.\-]+/\d{7}|\d{4}\.\d{4,5})",
    re.IGNORECASE,
)
_ARXIV_URL_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/([a-z.\-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?",
    re.IGNORECASE,
)


def matches_ur_keywords(title: str | None, abstract: str | None) -> bool:
    text = f"{title or ''} {abstract or ''}".lower()
    normalized_text = _TEXT_HYPHEN_RE.sub(" ", text)
    return any(
        pattern.search(text) or pattern.search(normalized_text)
        for pattern in _UR_KEYWORD_PATTERNS
    )


def _extract_arxiv_id(work: dict) -> str | None:
    candidates = [work.get("doi") or ""]
    for location in work.get("locations") or []:
        candidates.extend([
            location.get("landing_page_url") or "",
            location.get("pdf_url") or "",
        ])
    for candidate in candidates:
        match = _ARXIV_DOI_RE.search(candidate) or _ARXIV_URL_RE.search(candidate)
        if match:
            return match.group(1)
    return None


def _openalex_work_to_arxiv_record(work: dict) -> dict | None:
    arxiv_id = _extract_arxiv_id(work)
    if not arxiv_id:
        return None
    publication_date = work.get("publication_date") or ""
    return {
        "arxiv_id": arxiv_id,
        "doi": f"10.48550/arxiv.{arxiv_id}",
        "title": work.get("title") or "",
        "abstract": reconstruct_abstract(work.get("abstract_inverted_index")),
        "authors": [
            {"name": (authorship.get("author") or {}).get("display_name")}
            for authorship in (work.get("authorships") or [])
            if (authorship.get("author") or {}).get("display_name")
        ],
        "publication_date": publication_date,
        "updated_date": publication_date,
        "publication_year": work.get("publication_year"),
        "categories": [],
        "primary_category": None,
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "landing_page_url": f"https://arxiv.org/abs/{arxiv_id}",
    }


def fetch_arxiv_via_openalex(
    from_date: str,
    until_date: str | None = None,
) -> Iterator[dict]:
    """Fallback discovery for arXiv when its Query API is unavailable."""
    filters = [
        f"primary_location.source.id:{OPENALEX_ARXIV_SOURCE_ID}",
        f"from_publication_date:{from_date}",
    ]
    if until_date:
        filters.append(f"to_publication_date:{until_date}")
    chunks = [UR_KEYWORDS[i : i + 10] for i in range(0, len(UR_KEYWORDS), 10)]
    seen_ids: set[str] = set()

    with make_client(timeout=60) as client:
        for chunk_index, chunk in enumerate(chunks, 1):
            search = " OR ".join(f'"{keyword}"' for keyword in chunk)
            cursor = "*"
            print(
                f"  [OpenAlex fallback {chunk_index}/{len(chunks)}] "
                f"kw={chunk[:3]}..."
            )
            while cursor:
                response = get_with_retry(
                    client,
                    OPENALEX_WORKS,
                    params={
                        "search": search,
                        "filter": ",".join(filters),
                        "per-page": PER_PAGE,
                        "cursor": cursor,
                        "mailto": os.getenv("CONTACT_EMAIL", "anonymous@example.com"),
                    },
                )
                data = response.json()
                for work in data.get("results", []):
                    record = _openalex_work_to_arxiv_record(work)
                    if not record or record["arxiv_id"] in seen_ids:
                        continue
                    if not matches_ur_keywords(record["title"], record["abstract"]):
                        continue
                    seen_ids.add(record["arxiv_id"])
                    yield record
                cursor = data.get("meta", {}).get("next_cursor")
                if cursor:
                    time.sleep(OPENALEX_RATE_SLEEP)


def _build_query(category: str, kw_chunk: list[str]) -> str:
    """Build arXiv API search query. abstract OR title 任一命中关键词。"""
    kw_clauses = []
    for kw in kw_chunk:
        kw_q = f'"{kw}"' if " " in kw else kw
        kw_clauses.append(f"abs:{kw_q}")
        kw_clauses.append(f"ti:{kw_q}")
    kw_part = "(" + " OR ".join(kw_clauses) + ")"
    return f"cat:{category} AND {kw_part}"


def fetch_category(
    category: str,
    from_date: str = "2023-01-01",
    until_date: str | None = None,
    max_results: int = 5000,
) -> Iterator[dict]:
    """生成器：按分类抓取与用户研究/HCI 相关的 arXiv 论文。

    arXiv 的 query 长度有限制（~6000 字符），所以关键词分块发请求。
    每块用 cursor pagination。
    """
    # 把关键词切成 ~10 个一块，避免 URL 过长
    chunks = [UR_KEYWORDS[i : i + 10] for i in range(0, len(UR_KEYWORDS), 10)]

    seen_ids = set()
    with make_client(timeout=60) as client:
        for chunk_idx, chunk in enumerate(chunks):
            query = _build_query(category, chunk)
            start = 0
            print(f"  [chunk {chunk_idx+1}/{len(chunks)}] cat={category} kw={chunk[:3]}...")
            while start < max_results:
                params = {
                    "search_query": query,
                    "start": start,
                    "max_results": PER_PAGE,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                }
                try:
                    r = get_with_retry(client, ARXIV_API, params=params)
                except Exception as e:
                    raise RuntimeError(
                        f"arXiv API failed for category={category} "
                        f"keyword_chunk={chunk_idx + 1} start={start}: {e}"
                    ) from e
                feed = feedparser.parse(r.text)
                entries = feed.entries
                if not entries:
                    break

                yielded_in_page = 0
                for e in entries:
                    rec = parse_entry(e)
                    if not rec:
                        continue
                    pub = rec.get("publication_date") or ""
                    # 历史窗过滤
                    if pub and pub < from_date:
                        continue
                    if until_date and pub and pub > until_date:
                        continue
                    if rec["arxiv_id"] in seen_ids:
                        continue
                    seen_ids.add(rec["arxiv_id"])
                    yield rec
                    yielded_in_page += 1

                # 当结果按时间倒排时，遇到日期早于 from_date 的就该停
                if entries and (entries[-1].get("published") or "")[:10] < from_date:
                    break

                start += PER_PAGE
                if len(entries) < PER_PAGE:
                    break
                time.sleep(RATE_SLEEP)
            time.sleep(RATE_SLEEP)


def parse_entry(e) -> dict | None:
    """parse 一个 feedparser entry。"""
    arxiv_url = e.get("id", "")  # 形如 http://arxiv.org/abs/2401.12345v2
    m = re.search(r"arxiv\.org/abs/([\w.\-]+?)(?:v\d+)?$", arxiv_url)
    if not m:
        return None
    arxiv_id = m.group(1)

    title = (e.get("title") or "").replace("\n", " ").strip()
    summary = (e.get("summary") or "").replace("\n", " ").strip()
    published = (e.get("published") or "")[:10]
    updated = (e.get("updated") or "")[:10]

    authors = [
        {"name": a.get("name")} for a in (e.get("authors") or [])
    ]
    cats = [t.get("term") for t in (e.get("tags") or [])]

    # arXiv 有时给 doi, journal_ref
    doi = None
    for link in (e.get("links") or []):
        if link.get("title") == "doi":
            doi = link.get("href", "").replace("http://dx.doi.org/", "")
    arxiv_doi = e.get("arxiv_doi") if hasattr(e, "arxiv_doi") else None
    if not doi and arxiv_doi:
        doi = arxiv_doi

    pdf_url = None
    abs_url = None
    for link in (e.get("links") or []):
        if link.get("type") == "application/pdf":
            pdf_url = link.get("href")
        elif link.get("rel") == "alternate":
            abs_url = link.get("href")

    return {
        "arxiv_id": arxiv_id,
        "doi": doi,
        "title": title,
        "abstract": summary,
        "authors": authors,
        "publication_date": published,
        "updated_date": updated,
        "publication_year": int(published[:4]) if published[:4].isdigit() else None,
        "categories": cats,
        "primary_category": cats[0] if cats else None,
        "pdf_url": pdf_url,
        "landing_page_url": abs_url or arxiv_url,
    }
