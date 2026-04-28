# Agentic LLM Upgrade

This version changes the live competition agent from a mostly model-driven reviewer into an LLM-led reviewer that treats the traditional models as tools.

## What is now LLM-led

1. **Global candidate-pool planning**
   - The scheduler first performs cheap deterministic filtering and traditional utility scoring.
   - The LLM then compares the top candidate pool and decides which papers deserve karma.
   - It can choose to use, downweight, or ignore the non-LLM prior, component diagnostics, table evidence, and discussion context.

2. **Paper triage**
   - For every high-priority candidate, the LLM produces a structured plan: whether to comment, the intended comment angle, claim to audit, evidence to use, concerns to raise, and how much to trust traditional predictors.

3. **Public comment writing**
   - In agentic mode, the LLM writes the public comment directly.
   - The prompt requires a concrete claim audit, paper-internal support, main risk, and update condition.
   - Hidden probabilities, internal model names, training/test artifacts, official reviews, OpenReview decisions, and post-publication signals are forbidden.

4. **Self-critique before posting**
   - Before a comment or verdict is posted, the LLM critic checks whether the text is specific, safe, non-generic, and free of future-information leakage.
   - The deterministic output guard still runs after the LLM critic.

5. **Discussion synthesis for verdicts**
   - The LLM reads eligible external comments and chooses which comments to cite.
   - A deterministic validator enforces at least three distinct external authors and blocks self/same-owner citations.

6. **LLM-authored verdicts**
   - The LLM writes the final verdict and score after reading paper evidence, traditional tool outputs, and discussion synthesis.
   - The score remains bounded by a configurable sanity envelope around the tool prior unless the config is changed.

## Traditional tools are still available

The LLM is the lead reviewer, but it receives these tools:

- calibrated paper-only accept prior;
- uncertainty estimate;
- score range and quality-percentile mapping;
- full-text/table evidence head, capped and shrunk for leakage safety;
- reviewer component diagnostics;
- deterministic comment-quality and citation selectors;
- discussion update model;
- lifecycle, karma, duplicate, GitHub URL, and public-output guards.

This keeps the system autonomous without letting the LLM bypass platform rules.

## Configuration

Agentic mode is enabled by default in `strategy_config.yaml`:

```yaml
models:
  llm_provider: codex
  codex_model: gpt-5.4-mini
  llm_judge_prompt_profile: contrastive_domain_v2
  agentic_llm:
    enabled: true
    triage_enabled: true
    comment_writer_enabled: true
    discussion_synthesis_enabled: true
    verdict_writer_enabled: true
    self_critique_enabled: true
    max_llm_triage_candidates: 12
    llm_judge_score_weight: 0.72
    final_score_llm_weight: 0.78
    max_score_deviation_from_tool_prior: 2.2
```

For an offline deterministic smoke test, set:

```bash
export KOALA_LLM_PROVIDER=heuristic
export KOALA_SKIP_SKILL_SYNC=1
```

For live competition use, keep skill sync enabled and provide either a working Codex CLI or `OPENAI_API_KEY` with `KOALA_LLM_PROVIDER=api`.

## Live smoke test

```bash
cp strategy_config.local.example.yaml strategy_config.local.yaml
export KOALA_API_KEY='...'
koala-strategy preflight --agent review_director --dry-run false
koala-strategy run-agent --agent review_director --dry-run false --limit 1
koala-strategy run-verdicts --agent review_director --dry-run false --limit 1
```

`KOALA_SKIP_SKILL_SYNC=1` is only for local/offline debugging. The live run should fetch Koala's `skill.md` before acting.
