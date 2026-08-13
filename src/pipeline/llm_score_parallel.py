"""
并发版 LLM 打分。
- batch=25 摊薄推理开销
- 4 个 worker 并发请求 minimax
- 主线程负责调度 + 写库（避免 SQLite 写锁）

打分维度：AI 相关性 + 社交关系相关性（0-5）。
"""

import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from sqlalchemy import select

from src.db.schema import get_session, Paper, PaperScore
from src.llm.client import MiniMaxClient, extract_json

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "./data/papers.db")

BATCH_SIZE = 25
N_WORKERS = 4
ABS_TRUNC = 800

SYSTEM_PROMPT = """你是一名学术论文相关性评判专家。任务：对一批传播学/社会心理/人际关系/HCI 期刊及会议论文，
严格判断每篇与 (1) AI 议题、(2) 社交关系领域 的相关性。

【领域定义】社交关系必须看到这些实质成分之一：
- 人际/人机关系研究（关系形成、维系、破裂；友谊、亲密关系、浪漫关系、陪伴关系）
- 社会互动与情感连接（互动质量、情感支持、共情、信任、依恋、归属感、孤独感）
- 人与 AI/机器人的关系（拟社会关系、AI 伴侣、聊天机器人关系、人机信任、拟人化）
- 传播学/CMC 实质议题（社交媒体互动、计算机中介传播、在线社群、拟社会互动）
- 社会心理学视角（自我表露、社会临场、人际吸引、社会比较、群体过程）

【打分标准】
- ai_relevance:
  5 = 论文核心议题就是 AI / GenAI / LLM / 智能体
  4 = AI 是主要变量之一
  3 = 论文实质涉及 AI，但 AI 不是中心
  2 = 仅在引言/讨论中提及 AI 作为背景
  1 = 字面提到 algorithm/automation 但与 AI 无关
  0 = 完全无关

- domain_relevance:
  5 = 核心人际/人机关系或社交互动研究（明确的关系、互动、情感连接、陪伴对象研究）
  4 = 强相关（社会心理学/CMC 实质内容、明确的互动或关系成分）
  3 = 中等相关：论文有明确的社会/关系视角或社交成分，但不是核心议题
  2 = 弱相关：仅在引言/相关工作提及社交/关系，论文本身不研究关系
  1 = 极弱：仅分类号落在 cs.HC/cs.CY，但内容与社交关系无实质关联
  0 = 完全无关

【反例（必须给 domain ≤ 2）】即使论文 arXiv 分类是 cs.HC/cs.CY，出现以下情形之一即判 domain ≤ 2：
- 纯算法/ML/统计技术（推荐算法、NLP 模型、检索系统、训练方法），无社会/关系维度
- 单智能体任务求解（对话生成质量评测、Agent 工具使用、benchmark），不涉及用户关系/互动
- 纯工程/系统/架构（无用户视角、无社交成分）
- 生物医学/生理学/基因组学
- 内容审核/内容安全（除非明确从用户/关系互动角度研究）
- 「social」仅出现在文献综述或未来工作中

【判定要诀】不要被分类号（cs.HC / cs.CY）或关键词字面（social / interaction / agent）迷惑。
必须读到摘要里有真实的人际/人机关系、社会互动、情感连接或陪伴体验研究，才能给 domain ≥ 3。
模棱两可时，倾向给 domain = 2（不通过）而非 domain = 3（卡线通过）。

【关键】必须为输入的每一篇论文返回一个 JSON 对象，id 严格对应 [p1] [p2] ... 编号。
即使无法判断，也要给 0 分而不是省略。

输出格式（严格 JSON 数组，无任何额外文字、无 markdown 包裹）：
[{"id": "p1", "ai": 5, "domain": 5, "reason": "..."}, {"id": "p2", "ai": 0, "domain": 5, "reason": "..."}, ...]
"""

USER_TEMPLATE = """请评分以下 {n} 篇论文（务必每篇都返回 JSON）：

{papers}

只输出长度为 {n} 的 JSON 数组，每篇一条 {{"id":"pN","ai":0-5,"domain":0-5,"reason":"≤30字"}}"""


def fmt_paper_dict(idx: int, p: dict) -> str:
    abs_text = (p.get("abstract") or "")[:ABS_TRUNC]
    abs_part = f"\n摘要: {abs_text}" if abs_text else "\n（无摘要）"
    return f"[p{idx}] 期刊={p.get('journal_abbr')} 标题: {p.get('title')}{abs_part}"


def score_one_batch(client: MiniMaxClient, papers: list[dict]) -> tuple[list[dict], dict, str]:
    """返回 (scores, usage, raw_text)。papers 是 dict 列表（避免 ORM 跨线程懒加载）。失败抛异常。"""
    body = "\n\n".join(fmt_paper_dict(i + 1, p) for i, p in enumerate(papers))
    user = USER_TEMPLATE.format(n=len(papers), papers=body)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    # M3 推理 ~1500 + 每篇输出 ~80 字符 ≈ 100 token，留 1.5 倍冗余
    max_tok = 2500 + 200 * len(papers)
    data = client.chat(messages, max_tokens=max_tok, temperature=0.0)
    usage = client.usage(data)
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    parsed = extract_json(text) or []
    return parsed, usage, text


# 全局统计（受锁保护）
stats_lock = threading.Lock()
total_in = total_out = total_reason = 0
n_done_papers = 0
n_ok = n_fail = 0


def process_batch(client: MiniMaxClient, batch_idx: int, batch: list[dict]) -> list[tuple]:
    """worker：跑一个 batch（输入纯 dict 避免 ORM 跨线程问题）。
    返回需写库的 (paper_id, score_data 或 None/error) 列表。"""
    global total_in, total_out, total_reason, n_done_papers, n_ok, n_fail
    try:
        scores, usage, raw_text = score_one_batch(client, batch)
    except Exception as e:
        return [(p["id"], {"error": str(e)[:200]}) for p in batch]

    with stats_lock:
        total_in += usage.get("prompt_tokens", 0)
        total_out += usage.get("completion_tokens", 0)
        total_reason += (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)

    score_by_idx = {}
    for s in scores:
        sid = str(s.get("id", "")).lower().lstrip("p")
        try:
            idx = int(sid) - 1
            score_by_idx[idx] = s
        except (ValueError, TypeError):
            continue

    if len(score_by_idx) < len(batch) // 2:
        print(f"  ! batch_idx={batch_idx} 仅解析 {len(score_by_idx)}/{len(batch)} raw[:300]: {raw_text[:300]!r}")

    out = []
    for i, p in enumerate(batch):
        s = score_by_idx.get(i)
        if s is None:
            out.append((p["id"], None))
        else:
            out.append((p["id"], {
                "ai": float(s.get("ai", 0) or 0),
                "domain": float(s.get("domain", 0) or 0),
                "reason": (s.get("reason") or "")[:200],
            }))
    return out


def run(limit: int = None, batch_size: int = BATCH_SIZE, n_workers: int = N_WORKERS,
        candidate_ids: list[int] | None = None):
    global total_in, total_out, total_reason, n_done_papers, n_ok, n_fail
    session = get_session(DB_PATH)
    client = MiniMaxClient()
    try:
        scored_ids = set(session.execute(
            select(PaperScore.paper_id).where(PaperScore.ai_relevance.is_not(None))
        ).scalars().all())
        wanted_ids = None
        wanted_order = {}
        if candidate_ids is not None:
            wanted_ids = {int(pid) for pid in candidate_ids}
            wanted_order = {int(pid): i for i, pid in enumerate(candidate_ids)}
            if not wanted_ids:
                print("待打分: 0 篇")
                return
        # 用 SQL 直接查需要字段，转成纯 dict 列表（避免跨线程 ORM 问题）
        from sqlalchemy import text
        rows = session.execute(text(
            "SELECT id, title, abstract, journal_abbr FROM papers"
        )).all()
        todo = [
            {"id": r[0], "title": r[1], "abstract": r[2], "journal_abbr": r[3]}
            for r in rows
            if r[0] not in scored_ids and (wanted_ids is None or r[0] in wanted_ids)
        ]
        if wanted_ids is not None:
            todo.sort(key=lambda p: wanted_order.get(p["id"], len(wanted_order)))
        if limit:
            todo = todo[:limit]
        print(f"待打分: {len(todo)} 篇 (batch={batch_size}, workers={n_workers})")

        # 切分 batches（每个元素是 dict）
        batches = [todo[i : i + batch_size] for i in range(0, len(todo), batch_size)]
        print(f"共 {len(batches)} 个 batch")

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            future_to_idx = {pool.submit(process_batch, client, i, b): i for i, b in enumerate(batches)}
            for fut in as_completed(future_to_idx):
                results = fut.result()
                # 主线程写库
                for paper_id, score_data in results:
                    if score_data is None:
                        # 漏返回 - 标记失败
                        if not session.get(PaperScore, paper_id):
                            session.add(PaperScore(
                                paper_id=paper_id, scored_at=datetime.utcnow(),
                                model_used=client.model, rationale="LLM 漏返回该条",
                            ))
                        with stats_lock:
                            n_fail += 1
                            n_done_papers += 1
                    elif "error" in score_data:
                        ps = session.get(PaperScore, paper_id)
                        if ps:
                            ps.scored_at = datetime.utcnow()
                            ps.model_used = client.model
                            ps.rationale = f"ERROR: {score_data['error']}"
                        else:
                            session.add(PaperScore(
                                paper_id=paper_id, scored_at=datetime.utcnow(),
                                model_used=client.model, rationale=f"ERROR: {score_data['error']}",
                            ))
                        with stats_lock:
                            n_fail += 1
                            n_done_papers += 1
                    else:
                        ps = session.get(PaperScore, paper_id)
                        if ps:
                            ps.ai_relevance = score_data["ai"]
                            ps.domain_relevance = score_data["domain"]
                            ps.rationale = score_data["reason"]
                            ps.model_used = client.model
                            ps.scored_at = datetime.utcnow()
                        else:
                            session.add(PaperScore(
                                paper_id=paper_id,
                                ai_relevance=score_data["ai"],
                                domain_relevance=score_data["domain"],
                                rationale=score_data["reason"],
                                model_used=client.model,
                                scored_at=datetime.utcnow(),
                            ))
                        with stats_lock:
                            n_ok += 1
                            n_done_papers += 1
                session.commit()

                elapsed = time.time() - t0
                pct = n_done_papers * 100 // max(len(todo), 1)
                rate = n_done_papers / max(elapsed, 1)
                eta = (len(todo) - n_done_papers) / max(rate, 0.01)
                print(f"  [{n_done_papers}/{len(todo)} {pct}%] ok={n_ok} fail={n_fail} "
                      f"in={total_in} out={total_out} reason={total_reason} "
                      f"elapsed={elapsed:.0f}s ETA={eta:.0f}s")

        print(f"\n完成: 成功 {n_ok} / 失败 {n_fail} / 用时 {(time.time()-t0)/60:.1f} 分钟")
        cost_cny = total_in / 1e6 * 1.2 + total_out / 1e6 * 8
        print(f"Token: in={total_in} out={total_out} (reasoning={total_reason})")
        print(f"估算成本: ¥{cost_cny:.2f}")
    finally:
        session.close()
        client.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--batch", type=int, default=BATCH_SIZE)
    p.add_argument("--workers", type=int, default=N_WORKERS)
    p.add_argument("--ids", default=None, help="Comma-separated paper IDs to score")
    args = p.parse_args()
    ids = [int(x) for x in args.ids.split(",") if x.strip()] if args.ids else None
    run(limit=args.limit, batch_size=args.batch, n_workers=args.workers, candidate_ids=ids)
