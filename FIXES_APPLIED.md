# Fixes applied for Koala Science competition agent

This branch/package was patched to turn the original offline-heavy prototype into a safer live-run candidate.  The default configuration remains `dry_run: true` and `github.publish_enabled: false` so the agent cannot accidentally post live actions until credentials and publishing are explicitly enabled.

## Major fixes

1. **Single effective runtime configuration**
   - `strategy_config.yaml` is the canonical config.
   - `strategy_config.local.yaml` and environment variables can override it.
   - Legacy `config.toml` is only bridged into missing fields.
   - `validate_runtime_config()` fails fast when real posting is requested without GitHub publishing/repo settings.
   - `effective_config_summary()` is logged before live/dry runs.

2. **Koala platform client hardening**
   - Supports Bearer auth by default, with raw-Authorization fallback on 401.
   - Reads API keys from `KOALA_API_KEY_<AGENT>`, `KOALA_API_KEY`, `.api_key`, `agent_configs/<agent>/.api_key`, or `<agent>/.api_key`.
   - Handles paginated paper lists and multiple likely endpoint shapes for papers, comments, verdicts, profile, and notifications.
   - Adds server-side `has_submitted_verdict()` checks.
   - Fetches and logs live `skill.md` guidance at run/preflight time when reachable.

3. **GitHub reasoning URL and publishing fixes**
   - Reasoning files publish to `agent-reasoning/<agent>/<paper>` branches, not `main`.
   - Supports GitHub `blob` URLs and `raw.githubusercontent.com` URLs consistently.
   - Live mode verifies the reasoning URL returns HTTP 200 before comment/verdict submission.
   - Adds a `preflight` command to validate config, skill sync, profile access, and sample GitHub URL generation.

4. **Scheduler now uses selection policy**
   - Replaced naive `list_papers()[:limit]` processing with candidate scanning, prediction, lifecycle checks, and utility ranking.
   - Respects paper age, comment crowding, participant count, karma, shard/policy gates, and local/server prior comments.
   - Logs paper selection decisions.
   - Live runs ignore prior dry-run actions when deciding whether the agent has truly commented or can submit a verdict.

5. **Notification loop**
   - Run/preflight loops now check unread notification count, fetch unread notifications, log them, extract deliberating-paper hints, and mark notifications read after logging.

6. **Verdict safety and compliance**
   - Checks that the agent commented during the review window before submitting a verdict.
   - Checks local and server-side duplicate verdict state.
   - Requires at least three distinct external citations.
   - Filters self and same-owner citations.
   - Adds minimum comment-quality thresholding for citations.
   - Rewrites verdicts into a more decision-relevant format with explicit external comment references.

7. **Public-output and reasoning-file leakage guards**
   - Adds a final output guard before public comment/verdict posting.
   - Blocks references to OpenReview, official reviews, meta-reviews, camera-ready status, hidden labels, internal model artifacts, post-publication signals, and direct external accept/reject status.
   - Sanitizes reasoning files before publishing by omitting raw full text, page text, private labels, hidden probabilities, and model artifact identifiers.

8. **Comment quality improvements**
   - Replaces template-like “main claim is the title” wording with a reviewer-style evidence check.
   - Comments now emphasize claim checked, concrete paper-internal support, main risk, update conditions, and qualitative confidence.
   - LLM polish prompt now forbids hidden probabilities, internal model names, and future/source leakage.

9. **Full-text model risk mitigation**
   - Caps full-text blending influence with `models.fulltext_blend_alpha_max`.
   - Shrinks extreme full-text probabilities before blending when `models.fulltext_extreme_shrink` is true.
   - The full-text model is now treated as a supporting signal rather than an unbounded final authority.

## How to run safely

Create `strategy_config.local.yaml` for live settings instead of editing secrets into the repo:

```yaml
online_policy:
  dry_run: false
github:
  publish_enabled: true
  repo: https://github.com/<owner>/<repo>
  url_style: blob
platform:
  base_url: https://koala.science
  api_base_url: https://koala.science/api/v1
  auth_scheme: bearer
```

Then export credentials without committing them:

```bash
export KOALA_API_KEY='...'
```

Recommended live smoke-test sequence:

```bash
koala-strategy preflight --agent review_director --dry-run false
koala-strategy run-agent --agent review_director --dry-run false --limit 1
koala-strategy run-verdicts --agent review_director --dry-run false --limit 1
```

## Validation performed in this sandbox

- Syntax-compiled the patched `koala_strategy` package and tests with `/usr/bin/python3 -S -m compileall -q koala_strategy tests`.
- Ran a manual smoke path for GitHub URL generation, public output guard, comment generation, reasoning-file generation, citation selection, and verdict generation. It printed `MANUAL_TESTS_OK`.
- Ran selected pytest modules (`test_output_guard.py`, `test_github_publisher.py`, `test_paper_selector.py`, `test_end_to_end_dry_run.py`) with plugin autoload disabled. The tests printed `.......... [100%]`; however, in this sandbox the pytest process did not return cleanly before the wrapper timeout, so this is not reported as a full clean pytest run.
- Removed generated `__pycache__`/`.pyc` files before packaging.

Live Koala posting, GitHub push, and full integration tests were not executed in this sandbox because no Koala API key or GitHub credentials were available here.


## Additional agentic LLM upgrade

10. **LLM-led planning and reviewing**
    - Added `koala_strategy/llm/agentic_reviewer.py`.
    - The LLM now plans across the candidate pool, performs paper triage, writes the public comment, self-critiques output, synthesizes external discussion, and writes the final verdict.
    - Traditional models are explicitly passed as tools rather than treated as the final authority.

11. **Global candidate-pool planner**
    - `_rank_comment_candidates()` now sends the top candidate pool to the LLM so it can compare papers and decide where karma should be spent.
    - Per-paper LLM triage remains as a fallback if the pool planner is unavailable.

12. **Agentic verdict synthesis**
    - Verdicts now use LLM discussion synthesis to select citations and score movement.
    - Deterministic validators still enforce distinct external citations, no self/same-owner citations, score bounds, GitHub reasoning URL checks, and public output leakage checks.

13. **Runtime import cleanup**
    - Added a lightweight parsed-payload accessor to avoid importing pandas/sklearn-heavy full-text training code during live scheduling.
    - Moved several heavy imports to function scope so the live agent can start faster.

14. **Offline debug controls**
    - Added `KOALA_SKIP_SKILL_SYNC=1` for local/offline testing only. Live runs should leave skill sync enabled, per Koala rules.
