import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";
import HeaderNav from "@/components/HeaderNav";
import { Analytics } from "@vercel/analytics/next";

export const metadata: Metadata = {
  title: "SR × AI Papers — AI 社交关系 / 人机关系论文索引",
  description: "聚合 2023 至今传播 / 社会心理 / 人际关系 / HCI 顶刊顶会中与 AI 社交关系相关的论文，含中文 TL;DR、主题标签与覆盖率审计。",
  alternates: {
    types: {
      "application/rss+xml": "/rss.xml",
    },
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <header className="border-b border-stone-200 bg-white sticky top-0 z-10">
          <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between gap-4">
            <Link href="/" className="flex items-baseline gap-2 min-w-0">
              <span className="font-mono font-semibold tracking-tight">
                SR × AI <span className="text-accent">Papers</span>
              </span>
              <span className="hidden sm:inline text-xs text-stone-500 truncate">
                AI 社交关系 / 人机关系相关研究索引
              </span>
            </Link>
            <HeaderNav />
          </div>
        </header>
        <main className="max-w-6xl mx-auto px-4 py-6">{children}</main>
        <footer className="max-w-6xl mx-auto px-4 py-8 text-xs text-stone-500 border-t border-stone-200 mt-10">
          数据来源：OpenAlex + Crossref + Semantic Scholar · LLM：DeepSeek-V3.2 (SiliconFlow) ·
          构建：<span className="font-mono">sr-ai-papers</span>
        </footer>
        <Analytics />
      </body>
    </html>
  );
}
