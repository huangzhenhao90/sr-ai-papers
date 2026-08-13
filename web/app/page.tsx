"use client";

import { useEffect, useState } from "react";
import PaperList, { type Paper } from "@/components/PaperList";
import DataLoadError from "@/components/DataLoadError";
import { errorMessage, fetchJson } from "@/lib/fetchJson";

export default function HomePage() {
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
    ]).then(([p, m]) => {
      if (cancelled) return;
      setPapers(p);
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

  return <PaperList papers={papers} meta={meta} />;
}
