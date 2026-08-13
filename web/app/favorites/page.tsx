"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import PaperList, { type Paper } from "@/components/PaperList";
import DataLoadError from "@/components/DataLoadError";
import { errorMessage, fetchJson } from "@/lib/fetchJson";
import { readFavorites } from "@/lib/favorites";
import { toCsv, toMarkdown, toBibtex, downloadFile } from "@/lib/export";
import { track } from "@/lib/analytics";

export default function FavoritesPage() {
  const [allPapers, setAllPapers] = useState<Paper[]>([]);
  const [meta, setMeta] = useState<any>(null);
  const [favIds, setFavIds] = useState<Set<number>>(new Set());
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
      setAllPapers(p);
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

  // 监听 favorites 变化（卡片上 ★ 切换时实时同步）
  useEffect(() => {
    const update = () => setFavIds(new Set(readFavorites()));
    update();
    window.addEventListener("favorites-changed", update);
    window.addEventListener("storage", update);
    return () => {
      window.removeEventListener("favorites-changed", update);
      window.removeEventListener("storage", update);
    };
  }, []);

  if (loading) return <div className="text-stone-500 text-sm py-20 text-center">加载中…</div>;
  if (error) return <DataLoadError detail={error} onRetry={() => setReloadKey((key) => key + 1)} />;

  const favPapers = allPapers.filter((p) => favIds.has(p.id));

  if (favPapers.length === 0) {
    return (
      <div className="text-center py-20 text-stone-500">
        <div className="text-5xl mb-4">☆</div>
        <p>还没有收藏论文。</p>
        <p className="text-sm mt-2">在 <Link href="/" className="text-accent hover:underline">论文列表</Link> 里点击 ☆ 加入收藏。</p>
        <p className="text-xs mt-4 text-stone-400">收藏只存在本浏览器（localStorage），不会上传服务器。</p>
      </div>
    );
  }

  const today = new Date().toISOString().slice(0, 10);
  const exports = (
    <div className="flex items-center gap-1.5 text-sm">
      <span className="text-stone-400 text-xs mr-1">导出</span>
      <button
        onClick={() => {
          downloadFile(`favorites-${today}.csv`, toCsv(favPapers), "text/csv;charset=utf-8");
          track("export", { format: "csv", count: favPapers.length });
        }}
        className="chip"
        title="导出为 CSV（Excel/Numbers 可打开）"
      >
        CSV
      </button>
      <button
        onClick={() => {
          downloadFile(`favorites-${today}.md`, toMarkdown(favPapers), "text/markdown;charset=utf-8");
          track("export", { format: "markdown", count: favPapers.length });
        }}
        className="chip"
        title="导出为 Markdown（Obsidian/Notion 友好）"
      >
        Markdown
      </button>
      <button
        onClick={() => {
          downloadFile(`favorites-${today}.bib`, toBibtex(favPapers), "application/x-bibtex;charset=utf-8");
          track("export", { format: "bibtex", count: favPapers.length });
        }}
        className="chip"
        title="导出为 BibTeX（Zotero/EndNote 可导入）"
      >
        BibTeX
      </button>
    </div>
  );

  const banner = (
    <div className="mb-3 p-3 bg-amber-50 border-l-4 border-amber-500 rounded-r text-sm text-stone-700">
      <strong className="text-amber-700">★ 我的收藏</strong>
      <span className="ml-2 text-stone-500">{favPapers.length} 篇 · 仅存在本浏览器</span>
    </div>
  );

  return (
    <PaperList
      papers={favPapers}
      meta={meta}
      banner={banner}
      rightHeader={exports}
    />
  );
}
