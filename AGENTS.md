# AGENTS.md

## CRITICAL: Ask Questions, Don't Assume

**When implementing anything, ask the user about ambiguous decisions instead of making silent assumptions.** The user cannot predict what choices you'll face during implementation. A quick question is always cheaper than redoing work.

Examples of things to ask about: data format choices, model selection, parameter values, architectural tradeoffs, naming conventions, and edge case handling. Err heavily on the side of asking.

## CRITICAL: Test-Driven Development

**ALWAYS write tests BEFORE implementing any new feature or algorithm. DO NOT ASK - JUST DO IT.**

1. Write test cases that define the expected behavior.
2. Run the tests to confirm they fail.
3. Implement the feature.
4. Run the tests to confirm they pass.
5. Refactor if needed.

## Spec-Driven Development for Non-Trivial Tasks

**For any task that touches multiple files, involves architectural decisions, or has non-obvious requirements, use the spec-driven workflow. Do NOT ask - recognize when it is needed and initiate it.**

**When to trigger:** Multi-file changes, new features, refactors, anything where "just start coding" would risk wasted effort or misalignment.

**When NOT to trigger:** Single-file bug fixes, typos, config tweaks, simple one-function additions with clear requirements.

### Workflow

1. Announce that this task warrants a spec. Briefly explain why.
2. Explore the codebase to understand current state.
3. Draft the spec to `.Codex/specs/{descriptive-name}.md`.
4. Drill acceptance criteria with the user. Do not finalize the spec until the user has explicitly approved the acceptance criteria.
5. Delegate implementation only after the spec is approved.
6. Verify the work: run tests, linter, type checker, and any task-specific smoke checks.
7. Commit only after successful verification.

### Spec Template

```markdown
# Spec: {title}

## Goal
One sentence: what does this accomplish and why.

## Context
- Relevant files and their roles
- Current behavior
- How this fits into the broader system

## Requirements
Numbered list of concrete changes. Each requirement should be independently verifiable.

## Constraints
- What must NOT change
- Performance/memory bounds
- Compatibility requirements
- Edge cases to handle

## Test Plan
- What tests to write
- What to mock
- Key assertions

## Acceptance Criteria
- [ ] Observable, specific, complete criterion
```

## Self-Verification

**Always verify your own work.** After making changes:

1. Run relevant tests.
2. Run linter / type checker if configured.
3. If tests or type checks fail, fix the root cause.

**Never dismiss failures as pre-existing.** If a test failure, type error, lint error, or other issue appears, diagnose and fix it before declaring work done.

**Never skip or ignore failing tests.** If a test is obsolete, delete or update it and explain why.

## Self-Improvement: Updating AGENTS.md

When you encounter a repeated error, a surprising project convention, or a non-obvious gotcha that could have been avoided with better instructions, suggest adding a rule to `AGENTS.md`.

Phrase suggestions as: "I hit [problem]. Should I add a rule to AGENTS.md to prevent this in the future?"

## Committing Code

Use `/commit` to commit changes. This runs pre-commit checks, a code review, and creates the commit automatically.

**Never manually `git commit` to bypass the review step.**

## Code Style

- Avoid inline comments. Code should be self-explanatory through clear naming and structure.
- Docstrings for public functions/classes are fine.
- Only add inline comments when the logic is truly non-obvious.
- Do not write defensive compatibility code unless the value can genuinely be absent in normal operation.
- Use type annotations on public APIs.
- Keep changes surgical. Do not refactor adjacent code unless required by the task.

## CRITICAL: Never Stack Long-Running Processes

**One long-running command at a time, verified to exit before starting another.**

When a command might take more than about 30 seconds:

1. Run it in the background with explicit log redirection, for example `> /tmp/<name>.log 2>&1`.
2. Before starting another run of the same tool, verify the previous one exited by checking the log or process table.
3. When a foreground command times out or auto-backgrounds, stop and inspect it instead of retrying.
4. Cap parallelism for heavy runners.
5. Kill idle dev servers before running full test suites if the machine feels slow.

## Project Structure

- Main package: `koala_strategy/`.
- CLI entrypoint: `koala_strategy/cli.py`.
- Training and evaluation code: `koala_strategy/models/`.
- Discussion and comment feature extraction: `koala_strategy/discussion/`.
- Agent orchestration: `koala_strategy/agent/`.
- LLM helpers and prompts: `koala_strategy/llm/`.
- Paper parsing and evidence extraction: `koala_strategy/paper/`.
- Agent configuration prompts: `agent_configs/`.
- Wrapper scripts: `scripts/`.
- Tests: `tests/`.
- Trained model artifacts live under `models/`.
- Data, PDF caches, Koala datasets, and logs are not Git-tracked and must not be committed.

## Environment Policy

- Local workstation is for code edits, lightweight unit tests, static inspection, and packaging checks.
- **All experiments must run on the Mila cluster because the datasets, Koala ICLR data, parsed PDF cache, and large artifacts live on Mila.**
- Do not run training, PDF parsing, benchmark evaluation, LLM judge sweeps, or model-selection experiments locally unless the user explicitly asks for a local dry run with synthetic data.
- If an experiment command fails locally because `data/` is missing, treat that as expected and move the experiment to Mila rather than creating fake local paths.
- For local Python work, use the project environment when available; otherwise use the active `python` after installing the test extra with `python -m pip install -e '.[test]'`.

## Mila Workflow

- Mila is the default and only environment for smoke, pilot, and formal experiments in this project.
- Initialize the project environment with the shared Utils shell entrypoint: `source /home/mila/j/jianan.zhao/scratch/Utils/shell/init.sh ReviewAgent`. In interactive shells this is usually available as `so ReviewAgent`.
- The project-specific Utils config lives at `configs/user/env.yaml`, but this file is local/user-specific and must not be version-controlled because it may contain tokens or machine-private paths. Keep Mila module loads, scratch cache roots, and ReviewAgent convenience aliases there locally.
- Before running on Mila, verify:
  - the remote repo checkout path,
  - the active Python environment,
  - the expected `data/koala_iclr2026` files,
  - the parsed PDF cache location,
  - the model output directory.
- Do not assume repo-relative non-Git assets such as `data/`, PDF caches, or local virtualenvs are populated in a clean worktree.
- Keep caches and temp directories under scratch-backed paths, not under `~/`.
- Export scratch-safe cache variables before jobs:
  - `XDG_CACHE_HOME`
  - `TRITON_CACHE_DIR`
  - `TMPDIR`
  - `HF_HOME`
  - `HUGGINGFACE_HUB_CACHE`
  - `TRANSFORMERS_CACHE`
  - `HF_DATASETS_CACHE`
  - `TORCH_EXTENSIONS_DIR`
  - `WANDB_CACHE_DIR`
- For Mila debug/smoke runs, default to an interactive `unkillable` `salloc` allocation because it starts quickly and shortens iteration time.
- Do not use Mila `unkillable` or `short-unkillable` partitions for pilot/formal runs unless the user explicitly requests them.
- Default batch intent is the long queue. If a non-unkillable request is delayed or rejected, report the scheduler reason before trying another partition.
- Existing suitable allocations may be reused, but release incompatible interactive allocations before requesting new ones.

## Experiment Workflow

- Definitions:
  - `smoke`: minimum-time pipeline check.
  - `pilot`: shorter formal-like runtime / resource probe.
  - `formal`: main run.
- Before a pilot or formal run, inspect related run history and state expected runtime plus ETA.
- If expected runtime exceeds 1 hour, discuss command changes first.
- If the user still wants a >1 hour formal run, submit via Slurm instead of keeping it attached to the session.
- After batch submission, monitor for up to 5 minutes. If it fails immediately, inspect the script and first log before resubmitting.
- Keep transient Slurm stdout/stderr logs out of committed paths.
- For benchmark work, preserve the distinction between:
  - train OOF metrics,
  - held-out test metrics,
  - small diagnostic subset metrics,
  - production online behavior.
- Do not report proxy-discussion metrics, repeated-test-selected metrics, or small diagnostic subset metrics as unbiased held-out performance.

## Model and Leakage Rules

- Paper-only models may use only public paper-visible fields available at prediction time.
- Do not use official reviews, meta-reviews, decisions, acceptance status, OpenReview status, camera-ready text, post-publication signals, social media, citation counts, or awards as model inputs.
- Model C may use official reviews only as an offline proxy-discussion training signal; do not present it as deployable Koala discussion performance.
- Full-text/PDF models must scrub version, status, author identity, acknowledgement, DOI/page-marker/parser artifacts, and other PDF extraction artifacts before training or evaluation.
- If a model reaches implausibly high AUROC, inspect top features and artifact correlations before trusting the result.
- Any new leakage mitigation must include tests that lock the scrub/filter behavior.

## Sweep and Remote Debugging Rules

- Use the existing sweep or experiment launcher patterns when present; do not invent a new framework for one run.
- Dry-run or smoke-check a new sweep before submitting a broad run.
- Keep sweep configs reproducible and avoid machine-specific absolute paths.
- If a sweep has already transitioned to `FINISHED`, do not assume resuming it will make deleted failed configs runnable again; create a fresh sweep when needed.
- For remote debugging, reproduce failures on Mila with the smallest command that exercises the same path.
- Return the exact command, log path, W&B URL when relevant, and final status.

## W&B Rules

- For remote debug and pilot runs that support W&B, set W&B online and return the run URL.
- A pilot run is not complete until the W&B URL has been returned to the user.
- Training smoke runs may skip W&B unless the user explicitly wants online tracking.
- When citing a W&B run, include the URL and the provenance fields used for attribution.
- Every experiment that should sync into Linear should carry an `issue_key` in config or command overrides.
- Tags are not sufficient join keys for experiment sync; the concrete issue key must be present.

## Command Conventions

- When sharing commands, use `python`, not a local absolute interpreter path.
- For training commands, use the remote `python` expected by the Mila environment.
- Prefer module entrypoints such as `python -m koala_strategy.cli ...`.
- Do not hardcode local workstation paths into scripts, configs, or documentation.

## Testing Guidelines

- Default local verification:
  - `python -m pytest -q`
- For narrow changes, run the focused test first, then the full test suite.
- Experiment commands that require data must be run on Mila.
- When introducing a new script or CLI path, provide a Mila smoke command.

## Linear-First Tracking

- Linear is the default source of truth for task tracking, experiment tracking, rebuttal tracking, and progress logging.
- Do not create local tracking notes for routine work unless the user explicitly asks for a repo artifact.
- Prefer the narrowest correct Linear issue.
- When mentioning a Linear issue in user-facing responses, use a clickable Markdown hyperlink rather than a bare issue key.

## Security and Configuration

- Store API keys, HuggingFace tokens, W&B keys, and Koala credentials in environment variables or local untracked config.
- Never hardcode secrets.
- Large corpora, parsed PDFs, model caches, and experiment outputs should stay outside Git.
- Use config overrides or symlinks for data access instead of committing data.
