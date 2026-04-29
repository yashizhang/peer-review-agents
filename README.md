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

## PDF-to-Markdown 最终流程

官方 Koala/ICML26 只提供 paper metadata、`pdf_url`、可能的 `tarball_url` / `github_urls`，**不提供已经处理好的 Markdown、全文 JSON、blocks 或 chunks**。自建 ICLR26 offline 数据也只在 JSONL 里保存 metadata、label/review 字段和 OpenReview PDF URL；现有 `data/pdf_cache/` 是旧 PyMuPDF 文本 cache，不是 review-ready corpus。

因此正式策略是统一把 ICLR26 offline PDFs 和 Koala/ICML26 live PDFs 都处理成同一种 `Paper2Markdown-V3` cache。agent 和 predictor 默认读 V3 的 model-facing artifacts，不直接读原始 PDF，也不直接读 raw Marker Markdown。

### 版本边界

```text
Paper2Markdown-V0
  PyMuPDF legacy text cache
  output: data/pdf_cache/parsed/{paper_id}.json
  status: 仅用于旧 full_text features，不再作为默认 review 输入

Paper2Markdown-V1
  Marker non-LLM raw parse
  output: raw Markdown + Marker blocks/chunks + images + parse_report
  status: 可审计 raw cache，不直接给模型

Paper2Markdown-V2
  V1 raw parse + deterministic scrub / artifact gate
  fixes: author/affiliation、acknowledgement、line numbers、bad page ids、bad image crops
  status: 安全出口基础层

Paper2Markdown-V3
  V2 chunks + final model payload split
  output: model_text_v3.txt + main_body/appendix/reference chunks + filtered assets
  status: 默认 review/factsheet/predictor text backbone
```

V3 的核心原则：

- Parser 阶段不使用 LLM，不做 summarization、semantic compression、citation deletion 或随机 section sampling。
- Raw Marker output 永远保留用于 audit；model-facing payload 单独生成。
- 默认模型输入是 `model_text_v3.txt`，来源于 `main_body_chunks.jsonl`。
- `appendix_chunks.jsonl` 和 `reference_chunks.jsonl` 默认不进入 predictor 主输入，只做 on-demand retrieval。
- 关键图、表、公式仍可回看 raw PDF / selected assets；V3 是 text backbone，不是 raw PDF 的完全替代品。

### 目录约定

正式 cache 根目录放在 Mila scratch，不提交 Git：

```text
data/processed_papers/
  icml26/
    {paper_id}/...
  iclr26/
    {paper_id}/...
  {run_name}/
    input_manifest.json
    raw/
      {paper_id}.pdf
    marker_raw/
      {paper_id}/
        parse_report.json
        marker_markdown/
          {paper_id}/
            {paper_id}.md
            {paper_id}_meta.json
            *.jpeg
        marker_chunks/
          {paper_id}/
            {paper_id}.json
    processed_v3/
      {paper_id}/...
    processed_v3_summary.json

也可以保留运行快照目录：
data/processed_papers/{run_name}/
  input_manifest.json
  raw/
    {paper_id}.pdf
  marker_raw/
    {paper_id}/
      parse_report.json
      marker_markdown/
        {paper_id}/
          {paper_id}.md
          {paper_id}_meta.json
          *.jpeg
      marker_chunks/
        {paper_id}/
          {paper_id}.json
  processed_v3/
    {paper_id}/
      paper.md
      paper.blocks.json
      marker_meta.json
      sanitized_v3.txt
      chunks_v3_anonymized.jsonl
      main_body_chunks.jsonl
      appendix_chunks.jsonl
      reference_chunks.jsonl
      model_text_v3.txt
      appendix_text_v3.txt
      reference_text_v3.txt
      assets/
      assets_all/
      assets.json
      parse_report.json
      sanitization_report.json
  processed_v3_summary.json
```

`input_manifest.json` 每条至少包含：

```json
{
  "paper_id": "Dp1RM3gPg8",
  "source": "iclr_public_test",
  "title": "Fast Proteome-Scale Protein Interaction Retrieval via Residue-Level Factorization",
  "pdf_path": "data/processed_papers/<run_name>/raw/Dp1RM3gPg8.pdf",
  "url": "https://openreview.net/pdf?id=Dp1RM3gPg8",
  "bytes": 1474383,
  "sha256": "..."
}
```

### 生产流水线脚本

已新增一组 V3 批处理脚本（均在 `scripts/` 下）：

- `scripts/build_parse_manifests.py`：从官方队列 CSV / ICLR JSONL 生成 manifest。
- `scripts/jobs/p2m_v3_shared_worker.py`：单个分片 worker，下载 PDF、跑 Marker（markdown/chunks）、postprocess、写 summary。
- `scripts/jobs/p2m_v3_shared_worker.sbatch`：Slurm 模板。
- `scripts/jobs/launch_p2m_v3_workers.py`：提交多 shard job（默认 4 个 `short-unkillable` + 剩余 `unkillable`）。
- `scripts/jobs/monitor_parse_jobs.py`：5 分钟轮询任务状态。
- `scripts/jobs/consolidate_p2m_v3_run.py`：将一轮 run 的 `processed_v3` 结果同步到 `data/processed_papers/{subset}/{paper_id}`。
- `scripts/upload_processed_to_hf.py`：将 `icml26/{paper_id}`、`iclr26/{paper_id}` 上传到 `jzshared/agent_paper_review`。

Koala/ICML26 的 `pdf_url` 可能是 `/storage/pdfs/<uuid>.pdf` 这种相对路径；下载时要拼接 Koala storage base URL。ICLR26 使用 OpenReview absolute PDF URL。

### Mila 环境

所有 PDF batch parsing 都在 Mila 跑。本地只做代码修改、轻量测试和文档维护。

```bash
cd /home/mila/j/jianan.zhao/scratch/ReviewAgent
source /home/mila/j/jianan.zhao/scratch/Utils/shell/init.sh ReviewAgent

export LD_LIBRARY_PATH=/cvmfs/ai.mila.quebec/apps/x86_64/debian/anaconda/3/lib:${LD_LIBRARY_PATH:-}
export XDG_CACHE_HOME=/network/scratch/j/jianan.zhao/.cache/reviewagent
export TRITON_CACHE_DIR=/network/scratch/j/jianan.zhao/.cache/reviewagent/triton
export TMPDIR=/network/scratch/j/jianan.zhao/tmp/reviewagent
export HF_HOME=/network/scratch/j/jianan.zhao/.cache/reviewagent/huggingface
export HUGGINGFACE_HUB_CACHE=/network/scratch/j/jianan.zhao/.cache/reviewagent/huggingface/hub
export TRANSFORMERS_CACHE=/network/scratch/j/jianan.zhao/.cache/reviewagent/huggingface/transformers
export HF_DATASETS_CACHE=/network/scratch/j/jianan.zhao/.cache/reviewagent/huggingface/datasets
export TORCH_EXTENSIONS_DIR=/network/scratch/j/jianan.zhao/.cache/reviewagent/torch_extensions
export WANDB_CACHE_DIR=/network/scratch/j/jianan.zhao/.cache/reviewagent/wandb
export TORCH_DEVICE=cuda
```

Marker raw parse 使用项目环境里的 binary：

```bash
/network/scratch/j/jianan.zhao/ReviewAgent/.venv/bin/marker_single
```

### 一条命令链（推荐）

按时间顺序（越新的越早）处理 ICML in-review 截点论文：

```bash
RUN_NAME=icml26_20260429_review
MANIFEST=data/processed_papers/manifests/icml26_due_queue.json
mkdir -p data/processed_papers/manifests

python scripts/build_parse_manifests.py icml \
  --csv data/koala_cache/reports/koala_in_review_paper_review_close_times_20260429_1417_montreal.csv \
  --output ${MANIFEST}

python - <<PY
from pathlib import Path
import json
manifest = json.loads(Path("${MANIFEST}").read_text())
# 写入按时间倒序的待处理 manifest（已按时间排序）
print(json.dumps({"n": len(manifest)}, indent=2))
PY

python scripts/jobs/launch_p2m_v3_workers.py \
  --run-root data/processed_papers \
  --run-name ${RUN_NAME} \
  --manifest ${MANIFEST} \
  --shard-count 5 \
  --short-unkillable-workers 4 \
  --short-partition short-unkillable \
  --short-gres gpu:a100l:4 \
  --short-time 03:00:00 \
  --rest-partition unkillable \
  --rest-gres gpu:a100l:1 \
  --rest-time 2-00:00:00 \
  --marker-timeout-seconds 240 \
  --max-retries 2
```

监控：

```bash
python scripts/jobs/monitor_parse_jobs.py --once --run-name ${RUN_NAME} --run-root data/processed_papers <job_id_0> <job_id_1> ...
```

### Consolidate + 上传 HF

```bash
python scripts/jobs/consolidate_p2m_v3_run.py \
  --run-root data/processed_papers \
  --run-name ${RUN_NAME} \
  --subset icml26 \
  --overwrite

python scripts/upload_processed_to_hf.py \
  --dataset-root data/processed_papers \
  --hf-repo jzshared/agent_paper_review \
  --subset icml26 \
  --token $HF_TOKEN
```

### Stage 0: Inventory

先生成 `input_manifest.json`，再下载 PDF 到 `raw/{paper_id}.pdf`。不要直接对 remote URL 反复 parse；PDF binary 要缓存并记录 SHA256、bytes、page_count。

ICLR26 输入来自：

```text
data/koala_iclr2026/global_train.jsonl
data/koala_iclr2026/global_test_public.jsonl
```

Koala/ICML26 输入来自 Koala API / MCP paper feed：

```text
paper_id
title
abstract
domains
pdf_url
tarball_url
github_urls
status
created_at / updated_at
```

### Stage 1: Marker Raw Parse

每篇 PDF 默认跑两个 non-LLM Marker output：`markdown` 和 `chunks`。当前 V3 smoke 是保守实现：同一篇 PDF parse 两次，换取输出稳定、debug 简单。

单篇命令形态：

```bash
PAPER_ID=Dp1RM3gPg8
PDF=data/processed_papers/<run_name>/raw/${PAPER_ID}.pdf
RAW_ROOT=data/processed_papers/<run_name>/marker_raw/${PAPER_ID}

mkdir -p "${RAW_ROOT}/marker_markdown" "${RAW_ROOT}/marker_chunks"

marker_single "${PDF}" \
  --output_dir "${RAW_ROOT}/marker_markdown" \
  --output_format markdown \
  --disable_tqdm \
  --disable_multiprocessing

marker_single "${PDF}" \
  --output_dir "${RAW_ROOT}/marker_chunks" \
  --output_format chunks \
  --disable_tqdm \
  --disable_multiprocessing
```

同目录还必须写 `parse_report.json`，至少包括：

```json
{
  "paper_id": "Dp1RM3gPg8",
  "pipeline": "marker_non_llm",
  "parser": "marker-pdf",
  "formats": ["markdown", "chunks"],
  "llm_enabled": false,
  "pdf_path": "...",
  "pdf_sha256": "...",
  "page_count": 18,
  "source": "iclr_public_test",
  "title": "..."
}
```

### Stage 2: V3 Postprocess

Marker raw parse 完成后，用 repo script 生成最终 model-facing artifacts：

```bash
python scripts/postprocess_marker_v3.py \
  --input-root data/processed_papers/<run_name>/marker_raw \
  --output-root data/processed_papers/<run_name>/processed_v3 \
  --copy-assets-all \
  --summary-path data/processed_papers/<run_name>/processed_v3_summary.json
```

也可以只处理特定 paper：

```bash
python scripts/postprocess_marker_v3.py \
  --input-root data/processed_papers/<run_name>/marker_raw \
  --output-root data/processed_papers/<run_name>/processed_v3 \
  --paper-id Dp1RM3gPg8 \
  --summary-path data/processed_papers/<run_name>/processed_v3_summary.json
```

V3 postprocess 做的事情：

- 从 Marker block id `/page/{i}/...` 恢复真实 1-based PDF 页码。
- 强制所有 chunk 满足 `1 <= page_start <= page_end <= page_count`。
- 跳过 abstract 前 author / affiliation / title-page metadata。
- 支持 `Abstract—...` 作为普通 Text block 的 IEEE 风格 PDF。
- 过滤 acknowledgement / funding / OpenReview status / LLM usage / author statement / reproducibility statement 等 section。
- 修复粘连 URL，例如 `Ihttps://...`。
- 过滤纯行号文本、PageHeader、PageFooter。
- 过滤过小、极端长宽比、疑似 margin strip 的 image crop。
- 将 chunks 拆成 main body、appendix、references 三个 view。
- 渲染默认 `model_text_v3.txt`，每段格式为：

```text
[p. 5 | section: 4 Experiments | type: TableGroup]
...
```

### Stage 3: 质量验收

每次 batch 完成后至少检查：

```bash
python - <<'PY'
import json
from pathlib import Path

summary = json.loads(Path("data/processed_papers/<run_name>/processed_v3_summary.json").read_text())
print("papers", len(summary))
print("ok", sum(1 for row in summary if row.get("ok")))
for row in summary:
    if not row.get("ok"):
        print(row["paper_id"], row.get("error"))
PY
```

对每篇 paper，`sanitization_report.json` 必须满足：

- `ok: true`
- `page_provenance.invalid_count == 0`
- `chunk_count > 0`
- `model_text_chars > 0`
- artifact leak audit 不命中 `OpenReview`、official review / meta-review 字段、decision / label 字段等。

如果某篇失败：

- `missing inputs`：先补跑 Marker raw parse。
- `chunk_count == 0`：检查 abstract/body start，可能是 parser block style 新边界。
- `invalid_count > 0`：禁止给 agent 使用，先修 page provenance。
- asset crop 噪声太多：看 `assets.json` 的 reject reason，必要时调 filter。

### Stage 4: Agent 使用规则

review / factsheet 默认读取：

```text
processed_v3/{paper_id}/model_text_v3.txt
processed_v3/{paper_id}/assets.json
processed_v3/{paper_id}/assets/*
```

retrieval 需要更多证据时再读：

```text
processed_v3/{paper_id}/appendix_chunks.jsonl
processed_v3/{paper_id}/reference_chunks.jsonl
processed_v3/{paper_id}/appendix_text_v3.txt
processed_v3/{paper_id}/reference_text_v3.txt
```

不要默认给 model 读：

```text
paper.md
paper.blocks.json
sanitized_v3.txt
assets_all/*
raw PDF
raw_records with labels/reviews/decisions
```

其中 `paper.md` / `paper.blocks.json` / `assets_all/` 是 audit/debug 用；`sanitized_v3.txt` 是全文审计文本，不是默认 prompt payload；raw PDF 只用于关键图表、公式和视觉 fidelity fallback。

### V3 smoke 结果和估时

最新 10 篇 Mila smoke：

```text
run root: data/processed_papers/v3_smoke_10/
final root: data/processed_papers/v3_smoke_10/processed_v3/
summary:   data/processed_papers/v3_smoke_10/processed_v3_summary.json
commit:    8c66f3a
result:    10 papers / ok 10
```

统计：

| subset | papers | pages | chunks | main | appendix | refs | assets kept/raw | invalid pages |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ICLR public/test sample | 5 | 140 | 944 | 347 | 563 | 34 | 44/51 | 0 |
| Koala/ICML sample | 5 | 106 | 1041 | 450 | 558 | 33 | 55/58 | 0 |
| total | 10 | 246 | 1985 | 797 | 1121 | 67 | 99/109 | 0 |

实测 short-unkillable job `9405494`：4 GPU，补跑 3 篇 / 77 页，wall time `00:06:59`。单 worker end-to-end 约 `10.9 sec/page`，这是保守估计，因为当前每篇跑 Marker markdown + chunks 两次。

按当前实现外推：

```text
ICLR downloaded cache: 1139 PDFs / 29300 pages
4 workers:  24-30h
8 workers:  12-15h
16 workers: 6-8h

ICLR public/test 349 papers: about 9000 pages
4 workers: about 7-8h

Koala/ICML current reference 349 papers: about 7400 pages by current sample
4 workers: about 6-7h
300 competition papers: about 5-6h plus PDF download time
```

全量 production batch 应使用 Slurm，优先用可并行 worker。不要在比赛交互时临时 parse PDF；应提前 batch preprocess 并缓存。

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
