# 数据说明：官方 ICML26 比赛数据与自建 ICLR26 验证数据

本文档修正一个容易混淆的点：**官方比赛数据是 Koala Science 平台上的 ICML 2026 submissions；`data/koala_iclr2026/` 是我们自己构造的 ICLR26 离线代理数据集。** 两者不能混为一谈。

我们的策略是：

```text
ICLR26 OpenReview labeled data
  -> 构造 Koala-shaped offline dataset
  -> 训练、校准、做 ablation、检查 leakage

Koala / ICML26 competition papers
  -> 真实比赛对象
  -> 没有可见 accept/reject ground truth
  -> 用离线模型和 review agent 在线审稿、讨论、提交 verdict
```

## 官方比赛数据：Koala / ICML26

官方 competition 页面写明，任务是设计 AI agent 在 Koala Science 平台上 peer-review **ICML 2026 submissions**，参与 threaded discussions，并提交 0 到 10 分的 verdict。比赛结束后，leaderboard 会根据 verdict 与真实 ICML 2026 accept/reject decisions 的相关性排名。[1][2]

官方关键规则：

- 平台：`https://koala.science`
- MCP endpoint：`https://koala.science/mcp`
- Agent skill guide：`https://koala.science/skill.md`
- 比赛时间：2026-04-24 12pm ET 到 2026-04-30 AoE。
- 每篇论文 72 小时生命周期：
  - `in_review`：前 48 小时，可以评论。
  - `deliberating`：后 24 小时，可以提交 verdict。
  - `reviewed`：结束后 verdict 公开，paper 关闭。
- verdict 是 0 到 10 分的 float。
- verdict 必须引用至少 3 个不同其他 agent 的 comments。
- 不能引用自己或同 owner/sibling agent。
- 每个 agent 初始 100 karma。
- 首次评论一篇论文花 1 karma，同一篇后续评论花 0.1 karma，verdict 免费。
- 平台论文 release 前会匿名化，作者姓名、affiliation 和明显身份信息会被移除；论文中的 GitHub URLs 会保留。[1]
- 初始释放 300 篇论文，之后每 2 小时根据 under-reviewed paper 数量动态补充。[1]

官方 starter repo 是 `koala-science/peer-review-agents`，它提供 `reva` CLI、agent prompt assembly、API key 管理、tmux/Slurm launch 等基础设施。[3]

### 官方 ICML26 paper record 格式

官方 platform skill guide 中，`get_paper` / paper API 会返回论文详情和资源 URL。[4] 对我们最重要的字段是：

| 字段 | 类型 | 说明 |
|---|---|---|
| `paper_id` | string | Koala 平台 paper id，通常是 UUID |
| `title` | string | 匿名化后的论文标题 |
| `abstract` | string | 匿名化后的论文摘要 |
| `domains` | list[string] | Koala domain 标签 |
| `status` | string | `in_review` / `deliberating` / `reviewed` |
| `arxiv_id` | string \| null | arXiv id，若平台提供 |
| `authors` | list/object \| null | API 可能返回的作者字段；比赛规则要求平台论文匿名化，agent 不应依赖身份信息 |
| `pdf_url` | string \| null | 论文 PDF URL |
| `tarball_url` | string \| null | source archive，可能包含 LaTeX、figures、bib files |
| `github_repo_url` | string \| null | legacy 单 repo 字段 |
| `github_urls` | list[string] | 论文中保留的 GitHub/code/artifact links |
| `preview_image_url` | string \| null | first-page PNG preview |
| `comment_count` | int | 当前评论数，平台 feed 中常见 |
| `created_at` / `updated_at` | string | 平台时间戳，平台 feed 中常见 |

资源 URL 可能是相对路径，例如：

```text
/storage/pdfs/<paper_uuid>.pdf
/storage/tarballs/<paper_uuid>.tar.gz
```

官方 skill guide 说明，相对路径需要用 platform storage base 补全；也就是从 API base `https://koala.science/api/v1` 去掉 `/api/v1`，得到 `https://koala.science`，再拼接相对路径。[4]

```python
storage_base = API_BASE_URL.replace("/api/v1", "")
full_url = url if url.startswith("http") else storage_base + url
```

### 官方 ICML26 数据的限制

官方 ICML26 competition papers 在比赛期间没有可见 label：

- 没有真实 accept/reject。
- 没有 official reviews。
- 没有 meta reviews。
- 不能使用 OpenReview reviews、scores、decisions、accept/reject status、post-publication signals、citation trajectory 等未来或泄漏信息。
- 最终 performance 只有比赛结束且 ICML decisions 公开后才能知道。

因此，ICML26 数据适合线上 inference、审稿、讨论和 verdict，不适合离线 supervised model selection。

## 自建验证数据：ICLR26 offline surrogate

`data/koala_iclr2026/` 是我们用 ICLR 2026 OpenReview 数据构造的离线代理数据集。它的目的不是代表官方 ICML26 数据本身，而是提供一个**有 ground truth 的近似任务**，用于模型开发和验证。

当前 Mila checkout：

```text
~/scratch/ReviewAgent
```

主要文件：

```text
data/koala_iclr2026/global_train.jsonl
data/koala_iclr2026/global_test_public.jsonl
data/koala_iclr2026/global_test_labels.jsonl
data/koala_iclr2026/koala_reference_current.jsonl
data/koala_iclr2026/manifest.json
```

`manifest.json` 记录的 ICLR 源数据为：

```text
/network/scratch/z/zhangya/shared_resource_perturbflow/iclr2026_openreview/papers.jsonl
```

构造策略：

- 只保留至少有一个当前 Koala domain 的论文。
- 去掉 ICLR-only fallback categories。
- 只使用 accepted/rejected ICLR papers。
- 要求至少 3 个 official reviews。
- 排除 withdrawn 和 desk-rejected papers。
- 删除 author names/ids，以模拟 Koala 匿名 paper release。
- 根据当前 Koala paper feed 的 title/abstract TF-IDF 相似度和 domain counts 选择 held-out test。
- 防止 global paper-id leakage：任一 domain 进 test 的 paper 都从所有 train split 中排除。

当前规模：

| 文件 | 行数 | 含义 |
|---|---:|---|
| `global_train.jsonl` | 13,378 | ICLR26 labeled train，含 reviews/meta-reviews/score summaries |
| `global_test_public.jsonl` | 349 | ICLR26 held-out public fields，模拟线上可见论文 |
| `global_test_labels.jsonl` | 349 | ICLR26 held-out labels，只用于 offline eval |
| `koala_reference_current.jsonl` | 349 | Koala/ICML26 当前平台论文快照，用于构造 ICLR surrogate 的 target distribution |

注意：`koala_reference_current.jsonl` 是 Koala/ICML26 平台快照，不是 labeled ICLR train/test。它在 `data/koala_iclr2026/` 目录下只是因为当时用它来 shape ICLR26 split。

## ICLR26 JSONL 字段

### `global_train.jsonl`

训练集包含 public paper metadata、ICLR labels、official reviews、meta reviews 和 score summaries。

| 字段 | 类型 | 说明 |
|---|---|---|
| `paper_id` | string | ICLR/OpenReview forum id |
| `title` | string | 论文标题 |
| `abstract` | string | 摘要 |
| `tldr` | string | OpenReview TL;DR |
| `domains` | list[string] | Koala domain 标签，如 `d/NLP` |
| `keywords` | list[string] | 原始关键词 |
| `primary_area` | string | OpenReview primary area |
| `domain_assignment_sources` | object | domain 映射来源 |
| `similarity_to_koala_by_domain` | object | 与 Koala/ICML26 平台论文分布的相似度 |
| `openreview_forum_url` | string | OpenReview forum URL |
| `openreview_pdf_url` | string | `https://openreview.net/pdf?id=<paper_id>` |
| `pdf_url_from_note` | string | OpenReview note 中的 hash PDF URL |
| `source_conference` | string | 当前为 `ICLR 2026` |
| `source_status` | string | `accepted` / `rejected` |
| `decision` | list[string] | 原始 decision 文本 |
| `decision_label` | string | `accept` / `reject` |
| `accept_label` | int | 1 表示 accept，0 表示 reject |
| `decision_tier` | string | `poster` / `reject` 等 |
| `suggested_verdict_score` | float | 映射到 0 到 10 verdict 的参考分 |
| `official_review_count` | int | official review 数量 |
| `official_reviews` | list[object] | ICLR official reviews |
| `meta_reviews` | list[object] | ICLR meta-review / decision note |
| `review_scores` | object | rating、confidence、soundness 等统计 |

### `global_test_public.jsonl`

模拟线上 public paper，只保留 prediction time 可见字段；没有 label、reviews、meta-review 或 source status。

| 字段 | 类型 | 说明 |
|---|---|---|
| `paper_id` | string | ICLR/OpenReview forum id |
| `title` | string | 标题 |
| `abstract` | string | 摘要 |
| `tldr` | string | TL;DR |
| `domains` | list[string] | Koala domain 标签 |
| `keywords` | list[string] | 关键词 |
| `primary_area` | string | OpenReview primary area |
| `domain_assignment_sources` | object | domain 映射来源 |
| `similarity_to_koala_by_domain` | object | 与 Koala/ICML26 平台论文分布的相似度 |
| `openreview_pdf_url` | string | OpenReview PDF URL |
| `pdf_url_from_note` | string | OpenReview hash PDF URL |
| `source_conference` | string | `ICLR 2026` |
| `simulated_status` | string | 如 `in_review` |

### `global_test_labels.jsonl`

offline evaluation label file，按 `paper_id` 与 `global_test_public.jsonl` join。正常 agent prompt、线上 selection、线上 verdict 不得读取这份文件。

| 字段 | 类型 | 说明 |
|---|---|---|
| `paper_id` | string | 与 public test 对齐的主键 |
| `title` | string | 标题 |
| `domains` | list[string] | Koala domain 标签 |
| `decision` | list[string] | 原始 decision |
| `decision_label` | string | `accept` / `reject` |
| `accept_label` | int | 二分类标签 |
| `decision_tier` | string | 更细粒度 tier |
| `source_status` | string | `accepted` / `rejected` |
| `suggested_verdict_score` | float | 参考 verdict score |
| `official_review_count` | int | official review 数量 |
| `review_scores` | object | review score summary |
| `similarity_to_koala_by_domain` | object | 与 Koala/ICML26 平台论文分布的相似度 |

### `koala_reference_current.jsonl`

这是 Koala/ICML26 平台当前论文 feed 快照，用来 shape ICLR26 split。它的 `paper_id` 是 Koala UUID，不是 OpenReview id。

| 字段 | 类型 | 说明 |
|---|---|---|
| `paper_id` | string | Koala paper UUID |
| `title` | string | 平台论文标题 |
| `abstract` | string | 平台论文摘要 |
| `domains` | list[string] | Koala domain 标签 |
| `status` | string | `in_review` / `deliberating` 等 |
| `comment_count` | int | 当前评论数 |
| `created_at` / `updated_at` | string | Koala 时间戳 |
| `arxiv_id` | string | arXiv id，若有 |
| `github_urls` | list[string] | 平台保留的 GitHub URLs |
| `pdf_url` | string | Koala 相对 PDF 路径 |
| `tarball_url` | string | Koala 相对 source tarball 路径 |

## PDF 与缓存

### 原始 JSONL 是否包含 PDF？

不包含 PDF 二进制，只包含 PDF URL 或 source tarball URL。

ICLR26 offline 数据：

```text
openreview_pdf_url = https://openreview.net/pdf?id=<paper_id>
pdf_url_from_note  = https://openreview.net/pdf/<hash>.pdf
```

Koala/ICML26 平台数据：

```text
pdf_url     = /storage/pdfs/<koala_uuid>.pdf
tarball_url = /storage/tarballs/<koala_uuid>.tar.gz
```

Koala 相对 URL 必须拼接 `https://koala.science` 才能下载。

### 现有 `data/pdf_cache`

位置：

```text
data/pdf_cache/raw/
data/pdf_cache/parsed/
data/pdf_cache/parse_summary.json
```

这个目录是旧版 ICLR26 PDF parser 的派生缓存：

```text
ICLR26 JSONL record
  -> OpenReview PDF URL
  -> data/pdf_cache/raw/{paper_id}.pdf
  -> PyMuPDF text extraction
  -> sanitizer
  -> data/pdf_cache/parsed/{paper_id}.json
```

当前 Mila cache 覆盖：

| 数据源 | 行数 | 已有 parsed JSON |
|---|---:|---:|
| `global_train.jsonl` | 13,378 | 2,000 |
| `global_test_public.jsonl` | 349 | 349 |
| `koala_reference_current.jsonl` | 349 | 0 |

当前 cache 统计：

| 指标 | 数量 |
|---|---:|
| `data/pdf_cache/parsed/*.json` | 2,349 |
| `data/pdf_cache/raw/*.pdf` | 1,138 |
| `ok=true` | 1,138 |
| 有 `full_text` | 1,138 |
| `download_failed` | 1,211 |

成功的 parsed JSON 字段：

| 字段 | 说明 |
|---|---|
| `paper_id` | ICLR/OpenReview id |
| `title` | 标题 |
| `abstract` | 摘要 |
| `ok` | 是否成功 |
| `pdf_path` | 下载后的 PDF 路径 |
| `parsed_path` | parsed JSON 路径 |
| `source_pdf_path` | parser 读取的 PDF |
| `full_text` | PyMuPDF 抽取并 sanitizer 后的全文 |
| `page_texts` | page index 到页面文本 |
| `sections` | regex section split |
| `references` | references section 抽取结果 |
| `figure_captions` | regex figure captions |
| `table_captions` | regex table captions |
| `table_evidence` | 文本表格证据片段 |
| `parser_warnings` | sanitizer/parser warnings |

失败的 parsed JSON 通常只有 metadata 和 error：

```json
{
  "ok": false,
  "error": "download_failed",
  "paper_id": "...",
  "title": "...",
  "abstract": "...",
  "pdf_path": "...",
  "parsed_path": "..."
}
```

## 两个数据集的核心差异

| 维度 | 官方 Koala/ICML26 比赛数据 | 自建 ICLR26 offline 数据 |
|---|---|---|
| 会议 | ICML 2026 | ICLR 2026 |
| 用途 | 实际参赛、线上审稿、讨论、verdict | 训练、离线验证、模型选择、leakage 检查 |
| 是否有 ground truth | 比赛期间不可见 | 可见，来自 ICLR decisions |
| 主键 | Koala UUID | OpenReview forum id |
| 数据入口 | Koala API / MCP / platform feed | `data/koala_iclr2026/*.jsonl` |
| PDF URL | Koala storage relative/absolute URL | OpenReview absolute URL |
| Source tarball | 可能有 `tarball_url` | 通常没有，主要是 OpenReview PDF |
| Reviews/meta-reviews | 不可见 | train 中可见，用于 proxy/offline training |
| Labels | 不可见 | train/test_labels 中可见 |
| 当前旧 `pdf_cache` 覆盖 | 0 | train 子集 + test public |
| Performance 可验证性 | 赛后才知道 | 现在可 offline eval |

## 对模型开发的含义

正确的数据边界是：

- 用 ICLR26 train 训练模型。
- 用 ICLR26 public test + labels 做 offline validation。
- 用 Koala/ICML26 platform papers 做线上 inference 和 comments/verdicts。
- 不把 ICLR offline metric 直接宣称为 ICML competition performance。
- 不在 ICML agent prompt 中使用任何 ICML future/leaked signal。
- ICLR train 中的 official reviews/meta-reviews 只能作为 offline proxy training signal；不能把同一篇论文的真实 review/decision 作为线上预测输入。

## Parser 改进方向

现有 `data/pdf_cache` 只是 PyMuPDF 文本 cache，足够做早期 full-text features，但不够做 review-ready corpus。

### Paper2Markdown 版本记录

`Apr29-10:02` 当前命名：

| 版本 | 定位 | 主要输出 | 当前状态 |
|---|---|---|---|
| `Paper2Markdown-V0` | PyMuPDF legacy cache | `data/pdf_cache/parsed/{paper_id}.json`，含 `full_text` / `page_texts` / regex sections | 已用于早期 full-text evidence；速度快但结构弱、section 不稳、匿名清洗不足 |
| `Paper2Markdown-V1` | Marker non-LLM raw parse baseline | Markdown、Marker JSON、Marker chunks、meta TOC、figure images、`parse_report.json` | 已在 Mila 对 RaftPPI / `Dp1RM3gPg8` smoke 成功 |
| `Paper2Markdown-V2` | V1 raw parse + deterministic anonymization | review-agent-facing anonymous text/chunks；raw parse 仍保留用于审计 | 本地 sanitizer 已加测试，先覆盖作者/单位 header 与 acknowledgement/funding block |

V1 相比 V0 的实质改进：

- 从 flat `full_text` 升级到 Markdown + parser-native JSON/chunks。
- 有 page/block/polygon/meta TOC provenance，能更可靠地构造 section-aware chunks。
- 能保留 figure image refs 和 table Markdown 结构。
- 不启用 LLM rewrite，不做 poster-style summarization、citation deletion 或 random section sampling。

V2 的边界：

- V2 不是删除 raw cache，而是在 raw cache 后生成给模型/agent 使用的 anonymous payload。
- V2 默认删除 title page 中 title 到 abstract 之间的 author/affiliation block。
- V2 删除 acknowledgement、author contribution、funding、camera-ready/rebuttal/checklist 等 block，直到下一个安全 section，如 references 或 appendix。
- V2 仍需要后续接入 final `chunks.jsonl` 生成器，当前只先锁定 sanitizer 行为。

建议新增：

```text
data/pdf_parse_cache/v1/{paper_id}/
  paper.md
  paper.blocks.json
  assets.json
  chunks.jsonl
  parse_report.json
  legacy_payload.json
```

处理原则：

- ICLR26 offline 和 Koala/ICML26 online paper 都应该能进同一套 parser cache schema。
- ICLR26 的 legacy payload 可以继续兼容现有 `ParsedPaperText`。
- Koala/ICML26 的 parser 要支持 relative `pdf_url` 和 `tarball_url`。
- parser 默认不做 LLM summarization、semantic compression、citation deletion 或随机 section sampling。
- Review agent 应主要读取 section-aware `chunks.jsonl`，而不是一次性读一个大 `full_text`。
- `parse_report.json` 要记录 parser、fallback、page count、section count、caption count、text length、leakage hit 等质量指标。

## 注意事项

- `ICLR26` 和 `ICML26` 是不同会议；前者是我们自建验证集，后者是官方比赛对象。
- `submission number`、OpenReview forum id、Koala UUID 不是同一种 id。例如用户说的论文编号 `11072`，在当前 ICLR26 JSONL 中对应 OpenReview id `Dp1RM3gPg8`。
- `koala_reference_current.jsonl` 是 Koala/ICML26 平台快照，只用于 shape ICLR26 surrogate distribution；它本身没有 label。
- `global_test_labels.jsonl` 只用于离线评估，不进入线上 agent。
- `data/pdf_cache`、raw PDFs、parse outputs 和 model artifacts 不应提交到 Git。

## Next steps

- 修改 parser pipeline，统一支持 ICLR OpenReview URL 和 Koala relative storage URL。
- 对 ICLR paper `Dp1RM3gPg8` 做 parser smoke，验证 legacy payload 与新 cache schema。
- 对一篇 Koala/ICML26 current paper 做只读 parser smoke，验证 relative `pdf_url` / `tarball_url` 解析。
- 在 Linear issue 中区分 ICLR offline parser work 与 ICML online parser work。

## References

[1] Koala competition page: https://koala.science/competition

[2] Raw competition rules: https://koala.science/COMPETITION.md

[3] Starter repo: https://github.com/koala-science/peer-review-agents

[4] Agent skill guide: https://koala.science/skill.md

## 数据总结

对，准确说是这样：

1. **官方 Koala/ICML26 比赛数据**
   - 官方 paper record 里提供 `pdf_url`，可能还提供 `tarball_url`、`github_urls`、`preview_image_url`。
   - 官方没有提供已经处理好的全文 Markdown / blocks / chunks。
   - 所以 ICML26 比赛论文需要我们自己下载 PDF 或 source tarball，再做 parsing。

2. **我们自建 ICLR26 offline 数据**
   - 原始 `data/koala_iclr2026/*.jsonl` 也不是 Markdown，而是 metadata + labels/reviews/PDF URL。
   - 但我们已经对其中一部分 ICLR26 PDF 跑过旧版 parser，生成了：
     ```text
     data/pdf_cache/raw/*.pdf
     data/pdf_cache/parsed/*.json
     ```
   - 当前 Mila 上有 `2349` 个 parsed JSON，其中 `1138` 个成功并有 `full_text`，`1211` 个是 `download_failed`。
   - 这个旧 parser 是 PyMuPDF text extraction，不是 Markdown，也没有 blocks/assets/chunks。

所以一句话：

**官方 ICML26 现在基本只有 PDF/source URL，没有处理好的 Markdown；我们 ICLR26 surrogate 已经有一批自己处理出的旧 JSON full_text cache，但它还不是理想的 review-ready Markdown/chunk cache。**

下一步就是统一做新版 parser cache，让 ICLR26 offline 和 ICML26 online 都输出同一种结构：

```text
paper.md
paper.blocks.json
assets.json
chunks.jsonl
parse_report.json
legacy_payload.json
```
