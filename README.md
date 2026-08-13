# SR-AI-Papers

聚合 2023-01 至今传播学 / CMC、社会心理与人际关系、HCI 顶刊顶会中**与 AI 社交关系相关**（AI 陪伴、拟社会关系、人机亲密、信任、依恋、情感连接、聊天机器人关系）的论文。

## 范围

- **25 个白名单来源**（传播/CMC 期刊 12 + 社会心理/关系期刊 7 + HCI 顶会 5 + 中文 1）
- **arXiv** 5 个分类（cs.HC, cs.CL, cs.CY, cs.SI, cs.AI）
- 时间窗：2023-01-01 至今
- 召回原则：**期刊全量入库 → 后置 LLM 判定**（关键词不作召回闸门）；arXiv 用社交关系强信号词预过滤

## 架构

```
GitHub Actions cron（每天 10:00 北京时间）
  ↓ Source Registry (config/journals.yaml)
  ↓ Connectors (crossref / openalex / arxiv / semantic_scholar / unpaywall)
  ↓ raw_records (永不删)
  ↓ normalizer → deduper → coverage_auditor
  ↓ enrichment_queue (摘要/引用/OA/PDF 补全)
  ↓ llm_queue (MiniMax-M3: 双打分 + TL;DR + 中文标题)
  ↓ publish_index → Next.js 前端
```

## 目录结构

```
sr-ai-papers/
├── config/              # journals.yaml, keywords.yaml
├── src/
│   ├── connectors/      # crossref, openalex, arxiv, semantic_scholar, unpaywall
│   ├── pipeline/        # normalize, dedupe, coverage_audit, enrich, llm_score
│   ├── db/              # schema, migrations
│   ├── llm/             # MiniMax client, prompts
│   └── utils/           # 通用工具
├── data/
│   ├── raw/             # 原始 API 响应（JSON）
│   ├── cnki_imports/    # 用户手动导出的 RIS/Endnote 文件
│   └── exports/         # 数据库导出（不入 git）
├── scripts/             # 一次性脚本
├── web/                 # Next.js 前端
├── docs/
└── logs/
```

## 与 ur-ai-papers 的差异

- **主题**：用户研究/HCI/CX → AI 社交关系（传播学 / 社会心理 / 人际关系）
- **期刊清单**：36 个用研/营销源 → 25 个传播/社会心理/HCI 源
- **arXiv 分类**：10 个 → 5 个（cs.HC, cs.CL, cs.CY, cs.SI, cs.AI）
- **arXiv 关键词**：用研/UX 词 → 社交关系强信号词（parasocial、companion、intimacy、attachment、human-AI relationship 等）
- **LLM 打分**：AI 相关性 + 领域相关性 → AI 相关性 + 社交关系相关性
- **代码骨架**：完全复用，仅替换 config、prompt 和文案
