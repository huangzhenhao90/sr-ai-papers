"""
批量给双≥3 的论文翻译标题为中文，写入 papers.title_zh。
- batch=10，并发 50（推理少，输出小，吞吐高）
- 仅对 title_zh 为空的论文做
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
from sqlalchemy import select, text, update

from src.db.schema import get_session, Paper, PaperScore
from src.llm.client import MiniMaxClient, extract_json

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "./data/papers.db")

BATCH_SIZE = 10
N_WORKERS = 50

SYSTEM_PROMPT = """你是一名学术论文标题中译专家。任务：把一批英文学术论文标题译成中文。
要求：
- 学术风格、不口语化
- 保留专有名词原文（人名、模型名如 ChatGPT/LLM/GPT-4 不译）
- 副标题用冒号连接
- 不超过 40 字

【关键】必须为每一篇返回一个对象，id 严格对应输入的 [p1] [p2] 编号。

输出格式（严格 JSON 数组，无 markdown 包裹）:
[{"id":"p1","zh":"中文标题"},{"id":"p2","zh":"..."}]
"""


USER_TEMPLATE = """请翻译以下 {n} 个英文标题:

{titles}

输出长度为 {n} 的 JSON 数组。"""


stats = {"in": 0, "out": 0, "ok": 0, "fail": 0, "done": 0}
stats_lock = threading.Lock()


def fmt(idx, p):
    return f"[p{idx}] {p['title']}"


def process_batch(client, batch_idx, batch):
    body = "\n".join(fmt(i + 1, p) for i, p in enumerate(batch))
    user = USER_TEMPLATE.format(n=len(batch), titles=body)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    # lightning 模型 + 短输出：reasoning 500-800 + 输出每条 ~30 token
    max_tok = 1500 + 80 * len(batch)
    try:
        data = client.chat(messages, max_tokens=max_tok, temperature=0.0)
    except Exception as e:
        return [(p["id"], None, f"ERR: {e}") for p in batch]

    usage = client.usage(data)
    raw = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    parsed = extract_json(raw) or []

    with stats_lock:
        stats["in"] += usage.get("prompt_tokens", 0)
        stats["out"] += usage.get("completion_tokens", 0)

    by_idx = {}
    for s in parsed:
        sid = str(s.get("id", "")).lower().lstrip("p")
        try:
            by_idx[int(sid) - 1] = s.get("zh") or ""
        except (ValueError, TypeError):
            continue

    if len(by_idx) < len(batch) // 2:
        print(f"  ! batch {batch_idx}: 仅解析 {len(by_idx)}/{len(batch)} | raw[:200]: {raw[:200]!r}")

    return [(p["id"], by_idx.get(i), None) for i, p in enumerate(batch)]


def run(min_ai=3.0, min_dom=3.0, batch_size=BATCH_SIZE, n_workers=N_WORKERS,
        limit=None, candidate_ids=None):
    session = get_session(DB_PATH)
    client = MiniMaxClient()
    try:
        wanted_ids = None
        wanted_order = {}
        if candidate_ids is not None:
            wanted_ids = {int(pid) for pid in candidate_ids}
            wanted_order = {int(pid): i for i, pid in enumerate(candidate_ids)}

        rows = session.execute(text("""
            SELECT p.id, p.title
            FROM papers p
            JOIN paper_scores s ON s.paper_id = p.id
            WHERE s.ai_relevance >= :ai AND s.domain_relevance >= :dom
              AND (p.title_zh IS NULL OR p.title_zh = '')
              AND p.title IS NOT NULL
            ORDER BY p.pub_year DESC, p.id
        """), {"ai": min_ai, "dom": min_dom}).all()

        todo = [
            {"id": r[0], "title": r[1]}
            for r in rows
            if wanted_ids is None or r[0] in wanted_ids
        ]
        if wanted_ids is not None:
            todo.sort(key=lambda p: wanted_order.get(p["id"], len(wanted_order)))
        if limit:
            todo = todo[:limit]
        print(f"待翻译: {len(todo)} 篇 (batch={batch_size}, workers={n_workers})")
        if not todo:
            return

        batches = [todo[i : i + batch_size] for i in range(0, len(todo), batch_size)]
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futs = {pool.submit(process_batch, client, i, b): i for i, b in enumerate(batches)}
            for fut in as_completed(futs):
                results = fut.result()
                for paper_id, zh, err in results:
                    if zh:
                        session.execute(
                            update(Paper).where(Paper.id == paper_id).values(title_zh=zh)
                        )
                        with stats_lock:
                            stats["ok"] += 1
                    else:
                        with stats_lock:
                            stats["fail"] += 1
                    with stats_lock:
                        stats["done"] += 1
                session.commit()
                elapsed = time.time() - t0
                print(f"  [{stats['done']}/{len(todo)}] ok={stats['ok']} fail={stats['fail']} "
                      f"in={stats['in']} out={stats['out']} elapsed={elapsed:.0f}s")

        cost = stats["in"] / 1e6 * 1.2 + stats["out"] / 1e6 * 8
        print(f"\n完成 用时 {(time.time()-t0)/60:.1f} 分钟  估算 ¥{cost:.2f}")
    finally:
        session.close()
        client.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--batch", type=int, default=BATCH_SIZE)
    p.add_argument("--workers", type=int, default=N_WORKERS)
    p.add_argument("--ids", default=None, help="Comma-separated paper IDs to process")
    args = p.parse_args()
    ids = [int(x) for x in args.ids.split(",") if x.strip()] if args.ids else None
    run(batch_size=args.batch, n_workers=args.workers, limit=args.limit, candidate_ids=ids)
