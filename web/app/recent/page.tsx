"use client";

import { useEffect, useState } from "react";
import PaperList, { type Paper } from "@/components/PaperList";
import DataLoadError from "@/components/DataLoadError";
import { errorMessage, fetchJson } from "@/lib/fetchJson";
import { comparePapersByRecent } from "@/lib/paperSort";

const DAYS = 7;

export default function RecentPage() {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [meta, setMeta] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([
      fetchJson<Paper[]>("/data/papers.json"),
      fetchJson<any>("/data/meta.json"),
    ]).then(([all, m]: [Paper[], any]) => {
      if (cancelled) return;
      // 取近 N 天实际发表的论文。
      const since = new Date(Date.now() - DAYS * 24 * 3600 * 1000);
      const sinceStr = since.toISOString().slice(0, 10);
      const untilStr = new Date().toISOString().slice(0, 10);
      const recent = all
        .filter((p) => p.date && p.date >= sinceStr && p.date <= untilStr)
        .sort(comparePapersByRecent);
      setPapers(recent);
      setMeta(m);
    }).catch((reason) => {
      if (!cancelled) setError(errorMessage(reason));
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  if (loading) return <div className="text-stone-500 text-sm py-20 text-center">加载中…</div>;
  if (error) return <DataLoadError detail={error} onRetry={() => setReloadKey((key) => key + 1)} />;

  const since = new Date(Date.now() - DAYS * 24 * 3600 * 1000).toISOString().slice(0, 10);
  const until = new Date().toISOString().slice(0, 10);

  const banner = (
    <div className="mb-3 p-3 bg-accent/5 border-l-4 border-accent rounded-r">
      <div className="text-accent font-semibold text-sm">📅 最近发表</div>
      <div className="text-xs text-stone-600 mt-0.5">
        过去 {DAYS} 天内发表的论文 · {since} 至 {until} · 共 {papers.length} 篇
      </div>
    </div>
  );

  return (
    <PaperList
      papers={papers}
      meta={meta}
      banner={banner}
    />
  );
}
