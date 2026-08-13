"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { readFavorites, toggleFavorite } from "@/lib/favorites";
import { readReadIds, markRead } from "@/lib/read";
import { track } from "@/lib/analytics";
import { comparePapersByRecent } from "@/lib/paperSort";

export type Paper = {
  id: number;
  doi: string | null;
  ingested_at: string | null;
  title: string;
  title_zh: string | null;
  journal: string | null;
  year: number | null;
  date: string | null;
  authors: string[];
  url: string | null;
  pdf_url: string | null;
  cited_by: number;
  ai_score: number;
  domain_score: number;
  ai_reason: string;
  tldr: string | null;
  topic_tags: string[];
  ai_type_tags: string[];
};

type Meta = {
  totals: { papers_indexed: number; papers_scored: number; papers_ai_relevant: number };
  facets: {
    years: Record<string, number>;
    journals: Record<string, number>;
    topic_tags: Record<string, number>;
    ai_type_tags: Record<string, number>;
  };
  generated_at: string;
};

const ARXIV_PREFIX = "arXiv:";

export default function PaperList({
  papers,
  meta,
  title,
  subtitle,
  banner,
  rightHeader,
}: {
  papers: Paper[];
  meta: Meta;
  title?: string;
  subtitle?: string;
  banner?: React.ReactNode;
  rightHeader?: React.ReactNode;
}) {
  // 已读 ID
  const [readIds, setReadIds] = useState<Set<number>>(new Set());
  useEffect(() => {
    setReadIds(new Set(readReadIds()));
    const update = () => setReadIds(new Set(readReadIds()));
    window.addEventListener("read-changed", update);
    return () => window.removeEventListener("read-changed", update);
  }, []);

  // 筛选状态：从 URL querystring 读，写回 URL（不刷新页面）
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const q = params.get("q") || "";
  const year = params.get("year") ? Number(params.get("year")) : null;
  const journal = params.get("journal") || null;
  const topicTag = params.get("topic") || null;
  const aiType = params.get("aitype") || null;
  const minAi = params.get("minai") ? Number(params.get("minai")) : 3;
  const sort = (params.get("sort") as "recent" | "ai_score" | "cited") || "recent";

  const updateParam = useCallback((key: string, value: string | number | null) => {
    const sp = new URLSearchParams(params.toString());
    if (value == null || value === "" || (key === "minai" && value === 3) || (key === "sort" && value === "recent")) {
      sp.delete(key);
    } else {
      sp.set(key, String(value));
    }
    const qs = sp.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    // 埋点：搜索框输入太频繁，跳过
    if (key !== "q" && value != null && value !== "") {
      track("filter_apply", { key, value: String(value) });
    }
  }, [params, pathname, router]);

  const setQ = (v: string) => updateParam("q", v);
  const setYear = (v: number | null) => updateParam("year", v);
  const setJournal = (v: string | null) => updateParam("journal", v);
  const setTopicTag = (v: string | null) => updateParam("topic", v);
  const setAiType = (v: string | null) => updateParam("aitype", v);
  const setMinAi = (v: number) => updateParam("minai", v);
  const setSort = (v: "recent" | "ai_score" | "cited") => updateParam("sort", v);

  // 应用所有筛选除某一维 —— 用于动态 facets 计算（漏斗逻辑）
  const filteredExcept = (excludeKey: "year" | "journal" | "topic" | "aiType" | null) => {
    let res = papers;
    if (year && excludeKey !== "year") res = res.filter((p) => p.year === year);
    if (journal && excludeKey !== "journal") {
      if (journal === "arXiv") {
        res = res.filter((p) => p.journal?.startsWith(ARXIV_PREFIX) || p.journal === "arXiv");
      } else {
        res = res.filter((p) => p.journal === journal);
      }
    }
    if (topicTag && excludeKey !== "topic") res = res.filter((p) => p.topic_tags?.includes(topicTag));
    if (aiType && excludeKey !== "aiType") res = res.filter((p) => p.ai_type_tags?.includes(aiType));
    if (minAi > 3) res = res.filter((p) => p.ai_score >= minAi);
    if (q.trim()) {
      const qq = q.trim().toLowerCase();
      res = res.filter(
        (p) =>
          p.title?.toLowerCase().includes(qq) ||
          p.title_zh?.includes(qq) ||
          p.tldr?.toLowerCase().includes(qq) ||
          p.authors?.some((a) => a?.toLowerCase().includes(qq)) ||
          p.topic_tags?.some((t) => t.includes(qq)) ||
          p.ai_type_tags?.some((t) => t.includes(qq))
      );
    }
    return res;
  };

  // 实际展示的论文 = 应用所有筛选
  const filtered = useMemo(() => {
    let res = filteredExcept(null);
    if (sort === "ai_score") res = [...res].sort((a, b) => (b.ai_score - a.ai_score) || comparePapersByRecent(a, b));
    else if (sort === "cited") res = [...res].sort((a, b) => (b.cited_by - a.cited_by) || comparePapersByRecent(a, b));
    else res = [...res].sort(comparePapersByRecent);
    return res;
  }, [papers, q, year, journal, topicTag, aiType, minAi, sort]);

  // 动态 facets：基于"应用了其他维度但未应用本维"的子集来计数
  const facetYears = useMemo(() => countBy(filteredExcept("year"), (p) => p.year), [papers, q, journal, topicTag, aiType, minAi]);
  const facetJournals = useMemo(() => {
    // arXiv 子分类要折叠成一个 "arXiv"
    const base = filteredExcept("journal");
    const c = new Map<string, number>();
    for (const p of base) {
      if (!p.journal) continue;
      if (p.journal.startsWith(ARXIV_PREFIX)) {
        c.set("arXiv", (c.get("arXiv") || 0) + 1);
        c.set(p.journal, (c.get(p.journal) || 0) + 1); // 保留子分类供展开
      } else {
        c.set(p.journal, (c.get(p.journal) || 0) + 1);
      }
    }
    return c;
  }, [papers, q, year, topicTag, aiType, minAi]);

  const facetTopics = useMemo(() => {
    const base = filteredExcept("topic");
    const c = new Map<string, number>();
    for (const p of base) for (const t of p.topic_tags || []) c.set(t, (c.get(t) || 0) + 1);
    return c;
  }, [papers, q, year, journal, aiType, minAi]);

  const facetAi = useMemo(() => {
    const base = filteredExcept("aiType");
    const c = new Map<string, number>();
    for (const p of base) for (const t of p.ai_type_tags || []) c.set(t, (c.get(t) || 0) + 1);
    return c;
  }, [papers, q, year, journal, topicTag, minAi]);

  return (
    <div className="grid md:grid-cols-[260px_1fr] gap-6">
      <aside className="space-y-4 text-sm">
        <div className="border border-stone-200 rounded p-3 bg-white">
          <div className="text-xs text-stone-500 mb-1">{title || "SR × AI Papers"}</div>
          <div className="text-[11px] text-stone-400 leading-relaxed mb-2">
            {subtitle ? (
              subtitle
            ) : (
              <>
                追踪 25 个传播 / 社会心理 / 人际关系 / HCI 期刊会议 + arXiv 中
                <span className="text-accent">与 AI 社交关系相关的研究</span>（2023 至今）。
                每篇用 AI 自动判断相关度、生成 200 字中文摘要。
                <Link href="/about" className="text-accent hover:underline ml-1">了解更多</Link>
              </>
            )}
          </div>
          <div className="text-2xl font-mono font-semibold">{filtered.length}</div>
          <div className="text-xs text-stone-500 mt-1">
            当前筛选 / 共 {papers.length} 篇
          </div>
        </div>

        <FilterGroup label="年份">
          <Chips
            items={sortedEntries(facetYears, "desc")}
            active={year ? String(year) : null}
            onPick={(v) => setYear(v ? Number(v) : null)}
          />
        </FilterGroup>

        <FilterGroup label="期刊">
          <JournalChips
            counts={facetJournals}
            active={journal}
            onPick={(v) => setJournal(v)}
          />
        </FilterGroup>

        <FilterGroup label="主题">
          <Chips items={topByCount(facetTopics, 20)} active={topicTag} onPick={setTopicTag} />
        </FilterGroup>

        <FilterGroup label="AI 类型">
          <Chips items={topByCount(facetAi, 14)} active={aiType} onPick={setAiType} />
        </FilterGroup>

        <FilterGroup label="AI 相关性 ≥">
          <div className="flex gap-2">
            {[3, 4, 5].map((v) => (
              <button
                key={v}
                onClick={() => setMinAi(v)}
                className={`chip ${minAi === v ? "chip-on" : ""}`}
                title={
                  v === 3 ? "实质涉及 AI（含背景讨论）"
                  : v === 4 ? "AI 是主要变量之一"
                  : "核心议题就是 AI / GenAI / LLM / 算法决策"
                }
              >
                {v}
              </button>
            ))}
          </div>
          <details className="mt-1.5">
            <summary className="text-[11px] text-stone-400 cursor-pointer hover:text-accent">如何打分？</summary>
            <div className="text-[11px] text-stone-500 mt-1 leading-relaxed">
              每篇用 LLM (DeepSeek-V3.2) 打两个分（0-5）：
              <strong>AI 相关性</strong>（论文与 AI 议题的关联度）和
              <strong>领域相关性</strong>（与社交关系 / 传播 / 社会心理的关联度）。默认仅展示双 ≥ 3。
              满分 5 = 核心议题；4 = 主要变量；3 = 实质涉及；0-2 = 不相关。
            </div>
          </details>
        </FilterGroup>

        {(year || journal || topicTag || aiType || minAi > 3 || q) && (
          <button
            onClick={() => {
              setYear(null); setJournal(null); setTopicTag(null); setAiType(null); setMinAi(3); setQ("");
            }}
            className="text-xs text-stone-500 underline hover:text-accent"
          >
            清空所有筛选
          </button>
        )}
      </aside>

      <section>
        {banner}
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between mb-3">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索标题、摘要、作者、标签…"
            className="w-full md:w-80 px-3 py-2 border border-stone-300 rounded text-sm focus:outline-none focus:border-accent"
          />
          <div className="flex items-center gap-2 text-sm">
            {rightHeader}
            <span className="text-stone-500">排序</span>
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as any)}
              className="px-2 py-1 border border-stone-300 rounded bg-white"
            >
              <option value="recent">最新</option>
              <option value="ai_score">AI 分数</option>
              <option value="cited">引用数</option>
            </select>
          </div>
        </div>

        <div className="text-xs text-stone-500 mb-3">
          {filtered.length} 篇 · 默认仅显示 AI 相关性 ≥ 3 且 领域相关性 ≥ 3
        </div>

        <ul className="space-y-3">
          {filtered.slice(0, 200).map((p) => (
            <PaperCard key={p.id} p={p} isRead={readIds.has(p.id)} />
          ))}
        </ul>
        {filtered.length > 200 && (
          <div className="text-center text-stone-400 text-xs py-4">
            仅展示前 200 条 · 通过筛选缩小范围
          </div>
        )}
        {filtered.length === 0 && (
          <div className="text-center text-stone-400 text-sm py-12">无符合的论文</div>
        )}
      </section>
    </div>
  );
}

// ---------- helpers ----------
function countBy<T>(arr: T[], keyFn: (x: T) => string | number | null): Map<string, number> {
  const c = new Map<string, number>();
  for (const x of arr) {
    const k = keyFn(x);
    if (k == null) continue;
    const ks = String(k);
    c.set(ks, (c.get(ks) || 0) + 1);
  }
  return c;
}
function sortedEntries(m: Map<string, number>, order: "asc" | "desc" = "desc"): [string, number][] {
  const arr = Array.from(m.entries());
  arr.sort((a, b) => (order === "desc" ? Number(b[0]) - Number(a[0]) : Number(a[0]) - Number(b[0])));
  return arr;
}
function topByCount(m: Map<string, number>, n: number): [string, number][] {
  return Array.from(m.entries()).sort((a, b) => b[1] - a[1]).slice(0, n);
}

// ---------- 子组件 ----------
function FilterGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs text-stone-500 mb-1.5 font-medium">{label}</div>
      {children}
    </div>
  );
}

function Chips({
  items, active, onPick,
}: { items: [string, number][]; active: string | null; onPick: (v: string | null) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map(([name, n]) => {
        const on = active === name;
        return (
          <button
            key={name}
            onClick={() => onPick(on ? null : name)}
            className={`chip ${on ? "chip-on" : ""}`}
          >
            {name}
            <span className="ml-1 text-[10px] opacity-60">{n}</span>
          </button>
        );
      })}
      {items.length === 0 && <span className="text-xs text-stone-400">—</span>}
    </div>
  );
}

function JournalChips({
  counts, active, onPick,
}: {
  counts: Map<string, number>;
  active: string | null;
  onPick: (v: string | null) => void;
}) {
  // 分两组：常规期刊 + arXiv 子分类
  const regular: [string, number][] = [];
  const arxivSubs: [string, number][] = [];
  let arxivTotal = 0;
  for (const [name, n] of counts) {
    if (name === "arXiv") arxivTotal = n;
    else if (name.startsWith(ARXIV_PREFIX)) arxivSubs.push([name, n]);
    else regular.push([name, n]);
  }
  regular.sort((a, b) => b[1] - a[1]);
  arxivSubs.sort((a, b) => b[1] - a[1]);

  // 选中 arXiv 或它的子分类时，自动展开子分类列表
  const arxivExpanded = active === "arXiv" || (active?.startsWith(ARXIV_PREFIX) ?? false);

  return (
    <div className="flex flex-wrap gap-1.5">
      {regular.slice(0, 12).map(([name, n]) => {
        const on = active === name;
        return (
          <button
            key={name}
            onClick={() => onPick(on ? null : name)}
            className={`chip ${on ? "chip-on" : ""}`}
          >
            {name}
            <span className="ml-1 text-[10px] opacity-60">{n}</span>
          </button>
        );
      })}
      {arxivTotal > 0 && (
        <>
          <button
            onClick={() => onPick(active === "arXiv" ? null : "arXiv")}
            className={`chip ${active === "arXiv" ? "chip-on" : ""}`}
            title="点击展开 arXiv 子分类"
          >
            arXiv
            <span className="ml-1 text-[10px] opacity-60">{arxivTotal}</span>
          </button>
          {arxivExpanded && arxivSubs.length > 0 && (
            <div className="basis-full mt-1 pl-3 border-l-2 border-stone-200 flex flex-wrap gap-1.5">
              {arxivSubs.map(([name, n]) => {
                const on = active === name;
                return (
                  <button
                    key={name}
                    onClick={() => onPick(on ? null : name)}
                    className={`chip text-[11px] ${on ? "chip-on" : ""}`}
                  >
                    {name.replace(ARXIV_PREFIX, "")}
                    <span className="ml-1 text-[10px] opacity-60">{n}</span>
                  </button>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function PaperCard({ p, isRead }: { p: Paper; isRead: boolean }) {
  const [fav, setFav] = useState(false);
  useEffect(() => { setFav(readFavorites().has(p.id)); }, [p.id]);

  return (
    <li
      onClick={() => { if (!isRead) markRead(p.id); }}
      className={`border rounded p-3 transition-colors cursor-default ${
        isRead
          ? "border-stone-200 bg-stone-50/70 hover:border-accent"
          : "border-stone-200 bg-white hover:border-accent"
      }`}>
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <Link href={`/papers/${p.id}`} className="block flex-1 min-w-0">
              <h3 className={`font-medium leading-snug hover:text-accent ${isRead ? "text-stone-500" : ""}`}>
                {isRead && <span className="text-stone-300 mr-1.5 text-xs align-middle" title="已读">●</span>}
                {p.title}
              </h3>
              {p.title_zh && (
                <div className={`text-sm mt-0.5 ${isRead ? "text-stone-400" : "text-stone-600"}`}>{p.title_zh}</div>
              )}
            </Link>
            <button
              onClick={(e) => {
                e.preventDefault();
                const nowFav = toggleFavorite(p.id);
                setFav(nowFav);
                track("favorite_toggle", { paper_id: p.id, action: nowFav ? "add" : "remove" });
              }}
              className={`flex-shrink-0 text-lg leading-none p-1 -m-1 ${fav ? "text-amber-500" : "text-stone-300 hover:text-amber-400"}`}
              title={fav ? "取消收藏" : "加入收藏"}
              aria-label={fav ? "取消收藏" : "加入收藏"}
            >
              {fav ? "★" : "☆"}
            </button>
          </div>
          <div className="text-xs text-stone-500 mt-1 flex flex-wrap gap-x-2 gap-y-0.5 items-center">
            <span className="font-mono">{p.journal}</span>
            {p.date && <span>· {p.date}</span>}
            {!p.date && p.year && <span>· {p.year}</span>}
            {p.authors?.length ? (
              <span>· {p.authors.slice(0, 3).join(", ")}{p.authors.length > 3 ? " 等" : ""}</span>
            ) : null}
            <span className="text-stone-400">· 引用 {p.cited_by} · AI {p.ai_score?.toFixed(0)}</span>
          </div>
          {p.tldr && (
            <p className="text-sm text-stone-700 mt-2 leading-relaxed">{p.tldr}</p>
          )}
          <div className="flex flex-wrap gap-1 mt-2">
            {p.topic_tags?.slice(0, 5).map((t) => (
              <span key={t} className="chip">{t}</span>
            ))}
            {p.ai_type_tags?.slice(0, 3).map((t) => (
              <span key={"ai-" + t} className="chip border-accent/40 text-accent">{t}</span>
            ))}
          </div>
          <div className="flex gap-3 mt-2 text-xs">
            {p.url && <a href={p.url} target="_blank" rel="noopener" className="text-accent hover:underline">原文 ↗</a>}
            {p.pdf_url && <a href={p.pdf_url} target="_blank" rel="noopener" className="text-accent hover:underline">PDF ↗</a>}
            {p.doi && <span className="text-stone-400 font-mono">{p.doi}</span>}
          </div>
        </div>
      </div>
    </li>
  );
}
