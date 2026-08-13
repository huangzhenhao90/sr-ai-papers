"""
SQLite schema for ur-ai-papers.

设计原则:
- raw_records 永不删除（可审计、可回溯、可重跑）
- 每一层有清晰边界，下游表通过外键回溯到 raw
- 多源同一篇论文用 paper_sources 关联
- coverage_gaps 显式记录每期的对账状态
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Float, Boolean,
    ForeignKey, JSON, Index, UniqueConstraint, create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


# ============================================================
# 1. 来源运行日志：每次抓取任务的元信息
# ============================================================
class SourceRun(Base):
    __tablename__ = "source_runs"

    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)         # crossref | openalex | arxiv_oai | wanfang | nssd | publisher_toc | cnki_ris
    journal_abbr = Column(String, nullable=True)    # 关联到 journals.yaml 的 abbr；arxiv 等无期刊来源为 NULL
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String, default="running")      # running | success | failed | partial
    records_fetched = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    params = Column(JSON, nullable=True)            # 本次跑的查询参数（便于复现）

    raw_records = relationship("RawRecord", back_populates="run")


# ============================================================
# 2. 原始记录：所有来源的原始 JSON，永不删
# ============================================================
class RawRecord(Base):
    __tablename__ = "raw_records"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("source_runs.id"), nullable=False)
    source = Column(String, nullable=False)         # 冗余存一份，方便查询
    source_record_id = Column(String, nullable=True)  # crossref DOI / openalex W ID / arxiv ID / RIS row hash
    fetched_at = Column(DateTime, default=datetime.utcnow)
    payload = Column(JSON, nullable=False)          # 原始响应（裁剪过的 dict）

    run = relationship("SourceRun", back_populates="raw_records")

    __table_args__ = (
        Index("ix_raw_records_source", "source"),
        Index("ix_raw_records_source_record_id", "source_record_id"),
        UniqueConstraint("source", "source_record_id", name="uq_raw_source_record"),
    )


# ============================================================
# 3. 论文实体：去重后的逻辑论文
# ============================================================
class Paper(Base):
    __tablename__ = "papers"

    id = Column(Integer, primary_key=True)
    doi = Column(String, nullable=True, unique=True)   # 优先去重键
    fingerprint = Column(String, nullable=True, index=True)  # 无 DOI 时的指纹: 规范化标题+作者+年份+期号+页码

    title = Column(Text, nullable=False)
    title_zh = Column(Text, nullable=True)             # 若中文期刊或后续翻译填入
    abstract = Column(Text, nullable=True)
    abstract_zh = Column(Text, nullable=True)
    authors = Column(JSON, nullable=True)              # [{name, affiliation, orcid}, ...]

    journal_abbr = Column(String, nullable=True, index=True)  # 关联 journals.yaml；arxiv 论文为 NULL
    journal_name = Column(String, nullable=True)              # 冗余存便于显示（含 arXiv 子分类）

    pub_year = Column(Integer, nullable=True, index=True)
    pub_date = Column(String, nullable=True)           # YYYY-MM-DD 字符串，便于范围查询
    volume = Column(String, nullable=True)
    issue = Column(String, nullable=True)
    pages = Column(String, nullable=True)

    cited_by_count = Column(Integer, default=0)
    open_access_url = Column(String, nullable=True)
    pdf_url = Column(String, nullable=True)
    landing_page_url = Column(String, nullable=True)

    is_arxiv = Column(Boolean, default=False)
    arxiv_id = Column(String, nullable=True, index=True)
    arxiv_categories = Column(JSON, nullable=True)

    lang = Column(String, nullable=True)               # en | zh

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    sources = relationship("PaperSource", back_populates="paper")
    score = relationship("PaperScore", back_populates="paper", uselist=False)
    llm_output = relationship("LlmOutput", back_populates="paper", uselist=False)


# ============================================================
# 4. 论文来源关联：一篇论文可来自多个来源
# ============================================================
class PaperSource(Base):
    __tablename__ = "paper_sources"

    id = Column(Integer, primary_key=True)
    paper_id = Column(Integer, ForeignKey("papers.id"), nullable=False)
    raw_record_id = Column(Integer, ForeignKey("raw_records.id"), nullable=False)
    source = Column(String, nullable=False)
    is_primary = Column(Boolean, default=False)  # 第一个写入此论文的来源

    paper = relationship("Paper", back_populates="sources")

    __table_args__ = (
        UniqueConstraint("paper_id", "raw_record_id", name="uq_paper_raw"),
        Index("ix_paper_sources_paper", "paper_id"),
    )


# ============================================================
# 5. LLM 打分：AI 相关性 & 领域相关性
# ============================================================
class PaperScore(Base):
    __tablename__ = "paper_scores"

    paper_id = Column(Integer, ForeignKey("papers.id"), primary_key=True)
    ai_relevance = Column(Float, nullable=True)        # 0-5
    domain_relevance = Column(Float, nullable=True)    # 0-5
    keyword_hits_l1 = Column(Integer, default=0)       # 第一层关键词命中数
    keyword_hits_l2 = Column(Integer, default=0)
    keyword_hits_l3 = Column(Integer, default=0)
    scored_at = Column(DateTime, default=datetime.utcnow)
    model_used = Column(String, nullable=True)
    rationale = Column(Text, nullable=True)            # LLM 给出的简短理由

    paper = relationship("Paper", back_populates="score")


# ============================================================
# 6. LLM 输出：TL;DR、标签
# ============================================================
class LlmOutput(Base):
    __tablename__ = "llm_outputs"

    paper_id = Column(Integer, ForeignKey("papers.id"), primary_key=True)
    tldr_zh = Column(Text, nullable=True)              # 中文 200 字 TL;DR
    topic_tags = Column(JSON, nullable=True)           # [团队, 领导力, 招聘, 决策, 伦理, 人机协作, 营销, 消费者, ...]
    ai_type_tags = Column(JSON, nullable=True)         # [GenAI, LLM, ChatGPT, 算法管理, ...]
    generated_at = Column(DateTime, default=datetime.utcnow)
    model_used = Column(String, nullable=True)

    paper = relationship("Paper", back_populates="llm_output")


# ============================================================
# 7. 覆盖率审计：每本期刊每期的对账状态
# ============================================================
class CoverageGap(Base):
    __tablename__ = "coverage_gaps"

    id = Column(Integer, primary_key=True)
    journal_abbr = Column(String, nullable=False, index=True)
    year = Column(Integer, nullable=False)
    volume = Column(String, nullable=True)
    issue = Column(String, nullable=True)
    expected_count = Column(Integer, nullable=True)    # 出版商目录显示的应有篇数
    crossref_count = Column(Integer, default=0)
    openalex_count = Column(Integer, default=0)
    cnki_count = Column(Integer, default=0)
    wanfang_count = Column(Integer, default=0)
    nssd_count = Column(Integer, default=0)
    publisher_toc_count = Column(Integer, default=0)
    missing_dois = Column(JSON, nullable=True)         # 已知但未抓到的 DOI 列表
    last_audit_at = Column(DateTime, default=datetime.utcnow)
    notes = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("journal_abbr", "year", "volume", "issue", name="uq_coverage_jvi"),
    )


# ============================================================
# 8. 补全队列：待补摘要、PDF、OA 状态等
# ============================================================
class EnrichmentQueue(Base):
    __tablename__ = "enrichment_queue"

    id = Column(Integer, primary_key=True)
    paper_id = Column(Integer, ForeignKey("papers.id"), nullable=False)
    needs = Column(JSON, nullable=False)               # ["abstract", "pdf", "citations", "oa_status"]
    priority = Column(Integer, default=0)
    attempts = Column(Integer, default=0)
    last_attempt_at = Column(DateTime, nullable=True)
    status = Column(String, default="pending")         # pending | in_progress | done | failed
    last_error = Column(Text, nullable=True)


# ============================================================
# 9. LLM 队列：待打分、待生成 TL;DR
# ============================================================
class LlmQueue(Base):
    __tablename__ = "llm_queue"

    id = Column(Integer, primary_key=True)
    paper_id = Column(Integer, ForeignKey("papers.id"), nullable=False)
    task = Column(String, nullable=False)              # score | tldr | tags
    priority = Column(Integer, default=0)
    attempts = Column(Integer, default=0)
    last_attempt_at = Column(DateTime, nullable=True)
    status = Column(String, default="pending")
    last_error = Column(Text, nullable=True)


# ============================================================
# 工具函数
# ============================================================
def get_engine(db_path: str):
    return create_engine(f"sqlite:///{db_path}", future=True)


def init_db(db_path: str):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    return engine


def get_session(db_path: str):
    engine = get_engine(db_path)
    Session = sessionmaker(bind=engine, future=True)
    return Session()
