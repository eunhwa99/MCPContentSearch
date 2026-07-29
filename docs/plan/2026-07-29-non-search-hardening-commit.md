# Non-search hardening selective commit

## Goal

Commit the completed runtime, demo, configuration, and documentation
hardening while keeping retrieval-quality evaluation and real-embedding proof
work uncommitted for a later task.

## Included

- deterministic SQLite connection cleanup and focused regression coverage
- credential-free local demo wording and focused tests
- concise project and usage documentation
- configuration guidance
- small lint, typing, container metadata, and dead-import cleanup

## Excluded

- lexical retrieval and answer-quality evaluator changes
- semantic benchmark datasets, runners, reports, and tests
- Codex LLM ranker implementation, runner, schema, and tests
- CI and verification-wrapper changes that only support those evaluation lanes

## Verification

- inspect the staged file list and staged diff
- run `git diff --cached --check`
- run focused demo and metadata-store tests
- run the retained functional E2E gate
- confirm excluded evaluation work remains unstaged after the commit

## Progress

- Branch safety: continuing the existing isolated
  `feature/ai-portfolio-hardening` worktree based on `origin/main`.
- Implementation: existing changes are being selectively staged; no target
  behavior is being newly implemented in this commit step.
- Focused verification:
  `uv run --locked pytest -q tests/storage/test_metadata_store.py tests/scripts/test_demo_public_flow.py`
  passed with 73 tests.
- Functional smoke: `./scripts/verify_functional_e2e.sh` passed with 25 tests
  using temporary/local test data.
- Scope amendment: removed `SECURITY.md` and its README link at the user's
  request; the file remains outside the commit.
