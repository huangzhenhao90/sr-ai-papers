"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { markRead } from "@/lib/read";
import { track } from "@/lib/analytics";
import DataLoadError from "@/components/DataLoadError";
import { errorMessage, fetchJson } from "@/lib/fetchJson";

type Paper = {
  id: number;
  doi: string | null;
  title: string;
  title_zh: string | null;
  journal: string | null;
  year: number | null;
  date: string | null;
  volume: string | null;
  issue: string | null;
  authors: string[];
  authors_full: { name: string; affiliation?: string[]; orcid?: string }[];
  abstract: string | null;
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

export default function PaperDetail() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = Number(params.id);
  const [p, setP] = useState<Paper | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  // 检测是否有"上一页"可回，否则 fallback 到 /
  const [canGoBack, setCanGoBack] = useState(false);
  useEffect(() => {
    setCanGoBack(typeof window !== "undefined" && window.history.length > 1);
  }, []);
  const goBack = () => {
    if (canGoBack) router.back();
    else router.push("/");
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchJson<Paper[]>("/data/papers_full.json")
      .then((all: Paper[]) => {
        if (cancelled) return;
        const found = all.find((x) => x.id === id) || null;
        setP(found);
        if (found) {
          markRead(found.id);
          track("paper_open", {
            paper_id: found.id,
            journal: found.journal || "",
            year: found.year || 0,
            ai_score: found.ai_score || 0,
          });
        }
      })
      .catch((reason) => {
        if (!cancelled) setError(errorMessage(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [id, reloadKey]);

  if (loading) return <div className="text-stone-500 text-sm py-20 text-center">加载中…</div>;
  if (error) return <DataLoadError detail={error} onRetry={() => setReloadKey((key) => key + 1)} />;
  if (!p) return (
    <div className="text-center py-20">
      <p className="text-stone-500">未找到。</p>
      <button onClick={goBack} className="text-accent hover:underline mt-4 inline-block">← 返回</button>
    </div>
  );

  return (
    <article className="max-w-3xl mx-auto">
      <button onClick={goBack} className="text-sm text-accent hover:underline">← 返回列表</button>

      <div className="mt-4 flex flex-wrap gap-x-3 gap-y-1 text-xs text-stone-500">
        <span className="font-mono">{p.journal}</span>
        {p.date && <span>· {p.date}</span>}
        {p.volume && <span>· Vol.{p.volume}{p.issue ? `(${p.issue})` : ""}</span>}
        <span>· 引用 {p.cited_by}</span>
        <span className="text-stone-400">· AI {p.ai_score?.toFixed(0)} · 领域 {p.domain_score?.toFixed(0)}</span>
      </div>

      <h1 className="text-2xl font-semibold leading-tight mt-2">{p.title}</h1>
      {p.title_zh && (
        <h2 className="text-base text-stone-600 mt-1 leading-snug">{p.title_zh}</h2>
      )}

      <div className="text-sm text-stone-700 mt-3">
        {p.authors_full?.map((a, i) => (
          <span key={i}>
            {a.name}
            {a.affiliation?.length ? <span className="text-stone-400 text-xs"> ({a.affiliation[0]})</span> : null}
            {i < p.authors_full.length - 1 ? "; " : ""}
          </span>
        ))}
      </div>

      <div className="flex gap-4 mt-3 text-sm">
        {p.url && <a href={p.url} target="_blank" rel="noopener" className="text-accent hover:underline">原文 ↗</a>}
        {p.pdf_url && <a href={p.pdf_url} target="_blank" rel="noopener" className="text-accent hover:underline">PDF ↗</a>}
        {p.doi && <span className="text-stone-400 font-mono text-xs">DOI: {p.doi}</span>}
      </div>

      {p.tldr && (
        <section className="mt-6 p-4 bg-accent/5 border-l-4 border-accent rounded-r">
          <div className="text-xs text-accent font-semibold mb-1">中文 TL;DR</div>
          <p className="text-stone-800 leading-relaxed">{p.tldr}</p>
        </section>
      )}

      {(p.topic_tags?.length || p.ai_type_tags?.length) ? (
        <section className="mt-5">
          <div className="text-xs text-stone-400 mb-1.5">主题</div>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-stone-500">
            {p.topic_tags?.map((t) => <span key={t}>#{t}</span>)}
            {p.ai_type_tags?.map((t) => <span key={"ai-" + t} className="text-accent/80">#{t}</span>)}
          </div>
        </section>
      ) : null}

      {p.ai_reason && (
        <section className="mt-5 text-xs text-stone-500">
          <span className="text-stone-400">LLM 评分理由：</span>{p.ai_reason}
        </section>
      )}

      {p.abstract && (
        <section className="mt-6">
          <div className="text-xs text-stone-500 font-semibold mb-2">原始摘要 (英文)</div>
          <p className="text-stone-700 leading-relaxed text-sm whitespace-pre-wrap">{p.abstract}</p>
        </section>
      )}
    </article>
  );
}
