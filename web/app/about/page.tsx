"use client";

import { useEffect, useState } from "react";
import DataLoadError from "@/components/DataLoadError";
import { errorMessage, fetchJson } from "@/lib/fetchJson";

type Meta = {
  totals: { papers_indexed: number; papers_scored: number; papers_ai_relevant: number };
  generated_at: string;
  journals: { abbr: string; name_en: string; name_zh: string; publisher: string; tier: string; lang: string; papers_indexed: number; papers_ai_relevant: number; publisher_toc: string }[];
};

export default function AboutPage() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchJson<Meta>("/data/meta.json")
      .then((value) => {
        if (!cancelled) setMeta(value);
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
  }, [reloadKey]);

  if (loading) return <div className="text-stone-500 text-sm py-20 text-center">加载中…</div>;
  if (error) return <DataLoadError detail={error} onRetry={() => setReloadKey((key) => key + 1)} />;
  if (!meta) return null;

  return (
    <div className="max-w-4xl mx-auto space-y-10">
      {/* 项目说明 */}
      <section>
        <h1 className="text-2xl font-semibold mb-4">关于这个项目</h1>
        <p className="text-sm leading-relaxed text-stone-700">
          聚合 2023-01 至今传播学 / CMC、社会心理与人际关系、HCI 顶刊与顶会
          及 arXiv 5 个分类中
          <strong className="text-accent"> 与 AI 社交关系（陪伴、亲密、拟社会关系、人机信任、情感连接）相关 </strong>
          的论文，用 LLM 自动生成中文 TL;DR、主题标签和 AI 类型标签。
        </p>
        <div className="mt-4 grid grid-cols-3 gap-3 text-sm">
          <Stat n={meta.totals.papers_indexed} label="数据库总论文" />
          <Stat n={meta.totals.papers_ai_relevant} label="AI 相关（双≥3）" />
          <Stat n={meta.journals.length} label="覆盖刊物 / 来源" />
        </div>
        <p className="text-xs text-stone-400 mt-2">
          数据更新时间：{new Date(meta.generated_at).toLocaleString("zh-CN")} · 每天 UTC 02:00 自动增量
        </p>
      </section>

      {/* 数据流水线 */}
      <section>
        <h2 className="text-base font-semibold mb-3">数据流水线</h2>
        <ol className="text-sm space-y-1 list-decimal pl-5 text-stone-700">
          <li><strong>抓取</strong>：每本期刊/会议从 OpenAlex 和 Crossref 双源全量抓取（不用关键词作召回闸门）；arXiv 用社交关系强信号词预过滤</li>
          <li><strong>规范化 + 去重</strong>：DOI 优先；无 DOI 用 标题+作者+年份+卷期 指纹</li>
          <li><strong>覆盖率审计</strong>：每期对账双源数字（见下方表）</li>
          <li><strong>LLM 双打分</strong>：DeepSeek-V3.2 (SiliconFlow)，对每篇论文给 AI 相关性 (0-5) 和社交关系相关性 (0-5)</li>
          <li><strong>TL;DR + 标签</strong>：仅对双 ≥ 3 的论文生成 200 字中文摘要 + 标签</li>
          <li><strong>标题翻译</strong>：双≥3 的论文标题全部预翻译为中文</li>
        </ol>
      </section>

      <section>
        <h2 className="text-base font-semibold mb-2">为什么不用关键词作召回闸门</h2>
        <p className="text-sm leading-relaxed text-stone-700">
          AI 议题的措辞每年都在变（2023「ChatGPT」→ 2024「GenAI」→ 2025「Agent」→ 2026「AI 伴侣」）。
          如果用关键词作召回闸门，会漏掉那些措辞不直白但实质相关的论文。
          所以本项目对期刊采用「<strong>全量入库 → LLM 后置判定</strong>」策略。
          arXiv 是例外——总量太大，只能用社交关系强信号词预过滤后再 LLM 复判。
        </p>
      </section>

      {/* 期刊清单 */}
      <section>
        <h2 className="text-base font-semibold mb-2">收录期刊清单</h2>
        <p className="text-sm text-stone-600 mb-4">
          每本期刊点击可跳转到出版商目录页。「共 N」是 2023 至今数据库总论文，「AI N」是经 LLM 判定双≥3 的 AI 相关论文。
        </p>
        <div className="grid sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
          {meta.journals.map((j) => (
            <a
              key={j.abbr}
              href={j.publisher_toc || "#"}
              target={j.publisher_toc ? "_blank" : undefined}
              rel={j.publisher_toc ? "noopener" : undefined}
              className="text-left p-2 border border-stone-200 bg-white rounded text-xs hover:border-accent/60 transition-colors"
            >
              <div className="font-mono font-semibold">{j.abbr}</div>
              <div className="text-stone-500 truncate" title={j.name_en}>{j.name_en}</div>
              <div className="mt-1 flex justify-between">
                <span className="text-stone-500">共 {j.papers_indexed}</span>
                <span className="text-accent">AI {j.papers_ai_relevant}</span>
              </div>
            </a>
          ))}
        </div>
      </section>

      <section>
        <h2 className="text-base font-semibold mb-2">已知局限</h2>
        <ul className="text-sm space-y-1 list-disc pl-5 text-stone-700">
          <li>部分论文摘要缺失（出版商未向 OpenAlex/Crossref 开放 abstract feed），LLM 仅能根据标题打分</li>
          <li>顶会论文（CHI/CSCW 等）OpenAlex 召回率略低于期刊，必要时通过 DOI 反查补全</li>
          <li>中文期刊 v1 仅接入心理学报，新闻与传播研究等需 CNKI 手动导入</li>
          <li>每天 UTC 02:00 自动跑增量，但 LLM 安全阀（默认 500 篇）防止 cache miss 时烧钱</li>
        </ul>
      </section>
    </div>
  );
}

function Stat({ n, label }: { n: number; label: string }) {
  return (
    <div className="border border-stone-200 rounded p-3 bg-white">
      <div className="text-2xl font-mono font-semibold">{n.toLocaleString()}</div>
      <div className="text-xs text-stone-500 mt-1">{label}</div>
    </div>
  );
}
