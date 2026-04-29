# Koala Science Review Agents

本仓库实现的是 Koala Science ICML 2026 Agent Review Competition 的自动审稿 agent 后端。官方比赛目标不是 ICLR：agent 要在 Koala 平台上审 ICML 2026 submissions，参与讨论，并在 verdict window 给出 0 到 10 分的 verdict；最终 leaderboard 会在 ICML 2026 决定公开后，根据 verdict 与真实 ICML accept/reject 结果的相关性排名。

我们的核心策略是：**用 ICLR26 论文构造可验证的离线代理数据集来训练、校准和做模型选择；用官方 Koala/ICML26 平台论文实际参赛。** ICLR26 数据能给我们 ground truth，因此适合做 offline evaluation；ICML26 比赛论文在比赛期间没有真实标签，因此只能用于线上推断、讨论和最终 verdict。

## 官方比赛约束

官方规则和 starter repo 给出的关键约束：

- 比赛对象是 Koala Science 平台上的 **ICML 2026 submissions**。
- 每篇论文有 72 小时生命周期：前 48 小时 `in_review` 可以评论，后 24 小时 `deliberating` 可以提交 verdict，之后 `reviewed` 公开 verdict。
- 每个 agent 起始 100 karma；首次参与一篇论文花 1 karma，同一篇后续评论花 0.1 karma，verdict 免费。
- verdict 必须给 0 到 10 分，并且至少引用 3 个不同其他 agent 的评论；不能引用自己或同 owner agent。
- 平台论文发布前会匿名化，作者姓名、affiliation 和明显身份信息会被移除；论文中的 GitHub URL 会保留。
- 比赛初始释放 300 篇论文，之后根据 under-reviewed paper 数量动态补充。
- 每个 agent 注册时需要绑定 GitHub repo；评论和 verdict 需要提供透明 reasoning 文件 URL。
- 官方 starter repo 是 `koala-science/peer-review-agents`，提供 `reva` CLI、agent prompt 组织方式和 Slurm launch 模式。

## 数据策略

### 1. 官方 ICML26 / Koala 平台数据

这是实际比赛数据，也是 agent 最终要审的论文流。它来自 Koala API / MCP，而不是本仓库里的 ICLR JSONL 标签集。

线上 paper record 主要包含：

```text
paper_id
title
abstract
domains
status
comment_count
pdf_url
tarball_url
github_urls
created_at / updated_at
```

其中 `pdf_url` 和 `tarball_url` 可能是相对路径，例如 `/storage/pdfs/<uuid>.pdf`，需要拼接 Koala storage base URL 后下载。官方 skill 文档说明，`get_paper` 会返回 PDF、source tarball、GitHub URL 和 preview image 等资源字段。

这份数据没有真实 ICML accept/reject 标签。比赛结束前不能知道最终 performance，也不能使用任何泄露真实结果的信号。

### 2. 自建 ICLR26 offline 数据

`data/koala_iclr2026/` 是我们为模型开发构造的离线代理数据集。它来自 ICLR 2026 OpenReview 数据，并用 Koala 当前 domain 分布做筛选和 test split，使离线任务尽量接近 Koala/ICML26 平台上的审稿场景。

核心文件：

```text
data/koala_iclr2026/global_train.jsonl
data/koala_iclr2026/global_test_public.jsonl
data/koala_iclr2026/global_test_labels.jsonl
data/koala_iclr2026/koala_reference_current.jsonl
```

当前规模：

```text
global_train.jsonl          13,378 ICLR26 papers with labels/reviews
global_test_public.jsonl       349 ICLR26 papers, public fields only
global_test_labels.jsonl       349 hidden labels for offline eval
koala_reference_current.jsonl  349 Koala platform papers used as target distribution snapshot
```

训练和离线评估的边界：

- `global_train.jsonl` 可以用于训练，包含 ICLR decision、official reviews、meta reviews 和 score summaries。
- `global_test_public.jsonl` 模拟线上可见字段，不含 decision/reviews/source status。
- `global_test_labels.jsonl` 只用于 offline evaluation，不进入 agent prompt 或线上决策。
- `koala_reference_current.jsonl` 是 Koala 平台当前论文流快照，用于构造 ICLR26 test distribution；它不是 labeled training data。

## PDF 解析策略

现有 `data/pdf_cache/` 是 ICLR26 offline 数据的旧版派生缓存，不是官方比赛原始数据。

`Paper2Markdown-V0` 是旧流程：

```text
ICLR26 JSONL record
  -> OpenReview PDF URL
  -> data/pdf_cache/raw/{paper_id}.pdf
  -> PyMuPDF text extraction
  -> data/pdf_cache/parsed/{paper_id}.json
```

Mila 当前 cache 状态：

```text
data/pdf_cache/parsed/*.json  2,349
ok=true/full_text              1,138
download_failed                1,211
```

这个 cache 覆盖 `global_train` 的 2,000 篇子集和 `global_test_public` 的 349 篇，不覆盖 Koala/ICML26 live papers。

`Paper2Markdown-V1` 是当前 Mila smoke 验证过的 Marker non-LLM raw parse baseline。它在 RaftPPI / `Dp1RM3gPg8` 上输出 Markdown、Marker JSON、Marker chunks、meta TOC、figure images 和 `parse_report.json`；相比 V0，它有 block/page/section provenance，更适合构造 review-agent chunks。但 V1 raw output 仍保留作者、单位、code release 和 acknowledgement 等匿名性风险，因此不能直接作为模型输入。

`Paper2Markdown-V2` 在 V1 raw parse 之后增加 deterministic model-facing artifact gate。原则是保留 raw parse cache 以便审计，但所有 review-agent / predictor 会读到的 payload 必须经过同一套安全出口：

- `sanitized_v2.txt` 删除 title page author/affiliation block、acknowledgement/funding blocks、email、OpenReview/status/camera-ready、PDF 行号污染等泄漏文本。
- `chunks_v2_anonymized.jsonl` 从 Marker blocks 重新生成，跳过 abstract 前的作者/单位 block，并对每条 chunk 独立清洗。
- chunk 页码从 Marker block id 的 `/page/{i}/...` 恢复成真实 1-based PDF page number，并强制满足 `1 <= page_start <= page_end <= page_count`；不再使用 Marker internal `page` id。
- `assets.json` 标注 image crop 是否可作为 visual evidence，默认过滤过小、极端长宽比、疑似 margin line-number strip 的 crop。
- `sanitization_report.json` 不只检查 `sanitized_v2.txt`，还要覆盖 chunks 等所有 model-facing text artifacts。

`Paper2Markdown-V3` 是默认 agent 输入层：不再建议把 `sanitized_v2.txt` 整段塞进 prompt，而是从匿名 chunks 生成 `model_text_v3.txt`，格式为 `[p. N | section: ... | type: ...] evidence`。V3 同时输出 `main_body_chunks.jsonl`、`appendix_chunks.jsonl`、`reference_chunks.jsonl`，让 predictor 默认只看 main body，appendix/reference 走 on-demand retrieval。V3 还会过滤 LLM usage / disclosure / reproducibility / author statement sections，并修复粘连 URL，例如 `Ihttps://...`。

parser 改进目标是保留 legacy JSON 兼容层，同时新增 review-ready parse cache：

```text
data/pdf_parse_cache/v1/{paper_id}/
  paper.md
  paper.blocks.json
  assets.json
  chunks_v3_anonymized.jsonl
  model_text_v3.txt
  main_body_chunks.jsonl
  appendix_chunks.jsonl
  reference_chunks.jsonl
  parse_report.json
  sanitization_report.json
  legacy_payload.json
```

默认策略是 Marker 主线解析，Docling 做 fallback / visual asset enhancement；不在 parser 阶段使用 LLM summarization、semantic compression、citation deletion 或随机 section sampling。

## Agent 架构

当前 live agent 是 LLM-led `review_director`。传统 predictor、full-text/table evidence、discussion features、lifecycle checks 和 citation selector 被暴露为工具；LLM 负责计划选题、写评论、综合讨论、写 verdict，并在 deterministic safety guard 前做 self-critique。

实现模块：

```text
koala_strategy/agent/        agent orchestration
koala_strategy/models/       offline predictors and calibration
koala_strategy/paper/        PDF parsing, section parsing, table evidence
koala_strategy/discussion/   discussion/comment features
koala_strategy/llm/          LLM prompts and providers
scripts/                     local entrypoints
agent_configs/               agent prompts
```

## 本地与 Mila 分工

本地 workstation：

- 代码修改
- 轻量单元测试
- 静态检查
- 文档维护

Mila cluster：

- 读取 `data/koala_iclr2026/`
- PDF batch parsing
- 训练、评估、benchmark
- 大规模 parse cache 生成

如果本地没有 `data/`，这是预期状态；不要在本地伪造比赛数据路径。重数据和实验都应在 Mila 上运行。

## 常用命令

本地安装：

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
```

Mila 环境初始化：

```bash
source /home/mila/j/jianan.zhao/scratch/Utils/shell/init.sh ReviewAgent
```

构造处理后的 ICLR26 examples：

```bash
python -m koala_strategy.cli build-iclr-dataset
```

训练并评估离线模型：

```bash
python -m koala_strategy.cli train-all
```

解析 ICLR26 PDFs 并训练 full-text evidence head：

```bash
python -m koala_strategy.cli parse-pdfs --train-limit 1200 --workers 3
python -m koala_strategy.cli train-fulltext-evidence --train-limit 1200 --model-type logreg
python -m koala_strategy.cli train-fast-text-evidence --train-limit 2000 --mode sections_safe
```

给一篇 offline public-test paper 打分：

```bash
python -m koala_strategy.cli score-paper PklMD8PwUy
```

运行测试：

```bash
python -m pytest -q
```

## Offline 结果解释

README 中的 offline AUROC/AUPRC/Brier/Log loss 都只衡量 **ICLR26 surrogate task**，不能直接等同于 ICML26 比赛表现。它们的价值是帮助我们选择模型、调 prompt、检查 leakage，并建立 score calibration。

当前 V1 title/abstract model 在 349 篇 ICLR26 held-out public test papers 上：

```text
AUROC: 0.6817
AUPRC: 0.6690
Brier: 0.2248
Log loss: 0.6367
Top 27% precision: 0.7184
Suggested score MAE: 1.1357
Mean predicted accept: 0.4219
Mean uncertainty: 0.3591
```

当前 V6 PDF text-evidence enhancement 在有 parsed PDFs 的 ICLR26 held-out 子集上：

```text
V1 base AUROC on same subset: 0.6824
V6 sections-safe text-evidence AUROC: 0.9347
V6 sections-safe text-evidence AUPRC: 0.8790
V6 sections-safe text-evidence Brier: 0.0883
V6 sections-safe text-evidence Log loss: 0.3044
V6 sections-safe text-evidence Pearson vs accept: 0.8112
V6 sections-safe text-evidence Spearman vs accept: 0.8040
V6 sections-safe text-evidence Top 27% precision: 0.8913
```

这些高分必须继续用 leakage guard 审查。PDF/full-text 模型不得使用 author identity、acknowledgement、OpenReview status、official reviews、meta reviews、decision、post-publication signals 或其他比赛时不可见信息。

## 参考资料

- Koala competition page: https://koala.science/competition
- Raw rules: https://koala.science/COMPETITION.md
- Agent skill guide: https://koala.science/skill.md
- Starter repo: https://github.com/koala-science/peer-review-agents
