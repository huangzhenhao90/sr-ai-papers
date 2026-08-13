"use client";

import type { Paper } from "@/components/PaperList";

function csvEscape(v: any): string {
  if (v == null) return "";
  const s = String(v).replace(/\r?\n/g, " ").trim();
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

export function toCsv(papers: Paper[]): string {
  const headers = [
    "id", "title_en", "title_zh", "journal", "year", "date",
    "authors", "doi", "url", "pdf_url",
    "ai_score", "domain_score", "topic_tags", "ai_type_tags",
    "tldr_zh",
  ];
  const rows = papers.map((p) => [
    p.id, p.title, p.title_zh || "", p.journal || "", p.year || "", p.date || "",
    (p.authors || []).join("; "), p.doi || "", p.url || "", p.pdf_url || "",
    p.ai_score, p.domain_score,
    (p.topic_tags || []).join("; "), (p.ai_type_tags || []).join("; "),
    p.tldr || "",
  ].map(csvEscape).join(","));
  return [headers.join(","), ...rows].join("\n");
}

export function toMarkdown(papers: Paper[]): string {
  const lines: string[] = [];
  lines.push(`# 收藏论文 (${papers.length} 篇)`);
  lines.push("");
  lines.push(`> 导出于 ${new Date().toLocaleString("zh-CN")} · 来自 [UR × AI Papers](https://ur-ai-papers.vercel.app)`);
  lines.push("");
  for (const p of papers) {
    lines.push(`## ${p.title}`);
    if (p.title_zh) lines.push(`**${p.title_zh}**`);
    lines.push("");
    const meta: string[] = [];
    if (p.journal) meta.push(`*${p.journal}*`);
    if (p.date) meta.push(p.date);
    else if (p.year) meta.push(String(p.year));
    if (p.authors?.length) meta.push(p.authors.slice(0, 5).join(", ") + (p.authors.length > 5 ? " 等" : ""));
    if (meta.length) lines.push(meta.join(" · "));
    lines.push("");
    if (p.tldr) {
      lines.push(`> ${p.tldr}`);
      lines.push("");
    }
    const tags: string[] = [];
    if (p.topic_tags?.length) tags.push(...p.topic_tags.map((t) => `#${t}`));
    if (p.ai_type_tags?.length) tags.push(...p.ai_type_tags.map((t) => `#${t}`));
    if (tags.length) lines.push(tags.join(" "));
    const links: string[] = [];
    if (p.url) links.push(`[原文](${p.url})`);
    if (p.pdf_url) links.push(`[PDF](${p.pdf_url})`);
    if (p.doi) links.push(`DOI: \`${p.doi}\``);
    if (links.length) lines.push(links.join(" · "));
    lines.push("");
    lines.push("---");
    lines.push("");
  }
  return lines.join("\n");
}

function bibKey(p: Paper, used: Set<string>): string {
  // 形如 author2024algorithmic
  const author = (p.authors?.[0] || "anon")
    .split(/[,\s]+/).pop()!
    .replace(/[^a-zA-Z]/g, "")
    .toLowerCase() || "anon";
  const year = p.year || "nd";
  const word = (p.title || "")
    .toLowerCase().replace(/[^a-z0-9 ]/g, " ")
    .split(/\s+/).filter((w) => w.length >= 4 && !["with", "from", "this", "that", "what", "when", "into", "their"].includes(w))[0] || "paper";
  let key = `${author}${year}${word}`;
  let n = 2;
  while (used.has(key)) {
    key = `${author}${year}${word}${n++}`;
  }
  used.add(key);
  return key;
}

function bibField(name: string, value: string | null | undefined): string {
  if (!value) return "";
  // 转义大括号、反斜杠
  const v = String(value).replace(/[\\{}]/g, "");
  return `  ${name} = {${v}},\n`;
}

export function toBibtex(papers: Paper[]): string {
  const used = new Set<string>();
  return papers.map((p) => {
    const key = bibKey(p, used);
    const isArxiv = p.journal?.startsWith("arXiv");
    const type = isArxiv ? "@misc" : "@article";
    const authors = (p.authors || []).join(" and ");
    let s = `${type}{${key},\n`;
    s += bibField("title", p.title);
    if (authors) s += bibField("author", authors);
    if (p.year) s += `  year = {${p.year}},\n`;
    if (!isArxiv && p.journal) s += bibField("journal", p.journal);
    if (isArxiv) s += bibField("howpublished", p.journal || "arXiv preprint");
    if (p.doi) s += bibField("doi", p.doi);
    if (p.url) s += bibField("url", p.url);
    s += "}\n";
    return s;
  }).join("\n");
}

export function downloadFile(filename: string, content: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
