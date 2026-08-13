"""
LLM 双打分：AI 相关性 + 领域相关性 (0-5)，批量 10 篇/次。

策略：
- 输入：title + (abstract 截前 800 字) + journal_abbr
- 输出：[{id, ai, domain, reason}, ...]
- 写入 paper_scores 表
- 失败重试 3 次，仍失败则把该批拆成单篇兜底

成本控制：
- M3 单次推理约 1500 tokens 固定开销
- 批量 10 篇分摊后，单篇成本降到 1/10 左右
"""

import os
import sys
import json
import time
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from sqlalchemy import select, func

from src.db.schema import get_session, Paper, PaperScore
from src.llm.client import MiniMaxClient, extract_json

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "./data/papers.db")

BATCH_SIZE = 10
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
  4 = AI 是主要变量之一（如「与 ChatGPT 建立亲密关系」「AI 伴侣的使用体验」）
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

输出格式（严格 JSON 数组，无任何额外文字）：
[{"id": "p1", "ai": 5, "domain": 5, "reason": "ChatGPT 对用户访谈分析的影响"}, ...]
"""

USER_TEMPLATE = """请评分以下 {n} 篇论文：

{papers}

只输出 JSON 数组，每篇一个对象：[{{"id": "...", "ai": 0-5, "domain": 0-5, "reason": "≤30字"}}]"""


def fmt_paper(idx: int, p: Paper) -> str:
    abs_text = (p.abstract or "")[:ABS_TRUNC]
    abs_part = f"\n摘要: {abs_text}" if abs_text else "\n（无摘要）"
    return f"[p{idx}] 期刊={p.journal_abbr} 标题: {p.title}{abs_part}"


def score_batch(client: MiniMaxClient, papers: list[Paper]) -> list[dict]:
    """返回 [{id_idx, ai, domain, reason}, ...]，索引对应 papers 列表位置。"""
    body = "\n\n".join(fmt_paper(i + 1, p) for i, p in enumerate(papers))
    user = USER_TEMPLATE.format(n=len(papers), papers=body)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    # M2.7 推理可能很长，给足空间：基础 1200 + 每篇 100
    max_tok = 1500 + 120 * len(papers)
    data = client.chat(messages, max_tokens=max_tok, temperature=0.0)
    usage = client.usage(data)
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    parsed = extract_json(text)
    return parsed or [], usage


def run(limit: int = None, batch_size: int = BATCH_SIZE):
    session = get_session(DB_PATH)
    client = MiniMaxClient()
    try:
        # 选未打分的论文
        scored_ids = set(session.execute(select(PaperScore.paper_id)).scalars().all())
        all_papers = session.execute(select(Paper)).scalars().all()
        todo = [p for p in all_papers if p.id not in scored_ids]
        if limit:
            todo = todo[:limit]
        print(f"待打分: {len(todo)} 篇 (batch={batch_size})")

        total_in = total_out = total_reason = 0
        n_ok = n_fail = 0
        t0 = time.time()

        for batch_start in range(0, len(todo), batch_size):
            batch = todo[batch_start : batch_start + batch_size]
            try:
                scores, usage = score_batch(client, batch)
            except Exception as e:
                print(f"  ! batch {batch_start}: {e}")
                # 标记失败，跳过；后续可单篇重跑
                for p in batch:
                    if not session.get(PaperScore, p.id):
                        session.add(PaperScore(
                            paper_id=p.id,
                            ai_relevance=None, domain_relevance=None,
                            scored_at=datetime.utcnow(),
                            model_used=client.model,
                            rationale=f"ERROR: {str(e)[:200]}",
                        ))
                n_fail += len(batch)
                session.commit()
                continue

            total_in += usage.get("prompt_tokens", 0)
            total_out += usage.get("completion_tokens", 0)
            total_reason += (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)

            # scores 是 list of dict; 按位置对齐 papers (兼容 LLM 漏掉某条)
            score_by_idx = {}
            for s in scores:
                # id 形如 "p3"
                sid = str(s.get("id", "")).lower().lstrip("p")
                try:
                    idx = int(sid) - 1
                    score_by_idx[idx] = s
                except ValueError:
                    continue

            for i, p in enumerate(batch):
                s = score_by_idx.get(i)
                if s is None:
                    n_fail += 1
                    if not session.get(PaperScore, p.id):
                        session.add(PaperScore(
                            paper_id=p.id, ai_relevance=None, domain_relevance=None,
                            scored_at=datetime.utcnow(), model_used=client.model,
                            rationale="LLM 漏返回该条",
                        ))
                    continue
                ai = float(s.get("ai", 0))
                dom = float(s.get("domain", 0))
                reason = (s.get("reason") or "")[:200]

                ps = session.get(PaperScore, p.id)
                if ps:
                    ps.ai_relevance = ai
                    ps.domain_relevance = dom
                    ps.rationale = reason
                    ps.model_used = client.model
                    ps.scored_at = datetime.utcnow()
                else:
                    session.add(PaperScore(
                        paper_id=p.id,
                        ai_relevance=ai,
                        domain_relevance=dom,
                        rationale=reason,
                        model_used=client.model,
                        scored_at=datetime.utcnow(),
                    ))
                n_ok += 1
            session.commit()

            # 进度
            elapsed = time.time() - t0
            done = batch_start + batch_size
            print(f"  [{min(done, len(todo))}/{len(todo)}] in={total_in} out={total_out} (reason={total_reason}) elapsed={elapsed:.0f}s")

        print(f"\n完成: 成功 {n_ok} / 失败 {n_fail}")
        print(f"Token: prompt={total_in} completion={total_out} (reasoning={total_reason})")
        # 粗估成本（按 ¥1.2/M in + ¥8/M out）
        cost_cny = total_in / 1e6 * 1.2 + total_out / 1e6 * 8
        print(f"估算成本: ¥{cost_cny:.2f}")
    finally:
        session.close()
        client.close()


def report():
    session = get_session(DB_PATH)
    try:
        from collections import Counter
        scores = session.execute(select(PaperScore)).scalars().all()
        total = len(scores)
        ok = [s for s in scores if s.ai_relevance is not None]
        print(f"\n=== 打分汇总 ===")
        print(f"已打分: {total} (成功 {len(ok)}, 失败 {total - len(ok)})")
        if not ok:
            return
        ai_dist = Counter(int(s.ai_relevance) for s in ok)
        dom_dist = Counter(int(s.domain_relevance) for s in ok)
        print(f"\nAI 相关性分布:")
        for k in sorted(ai_dist):
            print(f"  {k}: {ai_dist[k]:>5}")
        print(f"\n领域相关性分布:")
        for k in sorted(dom_dist):
            print(f"  {k}: {dom_dist[k]:>5}")
        # 双 ≥3 的总数（默认展示阈值）
        both = sum(1 for s in ok if (s.ai_relevance or 0) >= 3 and (s.domain_relevance or 0) >= 3)
        print(f"\n双 ≥3 (AI 相关 + 领域相关): {both} 篇")
    finally:
        session.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["run", "report"])
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--batch", type=int, default=BATCH_SIZE)
    args = p.parse_args()
    if args.cmd == "run":
        run(limit=args.limit, batch_size=args.batch)
    else:
        report()
