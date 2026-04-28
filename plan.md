# 实现计划：Koala ICML26 Review Agent 系统

> 2026-04-27 更新：当前实验路线已经从“三个独立 Koala agent”调整为“一个 `review_director` agent + 内部 subagent personas + PDF full-text/table evidence V2 predictor”。新的精简计划见 [plan_v2.md](plan_v2.md)。下文保留作为 V1 原始设计和规则参考。

## 0. 背景与核心目标

我们要在 Koala Science ICML 2026 Agent Review Competition 中实现 1–3 个自动审稿 agent。Koala 的最终评价不是看评论数量，而是看 agent 的 verdict 与真实 ICML 2026 accept/reject 决策的相关性；agent 会读匿名 ICML26 paper、参与讨论，并提交 0–10 分 verdict。([Koala Science][1])

平台关键约束如下，代码必须遵守：

1. 每篇 paper 有 72 小时生命周期：`in_review` 0–48h 可发评论，`deliberating` 48–72h 可提交 verdict，之后 `reviewed`。([Koala Science][1])
2. 每个 agent 初始 100 karma；每篇 paper 第一条评论花 1 karma，同一 paper 后续评论花 0.1 karma，verdict 免费。([Koala Science][1])
3. 只有在 `in_review` 阶段发过至少一条评论的 agent，才能在 `deliberating` 阶段提交 verdict。([Koala Science][1])
4. verdict 必须引用至少 3 个不同的其他 agent 的评论，不能引用自己，也不能引用同一 OpenReview ID / 团队下的 agent。([Koala Science][1])
5. 每条评论必须附带一个 GitHub reasoning file URL；starter repo 规则要求先写 reasoning file、commit、push，然后提交可访问的 GitHub URL。([GitHub][2])
6. 不得使用同一篇 paper 的未来泄漏信息，例如 OpenReview reviews、scores、meta-reviews、decisions、accept/reject status、后续声誉、社媒讨论等。只能使用 paper 本身、references、平台保留的作者代码/工件、以及 release 前合理可得的 prior work。([GitHub][2])
7. 每个用户或团队最多注册 3 个 agent；starter repo 中每个 agent 是 hand-authored system prompt + API key，prompt 会与平台全局规则、platform skill 指南一起拼接。([Koala Science][1])

我们的工程目标不是“让 LLM 直接写审稿意见”，而是：

```text
ICLR26 training data
  → 训练 paper-only / pseudo-review / discussion-aware 预测系统
  → 为每篇 Koala paper 生成 calibrated prediction_bundle
  → agent 根据 bundle 选择 paper、写可引用评论、提交 calibrated verdict
```

最终系统要实现：

```text
1. ICLR26 数据训练与校准
2. Koala paper 在线解析与预测
3. 三个可独立运行的 Koala agents
4. 自动 reasoning file 写入、push、URL 验证
5. 评论与 verdict 自动化
6. dashboard / logs / dry-run / tests
```

---

# 1. 总体架构

不要把 Model A / B / C 当作三个 Koala agent。正确结构是：

```text
Shared backend models:
  Model A: paper-only accept predictor
  Model B: pseudo-review simulator / calibrator
  Model C: discussion-aware verdict calibrator

Koala agents:
  Agent 1: calibrated_decider
  Agent 2: rigor_auditor
  Agent 3: novelty_scout

每个 agent 都共享 Model A/B/C，但有不同 persona、不同 paper shard、不同评论风格。
```

最终运行图：

```text
Koala paper released
      ↓
paper_ingest / pdf_parse
      ↓
feature_extractor
      ↓
Model A + Model B
      ↓
prediction_bundle.json
      ↓
paper_selector decides whether to comment
      ↓
comment_writer creates concise, evidence-heavy comment
      ↓
reasoning_file_writer writes evidence file
      ↓
github_publisher pushes reasoning file and verifies URL
      ↓
agent posts comment during in_review
      ↓
paper enters deliberating
      ↓
discussion_analyzer extracts other-agent comments
      ↓
Model C updates p_accept
      ↓
citation_selector picks ≥3 valid external comments
      ↓
verdict_writer submits calibrated 0–10 verdict
```

---

# 2. Repo 结构

在 starter repo 基础上新增如下结构。不要破坏原 starter 的 `agent_definition/`、`agent_configs/`、`reva/` 结构。

```text
peer-review-agents/
  config.toml
  agent_configs/
    calibrated_decider/
      system_prompt.md
      .api_key
      config.json
    rigor_auditor/
      system_prompt.md
      .api_key
      config.json
    novelty_scout/
      system_prompt.md
      .api_key
      config.json

  koala_strategy/
    __init__.py

    config.py
    constants.py
    schemas.py
    logging_utils.py

    data/
      iclr_loader.py
      koala_cache.py
      dataset_builder.py
      contamination_guard.py

    paper/
      pdf_parser.py
      section_parser.py
      paper_features.py
      reference_features.py
      code_link_checker.py

    llm/
      providers.py
      prompts.py
      json_guard.py
      pseudo_review_panel.py
      paper_judge.py
      comment_summarizer.py

    models/
      feature_schema.py
      train_model_a.py
      train_model_b.py
      train_model_c.py
      train_stacker.py
      calibrate.py
      predict_paper_only.py
      predict_discussion.py
      uncertainty.py
      score_mapping.py
      export_bundle.py

    discussion/
      discussion_features.py
      claim_extractor.py
      citation_selector.py
      comment_quality.py

    agent/
      paper_selector.py
      comment_writer.py
      verdict_writer.py
      reasoning_writer.py
      github_publisher.py
      lifecycle_policy.py
      scheduler.py
      sharding.py

    platform/
      koala_client.py
      notifications.py
      skill_sync.py

    cli.py

  scripts/
    build_iclr_dataset.py
    train_all.py
    score_koala_paper.py
    run_agent.py
    run_all_agents.py
    dashboard.py
    dry_run_one_paper.py

  data/
    iclr26/
      raw/
      processed/
    koala_cache/
    predictions/
    logs/

  models/
    iclr26_v1/
      model_a.pkl
      model_b.pkl
      stacker.pkl
      calibrator.pkl
      model_c.pkl
      feature_schema.json
      calibration_card.md
      metrics.json

  reasoning/
    calibrated_decider/
    rigor_auditor/
    novelty_scout/

  tests/
    test_contamination_guard.py
    test_score_mapping.py
    test_json_guard.py
    test_citation_selector.py
    test_lifecycle_policy.py
    test_github_publisher.py
    test_paper_selector.py
    test_discussion_features.py
    test_end_to_end_dry_run.py
```

---

# 3. 配置文件

新增 `koala_strategy/config.py`，支持从环境变量和 `strategy_config.yaml` 读取配置。

创建 `strategy_config.yaml`：

```yaml
competition:
  max_agents: 3
  min_external_citations_for_verdict: 3
  initial_karma: 100.0
  first_comment_cost: 1.0
  followup_comment_cost: 0.1
  score_min: 0.0
  score_max: 10.0

paths:
  iclr_raw_dir: data/iclr26/raw
  iclr_processed_dir: data/iclr26/processed
  koala_cache_dir: data/koala_cache
  model_dir: models/iclr26_v1
  reasoning_dir: reasoning
  logs_dir: data/logs

models:
  version: iclr26_v1
  use_lightgbm: true
  fallback_to_sklearn: true
  n_folds: 5
  random_seed: 42
  calibration_method: isotonic
  embedding_model: configurable
  llm_provider: configurable

online_policy:
  dry_run: false
  max_first_comments_per_agent_per_day: 30
  max_total_active_papers_per_agent: 90
  min_paper_age_hours_to_comment: 4
  max_paper_age_hours_to_comment: 44
  target_min_other_agents_for_comment: 2
  target_max_other_agents_for_comment: 9
  skip_if_comment_count_too_high: 18
  verdict_deadline_priority_hours: 8
  min_comment_quality_to_cite: 0.55
  max_comments_per_paper_by_agent: 2

agents:
  calibrated_decider:
    shard_id: 0
    shard_mod: 3
    focus: calibrated_decision
    domains:
      - Theory
      - Optimization
      - Probabilistic-Methods
      - Reinforcement-Learning

  rigor_auditor:
    shard_id: 1
    shard_mod: 3
    focus: experimental_rigor
    domains:
      - Deep-Learning
      - NLP
      - Computer-Vision
      - Generative-Models
      - Robotics

  novelty_scout:
    shard_id: 2
    shard_mod: 3
    focus: novelty_literature
    domains:
      - NLP
      - Trustworthy-ML
      - Graph-Learning
      - Healthcare-Science-Applications
      - Multimodal
```

---

# 4. 数据 schema

在 `koala_strategy/schemas.py` 中定义 Pydantic models。

```python
class PaperRecord(BaseModel):
    paper_id: str
    title: str
    abstract: str | None = None
    full_text: str | None = None
    sections: dict[str, str] = {}
    references: list[str] = []
    domains: list[str] = []
    pdf_url: str | None = None
    code_urls: list[str] = []
    release_time: datetime | None = None
    status: Literal["in_review", "deliberating", "reviewed"] | None = None
    comment_count: int = 0
    participant_count: int = 0


class ICLRTrainingExample(BaseModel):
    paper_id: str
    title: str
    abstract: str
    full_text: str
    sections: dict[str, str]
    references: list[str]
    domains: list[str]
    decision: Literal["accept", "reject"]
    official_reviews: list[dict] = []
    meta_review: str | None = None
    review_mean: float | None = None
    review_confidence_mean: float | None = None


class PseudoReview(BaseModel):
    persona: str
    novelty: int
    technical_soundness: int
    empirical_rigor: int
    clarity: int
    significance: int
    reproducibility: int
    claim_evidence_alignment: int
    missing_baseline_severity: int
    fatal_flaw_severity: int
    fatal_flaws: list[str]
    strongest_accept_signal: str
    strongest_reject_signal: str
    confidence: int
    recommended_score_band: str
    short_rationale: str


class PredictionBundle(BaseModel):
    paper_id: str
    domain: str | None
    model_version: str
    paper_only: dict
    pseudo_review_panel: dict
    discussion_update: dict | None = None
    agent_instruction: dict


class CommentRecord(BaseModel):
    comment_id: str
    paper_id: str
    author_agent: str
    owner_id: str | None = None
    parent_id: str | None = None
    content_markdown: str
    created_at: datetime
    extracted_claims: list[dict] = []
    quality_score: float | None = None
```

---

# 5. 本地数据库

实现 SQLite cache：`koala_strategy/data/koala_cache.py`。

表结构：

```sql
CREATE TABLE IF NOT EXISTS papers (
  paper_id TEXT PRIMARY KEY,
  title TEXT,
  abstract TEXT,
  domains_json TEXT,
  release_time TEXT,
  status TEXT,
  pdf_url TEXT,
  parsed_text_path TEXT,
  comment_count INTEGER,
  participant_count INTEGER,
  last_seen TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
  paper_id TEXT,
  model_version TEXT,
  prediction_bundle_json TEXT,
  created_at TEXT,
  PRIMARY KEY (paper_id, model_version)
);

CREATE TABLE IF NOT EXISTS comments (
  comment_id TEXT PRIMARY KEY,
  paper_id TEXT,
  author_agent TEXT,
  owner_id TEXT,
  parent_id TEXT,
  content_markdown TEXT,
  extracted_claims_json TEXT,
  quality_score REAL,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS actions (
  action_id TEXT PRIMARY KEY,
  agent_name TEXT,
  paper_id TEXT,
  action_type TEXT,
  content_hash TEXT,
  github_file_url TEXT,
  karma_cost REAL,
  status TEXT,
  created_at TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS verdicts (
  paper_id TEXT,
  agent_name TEXT,
  score REAL,
  verdict_body TEXT,
  citation_ids_json TEXT,
  submitted BOOLEAN,
  created_at TEXT,
  PRIMARY KEY (paper_id, agent_name)
);

CREATE TABLE IF NOT EXISTS agent_state (
  agent_name TEXT PRIMARY KEY,
  karma_remaining REAL,
  last_notification_sync TEXT,
  last_paper_sync TEXT,
  active_papers_json TEXT
);
```

要求：

```text
- 所有在线 action 必须先写 action pending，再执行，再更新 status。
- 用 content_hash 防止重复发同一评论。
- dry_run 模式下只写日志，不调用真实 post/submit。
```

---

# 6. ICLR26 数据训练 pipeline

## 6.1 输入假设

实现一个灵活 loader，支持以下任意输入格式：

```text
data/iclr26/raw/papers.jsonl
data/iclr26/raw/reviews.jsonl
data/iclr26/raw/decisions.jsonl
data/iclr26/raw/meta_reviews.jsonl
```

也支持用户提供 CSV。Codex 不要假设固定字段名，做字段映射配置：

```yaml
iclr_field_map:
  paper_id: paper_id
  title: title
  abstract: abstract
  full_text: full_text
  decision: decision
  reviews: reviews
  meta_review: meta_review
```

`build_iclr_dataset.py` 输出：

```text
data/iclr26/processed/examples.jsonl
data/iclr26/processed/feature_table.parquet
data/iclr26/processed/pseudo_reviews.jsonl
```

## 6.2 数据清洗规则

实现：

```python
def normalize_decision(raw_decision: str) -> Literal["accept", "reject"] | None:
    ...
```

规则：

```text
- Accept / Spotlight / Oral / Poster / Accepted → accept
- Reject / Rejected → reject
- Withdrawn / Desk Reject / Not reviewed / Unknown → None，默认排除
```

注意：训练 Model A/B 时，official reviews 和 meta-review 不能作为输入特征，只能作为：

```text
- Model C 的 proxy discussion
- Model B 的 auxiliary labels
- metrics / calibration 分析
```

## 6.3 contamination guard

实现 `koala_strategy/data/contamination_guard.py`。

目的：

```text
防止线上 Koala paper 与 ICLR26 training paper near-duplicate 时，系统把该 ICLR paper 的 reviews/decision 当作 evidence。
```

函数：

```python
def title_similarity(a: str, b: str) -> float: ...
def abstract_similarity(a: str, b: str) -> float: ...
def embedding_similarity(a: np.ndarray, b: np.ndarray) -> float: ...

def is_near_duplicate(koala_paper: PaperRecord, train_paper: ICLRTrainingExample) -> bool:
    return (
        title_similarity(...) >= 0.85
        or abstract_similarity(...) >= 0.92
        or embedding_similarity(...) >= 0.90
    )
```

线上 inference 规则：

```text
- 如果 Koala paper 与 ICLR training paper near-duplicate：
  - 不允许使用该 training paper 的 official reviews。
  - 不允许使用该 training paper 的 decision。
  - 不允许把该 paper 作为 kNN evidence。
  - 只允许使用全局训练好的模型参数。
```

实现测试：

```text
tests/test_contamination_guard.py
- identical title returns true
- slightly modified title + same abstract returns true
- unrelated paper returns false
```

---

# 7. Feature extraction

## 7.1 PDF / text parsing

实现 `paper/pdf_parser.py`：

```python
def parse_pdf_to_text(pdf_path_or_url: str) -> ParsedPaperText:
    ...
```

如果 starter repo 或平台 skill 已有 PDF 获取工具，优先使用已有工具。否则：

```text
- 支持下载 PDF
- 用 pymupdf / pdfminer 解析文本
- 提取标题、摘要、section headings、tables/captions 粗略文本
- 保留 page markers，例如 [Page 3]
```

`ParsedPaperText`：

```python
class ParsedPaperText(BaseModel):
    title: str | None
    abstract: str | None
    full_text: str
    sections: dict[str, str]
    page_texts: dict[int, str]
    references: list[str]
    figure_captions: list[str]
    table_captions: list[str]
```

## 7.2 结构化特征

实现 `paper/paper_features.py`：

```python
def extract_structured_features(parsed: ParsedPaperText) -> dict[str, float | int | str]:
    ...
```

特征包括：

```text
长度与结构：
- num_pages_estimate
- num_tokens
- num_sections
- has_abstract
- has_limitations_section
- has_reproducibility_statement

实验相关：
- num_tables
- num_figures
- has_ablation_keyword
- has_baseline_keyword
- has_sota_keyword
- has_error_bar_keyword
- has_confidence_interval_keyword
- has_statistical_significance_keyword
- num_dataset_mentions
- num_metric_mentions

理论相关：
- has_theorem
- has_lemma
- has_proposition
- has_proof
- num_equation_markers
- assumption_keyword_count

代码/复现：
- has_github_url
- has_code_url
- has_appendix
- has_hyperparameter_details

引用：
- num_references
- recent_reference_ratio_estimate
- arxiv_reference_count
```

## 7.3 Embeddings

实现 `llm/providers.py` 中的 provider abstraction：

```python
class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: list[str]) -> np.ndarray:
        ...
```

支持：

```text
- local sentence-transformers fallback
- OpenAI / Anthropic / other provider if env exists
```

提取：

```text
- title_abstract_embedding
- intro_embedding
- method_embedding
- experiment_embedding
- conclusion_embedding
```

---

# 8. Model B：pseudo-review panel

## 8.1 Prompt 模板

实现 `llm/prompts.py`：

```python
PSEUDO_REVIEW_PROMPT = """
You are a pseudo-reviewer used for calibration.
You are not writing a public review.
Your task is to extract structured decision-relevant features from the paper.

Do not use external reviews, decisions, acceptance status, social media, or later impact.

Return only valid JSON.

Persona: {persona}

Evaluate:
- novelty
- technical soundness
- empirical rigor
- clarity
- significance
- reproducibility
- claim-evidence alignment
- missing baselines
- fatal flaws

Use 1–10 scores.
Use severity 0–4:
0 = none
1 = minor
2 = moderate
3 = serious
4 = likely fatal

JSON schema:
{
  "novelty": int,
  "technical_soundness": int,
  "empirical_rigor": int,
  "clarity": int,
  "significance": int,
  "reproducibility": int,
  "claim_evidence_alignment": int,
  "missing_baseline_severity": int,
  "fatal_flaw_severity": int,
  "fatal_flaws": [string],
  "strongest_accept_signal": string,
  "strongest_reject_signal": string,
  "confidence": int,
  "recommended_score_band": "clear_reject|weak_reject|borderline|weak_accept|strong_accept",
  "short_rationale": string
}

Paper:
{paper_text}
"""
```

Personas：

```python
PSEUDO_REVIEW_PERSONAS = [
    "technical_skeptic",
    "experimental_rigor_auditor",
    "novelty_and_prior_work_checker",
    "clarity_and_significance_reviewer",
    "area_chair_calibrator",
]
```

## 8.2 JSON guard

实现 `llm/json_guard.py`：

```python
def extract_json_object(text: str) -> dict:
    ...
def validate_pseudo_review(data: dict) -> PseudoReview:
    ...
def repair_or_retry_invalid_json(...):
    ...
```

要求：

```text
- LLM 输出必须 parse 成 JSON。
- 如果失败，最多 retry 2 次。
- 如果仍失败，写入 fallback review：
  scores = neutral, confidence = 1, rationale = "parse failed"
```

## 8.3 聚合 pseudo-review features

实现 `llm/pseudo_review_panel.py`：

```python
def run_pseudo_review_panel(parsed_paper: ParsedPaperText) -> list[PseudoReview]:
    ...

def aggregate_pseudo_reviews(reviews: list[PseudoReview]) -> dict[str, float]:
    ...
```

聚合特征：

```text
mean_novelty
min_novelty
max_novelty
std_novelty

mean_technical_soundness
min_technical_soundness
std_technical_soundness

mean_empirical_rigor
min_empirical_rigor
std_empirical_rigor

mean_clarity
mean_significance
mean_reproducibility

max_fatal_flaw_severity
mean_fatal_flaw_severity
mean_missing_baseline_severity
mean_claim_evidence_alignment

panel_disagreement
num_acceptish_reviewers
num_rejectish_reviewers
mean_confidence
```

---

# 9. Model A：paper-only accept predictor

## 9.1 训练输入

Model A 输入：

```text
- structured features
- section embeddings
- basic LLM judge features
- domain features
```

不要把 official reviews、meta-review、decision text 放进 Model A 特征。

实现：

```python
def build_model_a_features(examples: list[ICLRTrainingExample]) -> tuple[pd.DataFrame, np.ndarray]:
    ...
```

## 9.2 模型

优先：

```text
LightGBMClassifier
```

fallback：

```text
sklearn HistGradientBoostingClassifier
LogisticRegression
RandomForestClassifier
```

实现 OOF training：

```python
def train_model_a_oof(X, y, groups=None, n_folds=5):
    ...
```

分组 split：

```text
- 如果 embedding clustering 可用：按 cluster 做 GroupKFold
- 否则 StratifiedKFold
```

输出：

```text
model_a.pkl
model_a_oof_predictions.npy
model_a_metrics.json
```

metrics：

```text
AUROC
AUPRC
Brier score
log loss
top_27_percent_precision
calibration_bins
```

---

# 10. Model B：pseudo-review calibrator

Model B 输入 pseudo-review 聚合特征，输出：

```text
p_accept_from_pseudo_reviews
predicted_review_mean
predicted_meta_stance
```

实现：

```python
def train_model_b(X_B, y_accept, review_mean=None, meta_stance=None):
    ...
```

最低要求：

```text
- accept/reject classifier 必须实现
```

可选辅助头：

```text
- official_review_mean regressor
- meta_stance classifier
```

输出：

```text
model_b.pkl
model_b_aux_review_mean.pkl, optional
model_b_aux_meta_stance.pkl, optional
model_b_metrics.json
```

---

# 11. Stacker + calibration

把 Model A 和 Model B 合并。

```python
X_stack = [
    logit(p_A_oof),
    logit(p_B_oof),
    model_A_uncertainty_estimate,
    model_B_panel_disagreement,
    domain_prior_accept_rate,
    has_code,
    has_ablation_keyword,
    has_theorem,
]
```

训练：

```python
stacker = LogisticRegression(C=0.5)
calibrator = IsotonicRegression(out_of_bounds="clip")
```

实现：

```python
def train_stacker_and_calibrator(...):
    ...
```

输出：

```text
p_accept_paper_only
uncertainty
domain_calibrated_percentile
recommended_score_range
top_positive_features
top_negative_features
```

---

# 12. Model C：discussion-aware verdict calibrator

## 12.1 用 ICLR official reviews 模拟 Koala discussion

因为当前没有 Koala discussion → ICML final decision 的训练数据，用 ICLR26 official reviews 当作 proxy discussion。

实现：

```python
def extract_discussion_features_from_reviews(reviews: list[dict]) -> dict[str, float]:
    ...
```

特征：

```text
positive_claim_count
negative_claim_count
max_fatal_flaw_severity
mean_fatal_flaw_severity
novelty_support_score
novelty_concern_score
reproducibility_concern_score
missing_baseline_concern_score
theory_concern_score
clarity_concern_score
independent_fatal_flaw_agreement
independent_positive_agreement
review_confidence_mean
review_disagreement
strongest_positive_signal
strongest_negative_signal
```

## 12.2 训练 Model C

输入：

```python
X_C = [
    logit(p_accept_paper_only_oof),
    discussion_features,
    domain_features,
]
```

输出：

```text
p_accept_final
score_0_10
```

模型：

```text
LogisticRegression with regularization
```

实现：

```python
def train_model_c(p_paper_only_oof, review_discussion_features, y):
    ...
```

要求：

```text
- Model C 不要让 discussion completely override prior。
- 使用 regularization。
- 输出 feature coefficients 到 metrics.json，方便 debug。
```

---

# 13. Score mapping

实现 `models/score_mapping.py`。

不要简单 `score = 10 * p_accept`。Koala score 是质量分档，最终 accept 低基率，所以用 percentile/rank calibration。

函数：

```python
def probability_to_quality_percentile(
    p_accept: float,
    domain: str | None,
    current_pool_distribution: list[float] | None = None,
) -> float:
    ...

def percentile_to_koala_score(q: float) -> float:
    if q < 0.20:
        return 1.5 + 1.5 * q / 0.20
    elif q < 0.73:
        return 3.0 + 2.0 * (q - 0.20) / 0.53
    elif q < 0.90:
        return 5.0 + 2.0 * (q - 0.73) / 0.17
    elif q < 0.98:
        return 7.0 + 2.0 * (q - 0.90) / 0.08
    else:
        return 9.0 + 1.0 * (q - 0.98) / 0.02
```

加入 uncertainty shrinkage：

```python
def shrink_score_for_uncertainty(score: float, uncertainty: float, prior_score: float = 4.8) -> float:
    confidence = max(0.0, min(1.0, 1.0 - uncertainty))
    return confidence * score + (1.0 - confidence) * prior_score
```

测试：

```text
q=0.10 → score < 3
q=0.50 → score between 3 and 5
q=0.80 → score between 5 and 7
q=0.95 → score between 7 and 9
q=0.99 → score > 9
```

---

# 14. prediction_bundle 输出

线上每篇 paper 生成：

```json
{
  "paper_id": "abc123",
  "domain": "NLP",
  "model_version": "iclr26_v1",
  "paper_only": {
    "p_accept": 0.57,
    "quality_percentile": 0.74,
    "uncertainty": 0.22,
    "recommended_score_range": [5.2, 6.5],
    "main_positive_evidence": [
      "Ablation partially isolates the proposed mechanism",
      "Strong main benchmark performance"
    ],
    "main_negative_evidence": [
      "Baseline coverage may be incomplete",
      "Limited robustness analysis"
    ],
    "do_not_score_above": 6.8
  },
  "pseudo_review_panel": {
    "mean_novelty": 6.8,
    "mean_soundness": 6.4,
    "mean_empirical_rigor": 5.7,
    "mean_clarity": 7.3,
    "mean_significance": 6.9,
    "max_fatal_flaw_severity": 1,
    "panel_disagreement": 0.31,
    "p_accept_from_panel": 0.54
  },
  "discussion_update": {
    "available": false
  },
  "agent_instruction": {
    "comment_strategy": "balanced_borderline",
    "verdict_strategy": "weak_accept_if_no_new_fatal_flaw"
  }
}
```

实现：

```python
def generate_prediction_bundle(paper: PaperRecord) -> PredictionBundle:
    ...
```

保存到：

```text
data/koala_cache/predictions/{paper_id}.json
```

---

# 15. Koala platform client

实现 `platform/koala_client.py`，但不要硬编码不确定的 API schema。Codex 应先读取 starter repo 和 live skill guide 接口，然后封装：

```python
class KoalaClient:
    def get_unread_count(self) -> int: ...
    def get_notifications(self) -> list[dict]: ...
    def mark_notifications_read(self, ids: list[str]) -> None: ...

    def list_papers(self, status: str | None = None) -> list[PaperRecord]: ...
    def get_paper(self, paper_id: str) -> PaperRecord: ...
    def get_comments(self, paper_id: str) -> list[CommentRecord]: ...

    def post_comment(
        self,
        paper_id: str,
        content_markdown: str,
        github_file_url: str,
        parent_id: str | None = None,
    ) -> str: ...

    def submit_verdict(
        self,
        paper_id: str,
        score: float,
        verdict_markdown: str,
    ) -> str: ...

    def get_agent_profile(self) -> dict: ...
    def update_agent_profile(self, description: str) -> None: ...
```

要求：

```text
- 所有网络请求都有 retry 和 timeout。
- 所有失败写入 logs/errors.jsonl。
- post_comment 前必须 dry-run validate。
- submit_verdict 前必须检查：
  - paper status == deliberating
  - agent 在 in_review 发过评论
  - verdict body 包含 ≥3 个合法外部 comment refs
  - score in [0, 10]
```

---

# 16. GitHub reasoning file 自动化

实现 `agent/github_publisher.py`。

平台规则要求评论带 reasoning file URL，并建议每篇 paper 使用专门 branch；不要把生成的 reasoning 文件推到 main。([GitHub][2])

函数：

```python
def write_reasoning_file(
    agent_name: str,
    paper_id: str,
    action_type: Literal["comment", "reply", "verdict"],
    content_markdown: str,
    evidence: dict,
) -> Path:
    ...

def publish_reasoning_file(
    agent_name: str,
    paper_id: str,
    file_path: Path,
) -> str:
    ...
```

branch 命名：

```text
agent-reasoning/{agent_name}/{paper_id}
```

文件路径：

```text
reasoning/{agent_name}/{paper_id}/{timestamp}_{action_type}.md
```

reasoning file 内容模板：

```markdown
# Reasoning file

Agent: {agent_name}
Paper ID: {paper_id}
Action: {comment|reply|verdict}
Timestamp: {iso_timestamp}

## Prediction summary

{prediction_bundle_summary}

## Evidence from paper

- Section/table/equation references
- Positive signals
- Negative signals

## Discussion evidence, if applicable

{discussion_summary}

## Action content

{comment_or_verdict_markdown}

## Policy checks

- Did not use future leaked same-paper information.
- Did not cite self.
- Did not cite same-owner agents.
- URL verified before posting.
```

URL 验证：

```python
def verify_github_url(url: str) -> bool:
    # HTTP GET, require 200
```

测试：

```text
- branch name generated correctly
- URL verifier rejects 404
- reasoning file contains action content hash
```

---

# 17. Paper selection policy

实现 `agent/paper_selector.py`。

目标：不要盲目覆盖最多 paper，而是选最可能产生有效 verdict、且能写高质量评论的 paper。

函数：

```python
def compute_paper_utility(
    paper: PaperRecord,
    prediction: PredictionBundle | None,
    agent_name: str,
    agent_focus: str,
    current_karma: float,
) -> float:
    ...
```

推荐 utility：

```python
utility = (
    1.8 * predicted_metric_gain
    + 1.0 * coverage_reward
    + 0.8 * under_reviewed_bonus
    + 0.6 * citation_probability
    - 0.7 * uncertainty_penalty
    - 0.4 * time_cost
    - 2.0 * cannot_get_3_citations_risk
)
```

子项：

```text
predicted_metric_gain:
  high when p_accept very high or very low and uncertainty low

coverage_reward:
  positive if this agent has not reviewed too many papers in same domain

under_reviewed_bonus:
  best when participant_count between 2 and 9
  negative when participant_count > 12

citation_probability:
  high if agent can make specific comment matching its focus

uncertainty_penalty:
  high when model disagreement / panel disagreement high

cannot_get_3_citations_risk:
  high when paper is near end of in_review and fewer than 3 other agents likely
```

Hard filters：

```python
def should_comment(...):
    reject if paper.status != "in_review"
    reject if already_commented_by_agent
    reject if current_karma < 1.0
    reject if paper age > 44h
    reject if paper age < 4h and participant_count < 1
    reject if comment_count > 18
    reject if assigned_agent_for_paper != agent_name
    reject if no substantive evidence can be generated
```

Sharding：

```python
def assigned_agent(paper_id: str, agents: list[str]) -> str:
    return agents[stable_hash(paper_id) % len(agents)]
```

---

# 18. Comment writer

实现 `agent/comment_writer.py`。

输入：

```text
- paper
- prediction_bundle
- parsed paper
- agent persona/focus
```

输出：

```text
- content_markdown
- evidence dict for reasoning file
```

要求：

```text
- 250–450 words preferred
- 必须 decision-relevant
- 必须包含具体 section/table/equation/page 证据，如果 parser 能找到
- 避免复述摘要
- 避免泛泛而谈
- 不要直接暴露完整内部模型细节，例如 “LightGBM says...”
- 可以说 “my current decision-relevant leaning is...”
```

通用评论模板：

```markdown
**Decision-relevant evidence check**

My reading is that the paper's main claim is: {main_claim}.

**Strongest acceptance signal.**
{specific_positive_evidence}. This matters because {why_decision_relevant}.

**Main rejection risk.**
{specific_negative_evidence}. I think this is decision-relevant because {why_it_could_affect_acceptance}.

**Evidence quality.**
The current evidence supports {limited_claim}, but does not fully establish {stronger_claim}, because {missing_check_or_limitation}.

**Current leaning.**
{weak reject / borderline / weak accept}, with {low/medium/high} confidence. The evidence that would most change my view is {specific_missing_check}.
```

Agent-specific style：

```text
calibrated_decider:
  balanced, score-calibrated, mentions novelty/soundness/evidence balance

rigor_auditor:
  focuses on baselines, ablations, statistical reporting, code/method alignment

novelty_scout:
  focuses on novelty claim, related work, missing comparisons, claim grounding
```

---

# 19. Discussion analyzer

实现 `discussion/discussion_features.py` 和 `discussion/claim_extractor.py`。

输入：

```text
Koala comments for one paper
```

输出：

```text
DiscussionFeatureBundle
```

每条评论抽取：

```json
{
  "comment_id": "c123",
  "author_agent": "agent_x",
  "claim_types": [
    "novelty_support",
    "missing_baseline",
    "fatal_flaw",
    "reproducibility_concern"
  ],
  "polarity": "positive|negative|mixed|neutral",
  "severity": 0-4,
  "specificity": 0-1,
  "evidence_references": ["Table 2", "Section 4.1"],
  "quality_score": 0-1,
  "summary": "..."
}
```

Aggregate features：

```text
positive_claim_count
negative_claim_count
verified_fatal_flaw_signal
independent_positive_signal
reproducibility_concern_signal
novelty_prior_art_signal
missing_baseline_signal
theory_soundness_signal
clarity_signal
comment_quality_mean
comment_quality_max
num_distinct_external_agents
num_citable_comments
```

质量评分：

```python
quality_score = (
    0.35 * specificity
    + 0.25 * evidence_reference_presence
    + 0.20 * decision_relevance
    + 0.10 * clarity
    + 0.10 * independence
)
```

---

# 20. Citation selector

实现 `discussion/citation_selector.py`。

必须保证：

```text
- 至少 3 个不同其他 agent
- 不引用自己
- 不引用同 owner / same team agents
- 优先高质量、高具体性、高决策相关评论
- 尽量覆盖正反两类 evidence
```

函数：

```python
def select_citations(
    comments: list[CommentRecord],
    current_agent_name: str,
    same_owner_agent_names: set[str],
    min_citations: int = 3,
    max_citations: int = 5,
) -> list[CommentRecord]:
    ...
```

排序：

```python
score = (
    1.2 * quality_score
    + 0.8 * decision_relevance
    + 0.4 * evidence_specificity
    + 0.3 * diversity_bonus
    - 1.0 * redundancy_penalty
)
```

测试：

```text
- rejects own comments
- rejects same-owner comments
- returns at least 3 distinct authors if available
- returns failure if not enough valid comments
- includes both positive and negative comments when possible
```

---

# 21. Verdict writer

实现 `agent/verdict_writer.py`。

输入：

```text
- paper
- prediction_bundle
- discussion_features
- citation_plan
```

输出：

```text
- score: float
- verdict_markdown
```

流程：

```python
def prepare_verdict(...):
    assert paper.status == "deliberating"
    assert agent_commented_in_review
    assert len(valid_external_citations) >= 3

    p_final = model_c_update(...)
    score = map_to_score(...)
    verdict = write_verdict_markdown(...)
    validate_verdict(verdict, score, citations)
    return score, verdict
```

模板：

```markdown
**Verdict: {score:.1f}/10 — {band}**

I assign this score because the paper appears to be {below / near / above} the likely ICML acceptance threshold after weighing novelty, technical soundness, and evidence quality.

**Positive evidence.**
{positive_summary}. This is supported by [[comment:{comment_id_1}]] and [[comment:{comment_id_2}]].

**Negative evidence / risks.**
{negative_summary}. The most decision-relevant concern is [[comment:{comment_id_3}]], especially because {reason}. {optional_extra_citation}

**Calibration.**
Relative to papers likely to clear an ICML accept threshold, I view this as {calibration_position}. The score is not higher because {specific_reason_not_higher}, and not lower because {specific_reason_not_lower}.

**Final score:** {score:.1f}
```

Validation：

```python
def validate_verdict_body(body: str, citations: list[CommentRecord], agent_name: str) -> None:
    - body contains [[comment:id]] for each citation
    - at least 3 distinct external authors
    - no self citation
    - no same-owner citation
    - score float in [0, 10]
```

---

# 22. Agent lifecycle scheduler

实现 `agent/scheduler.py`。

每个 agent 独立运行，但共享 DB 和 model artifacts。

主循环：

```python
def run_agent(agent_name: str, dry_run: bool = False):
    load_config()
    load_models()
    client = KoalaClient(agent_name)

    while True:
        sync_notifications(client, agent_name)
        sync_papers(client)
        sync_comments_for_active_papers(client, agent_name)

        handle_in_review_papers(client, agent_name)
        handle_deliberating_papers(client, agent_name)

        write_heartbeat(agent_name)
        sleep_with_jitter()
```

`handle_in_review_papers`：

```python
for paper in candidate_in_review_papers:
    if lifecycle_policy.can_comment(...):
        prediction = get_or_create_prediction_bundle(paper)
        if paper_selector.should_comment(paper, prediction, agent_name):
            comment, evidence = comment_writer.write(...)
            reasoning_path = reasoning_writer.write(...)
            url = github_publisher.publish_and_verify(...)
            client.post_comment(...)
            record_action_success(...)
```

`handle_deliberating_papers`：

```python
for paper in active_deliberating_papers:
    if lifecycle_policy.can_submit_verdict(...):
        comments = client.get_comments(paper.paper_id)
        discussion_features = discussion_analyzer.extract(comments)
        prediction = update_prediction_with_discussion(...)
        citations = citation_selector.select(...)
        if len(citations) >= 3:
            score, verdict = verdict_writer.write(...)
            client.submit_verdict(...)
            record_verdict_success(...)
        else:
            record_blocked_due_to_insufficient_citations(...)
```

Notifications：

```text
- REPLY: read and possibly reply if useful
- COMMENT_ON_PAPER: update discussion cache
- PAPER_DELIBERATING: prioritize verdict
- PAPER_REVIEWED: log final public verdicts if available
```

Starter rules explicitly tell agents to check unread notifications at session start, get notifications, respond where appropriate, and mark notifications read.([GitHub][2])

---

# 23. Agent prompts

Create three agent configs using starter CLI:

```bash
uv run reva create --name calibrated_decider
uv run reva create --name rigor_auditor
uv run reva create --name novelty_scout
```

Starter repo quickstart says to fork first, set `github_repo` in `config.toml`, create agent config, edit `system_prompt.md`, place API key in `.api_key`, then launch with `uv run reva launch --name foo`.([GitHub][3])

## 23.1 Shared prompt block

Put this into all three `system_prompt.md` files:

```markdown
You are a Koala Science peer-review agent. Your objective is to produce calibrated verdicts that predict the real ICML 2026 accept/reject outcome.

You are not a generic reviewer. You use an external ICLR26-trained prediction system as a calibrated prior, then update based on the paper and discussion.

Before commenting:
1. Read the paper.
2. Read or generate prediction_bundle.json.
3. Check p_accept_paper_only, uncertainty, domain_calibrated_percentile, and recommended score range.
4. Identify 1–3 decision-relevant pieces of evidence from the paper.
5. Comment only if you can contribute a specific, verifiable observation.

Before submitting a verdict:
1. Confirm you commented during the in_review phase.
2. Confirm the paper is in deliberating phase.
3. Read the current discussion.
4. Extract discussion signals: independent positive support, fatal flaw claims, reproducibility concerns, novelty/prior-art concerns, evidence quality.
5. Read verdict_bundle.json.
6. Cite at least 3 distinct comments from other agents.
7. Do not cite yourself or agents owned by the same OpenReview ID/team.
8. Submit a calibrated score, not a popularity-weighted average of the discussion.

Model use:
- Treat p_accept_paper_only as the prior.
- Treat discussion evidence as an update.
- Override the model only with concrete evidence from the paper or valid external comments.
- If uncertainty is high, be conservative and explain what evidence would change the decision.

Comment style:
- Be concise.
- Be specific.
- Include section/table/equation references when possible.
- Separate facts from judgments.
- Make the comment useful for later verdicts.
```

## 23.2 calibrated_decider focus

```markdown
Your reviewing focus is calibrated decision quality.

Prioritize:
- novelty
- technical soundness
- significance
- whether evidence is sufficient for an ICML accept threshold
- score calibration

Your comments should be balanced and decision-relevant.
```

## 23.3 rigor_auditor focus

```markdown
Your reviewing focus is experimental rigor and reproducibility.

Prioritize:
- baselines
- ablations
- statistical reporting
- dataset/metric appropriateness
- code or artifact alignment when available
- whether claims are supported by experiments

Your comments should be highly citable because they point to concrete evidence gaps.
```

## 23.4 novelty_scout focus

```markdown
Your reviewing focus is novelty, literature grounding, and claim verification.

Prioritize:
- whether the central contribution is genuinely new
- missing or misrepresented prior work
- whether the paper overclaims
- whether references support the claimed positioning
- whether the contribution is incremental or substantial

Your comments should distinguish clearly between novelty support and novelty risk.
```

---

# 24. CLI commands

Implement `koala_strategy/cli.py` with Typer or argparse.

Commands:

```bash
python -m koala_strategy.cli build-iclr-dataset
python -m koala_strategy.cli run-pseudo-reviews --limit 100
python -m koala_strategy.cli train-model-a
python -m koala_strategy.cli train-model-b
python -m koala_strategy.cli train-model-c
python -m koala_strategy.cli train-all
python -m koala_strategy.cli score-paper --paper-id PAPER_ID
python -m koala_strategy.cli dry-run-one-paper --paper-id PAPER_ID --agent calibrated_decider
python -m koala_strategy.cli run-agent --agent calibrated_decider
python -m koala_strategy.cli run-all-agents
python -m koala_strategy.cli dashboard
```

`scripts/train_all.py` should run:

```text
1. build dataset
2. parse papers
3. extract structured features
4. run pseudo reviews, using cache
5. train Model A
6. train Model B
7. train stacker/calibrator
8. train Model C
9. export model bundle
10. write metrics report
```

---

# 25. Logging and trajectory logs

Because winners must provide full trajectory logs covering every interaction, implement complete JSONL logs. The competition page explicitly says winners must provide full agent trajectory logs and explain strategy/implementation.([Koala Science][1])

Files:

```text
data/logs/actions.jsonl
data/logs/model_predictions.jsonl
data/logs/comments.jsonl
data/logs/verdicts.jsonl
data/logs/errors.jsonl
data/logs/notifications.jsonl
data/logs/heartbeats.jsonl
```

Every action log should include:

```json
{
  "timestamp": "...",
  "agent_name": "rigor_auditor",
  "paper_id": "...",
  "action_type": "post_comment",
  "dry_run": false,
  "input_summary": "...",
  "prediction_bundle_path": "...",
  "reasoning_file_url": "...",
  "content_hash": "...",
  "result": "success|failure",
  "error": null
}
```

---

# 26. Dashboard

Implement lightweight dashboard in `scripts/dashboard.py`.

Print:

```text
Agent status:
- karma remaining
- comments posted
- verdicts submitted
- active papers
- papers blocked by insufficient citations
- unread notifications
- error count
- strike count if accessible

Prediction distribution:
- mean p_accept
- score histogram
- uncertainty histogram
- domain distribution

Deadline queue:
- deliberating papers sorted by deadline
- papers with <3 valid citations
- papers needing verdict soon
```

No need for fancy UI. Terminal table is fine.

---

# 27. Tests

Implement unit tests before full online launch.

Minimum tests:

```text
test_score_mapping.py
- percentile bands correct
- uncertainty shrinkage works
- score always in [0, 10]

test_contamination_guard.py
- exact duplicate caught
- near duplicate caught
- unrelated not caught

test_json_guard.py
- parses valid JSON
- repairs fenced JSON
- fallback on invalid JSON

test_citation_selector.py
- no self citation
- no same-owner citation
- at least 3 distinct authors
- quality sorting works
- fails safely if insufficient citations

test_lifecycle_policy.py
- cannot comment outside in_review
- cannot verdict unless deliberating
- cannot verdict without prior comment
- cannot verdict without 3 valid citations

test_github_publisher.py
- branch naming correct
- URL verifier rejects 404
- reasoning file contains evidence and action content

test_paper_selector.py
- rejects over-commented paper
- rejects wrong shard
- prioritizes under-reviewed but viable paper

test_end_to_end_dry_run.py
- mock paper → prediction bundle → comment → reasoning file → mock post
- mock deliberating paper → discussion features → citations → verdict
```

---

# 28. MVP 优先级

Codex should implement in this order.

## MVP 1：先跑通 agent 与平台闭环

```text
1. Add config, schemas, DB.
2. Add KoalaClient wrapper using starter repo / skill guide.
3. Add reasoning_writer + github_publisher.
4. Add dry-run mode.
5. Create 3 agent configs and prompts.
6. Implement simple paper_selector.
7. Implement simple comment_writer using LLM and no trained model.
8. Run dry-run on one paper.
9. Run real comment on one safe low-risk paper.
```

Acceptance criteria:

```text
- `python -m koala_strategy.cli dry-run-one-paper --paper-id X --agent calibrated_decider` works.
- Reasoning file is created.
- GitHub URL can be verified.
- No duplicate action if command reruns.
```

## MVP 2：ICLR26 pseudo-review model

```text
1. Load ICLR26 dataset.
2. Run 5 pseudo-review personas per training paper, cached.
3. Aggregate pseudo-review features.
4. Train Model B classifier.
5. Calibrate probability.
6. Export model.
7. Score one Koala paper and generate prediction_bundle.
```

Acceptance criteria:

```text
- `python -m koala_strategy.cli train-model-b` produces model_b.pkl and metrics.json.
- `score-paper` outputs p_accept, uncertainty, recommended score range.
```

## MVP 3：Model A + stacker

```text
1. Extract structured features.
2. Extract embeddings.
3. Train Model A.
4. Stack Model A + Model B.
5. Calibrate paper-only p_accept.
```

Acceptance criteria:

```text
- metrics show AUROC/logloss/Brier.
- prediction_bundle includes paper_only and pseudo_review_panel.
```

## MVP 4：discussion-aware verdict

```text
1. Extract features from ICLR official reviews.
2. Train Model C.
3. Extract features from Koala comments.
4. Implement citation_selector.
5. Implement verdict_writer.
6. Submit dry-run verdict.
```

Acceptance criteria:

```text
- mock deliberating paper with 4 external comments produces valid verdict.
- verdict contains ≥3 `[[comment:id]]` refs.
- validation rejects own/same-owner citations.
```

## MVP 5：full scheduler

```text
1. Notification sync.
2. Paper sync.
3. In-review comment loop.
4. Deliberating verdict loop.
5. Dashboard.
6. JSONL trajectory logs.
```

Acceptance criteria:

```text
- `run-agent` can loop continuously.
- `dashboard` shows active state.
- action logs are complete.
```

---

# 29. Hard safety / rule checks

Implement these as code-level guardrails, not just prompt instructions.

Before comment:

```python
assert paper.status == "in_review"
assert karma_remaining >= 1.0 or already_commented_on_paper
assert github_file_url_verified
assert content_hash_not_previously_posted
assert no_forbidden_source_used
```

Before verdict:

```python
assert paper.status == "deliberating"
assert agent_commented_during_in_review
assert len(valid_external_citations) >= 3
assert no_self_citation
assert no_same_owner_citation
assert 0.0 <= score <= 10.0
assert verdict_not_already_submitted
```

Before using ICLR retrieval evidence:

```python
assert not contamination_guard.is_near_duplicate(koala_paper, retrieved_iclr_paper)
```

---

# 30. Final Codex deliverables

Codex should produce:

```text
1. Working Python package `koala_strategy`.
2. Three configured agent prompts.
3. Training scripts for Model A/B/C.
4. Online inference scripts.
5. Agent scheduler.
6. Reasoning file GitHub publisher.
7. SQLite cache.
8. JSONL trajectory logging.
9. Tests.
10. README section explaining:
   - how to train
   - how to run dry-run
   - how to run each agent
   - how to inspect dashboard
   - how to avoid leakage
```

README commands:

```bash
uv sync
source .venv/bin/activate

python -m koala_strategy.cli build-iclr-dataset
python -m koala_strategy.cli train-all

python -m koala_strategy.cli dry-run-one-paper --paper-id PAPER_ID --agent calibrated_decider

uv run reva create --name calibrated_decider
uv run reva create --name rigor_auditor
uv run reva create --name novelty_scout

uv run reva launch --name calibrated_decider
uv run reva launch --name rigor_auditor
uv run reva launch --name novelty_scout

python -m koala_strategy.cli dashboard
```

---

# 31. 最终实现原则

Codex should keep these principles:

```text
1. Build the predictor first; do not rely on prompt intuition alone.
2. Treat ICLR26 as training data, not as a lookup table.
3. Never use future/leaked same-paper information.
4. Make comments short, specific, and citable.
5. Submit verdicts only when citation constraints are satisfied.
6. Use deterministic sharding so same-owner agents do not crowd the same paper.
7. Log everything.
8. Prefer dry-run validation before real platform actions.
9. Do not overfit to interaction karma; optimize calibrated accept/reject prediction.
```

The intended final system is:

```text
ICLR26-calibrated shared backend
  + three specialized autonomous Koala agents
  + contamination-safe inference
  + high-signal comments
  + discussion-aware calibrated verdicts
  + complete trajectory logs
```

[1]: https://koala.science/competition "Competition — Koala Science"
[2]: https://raw.githubusercontent.com/koala-science/peer-review-agents/main/agent_definition/GLOBAL_RULES.md "raw.githubusercontent.com"
[3]: https://raw.githubusercontent.com/koala-science/peer-review-agents/main/README.md "raw.githubusercontent.com"
