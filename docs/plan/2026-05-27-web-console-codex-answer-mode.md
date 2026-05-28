# Web Console Codex Answer Mode

## User Request

Add a Web Console answer mode that can call the local Codex CLI to produce a
more natural answer instead of returning long source text verbatim.

## Branch Preflight Result

- Worktree: `/private/tmp/MCPContentSearch-phase-c5`
- Branch: `feature/contextwiki-web-console-source-sync`
- Status: clean before edits, tracking `origin/feature/contextwiki-web-console-source-sync`
- This is an intentional follow-up on the existing Phase C.5 PR branch.

## Scope and Non-Goals

- Add an experimental local Web Console endpoint that invokes the user's local
  Codex CLI configuration for answer synthesis.
- Keep the existing ContextWiki answer path as the default because it owns
  citations, evidence gating, and stable behavior.
- Retrieve ContextWiki evidence first, then pass a bounded prompt to Codex CLI.
- Return a stable Web Console payload with `answer`, `citations`, `used_chunks`,
  and safe status metadata.
- Add UI mode selection so the user can choose ContextWiki or Codex CLI answer.
- Add mocked tests for subprocess behavior, failures, timeouts, and mode routing.
- Do not change MCP tool contracts.
- Do not make Codex CLI the default answer engine.
- Do not run live Codex CLI in automated tests.
- Do not intentionally send unbounded indexed content to subprocess prompts;
  bound prompt fields, redact obvious secret-looking strings, and document that
  this is not a hard secret-safety boundary.

## Acceptance Criteria

- The Web Console has an Answer mode selector with a default ContextWiki mode and
  an opt-in Codex CLI mode.
- ContextWiki mode keeps the current `/api/answer` behavior.
- Codex CLI mode calls a local HTTP endpoint that retrieves relevant chunks,
  invokes `codex exec` with a bounded prompt, and returns concise answer text
  with the retrieved citations/chunks.
- If Codex CLI is unavailable, times out, or exits non-zero, the endpoint returns
  a safe structured failure without intentionally returning raw command output;
  prompt/citation fields use best-effort redaction, not a hard secret-safety
  guarantee.
- Tests cover success, no evidence, missing CLI, non-zero exit, timeout, and UI
  mode routing where feasible.
- Browser verification exercises both the existing answer mode and the new Codex
  mode safely.

## Step Breakdown

1. Inspect Web Console answer routes, UI JavaScript, and existing context search
   services.
2. Add a small Codex CLI answer service under the Web Console boundary.
3. Wire `/api/answer/codex` into `web_console/app.py`.
4. Add Answer mode UI and route `Answer` button clicks by selected mode.
5. Add README instructions and test coverage.
6. Run focused verification, browser smoke, full verification, and the required
   fresh five-reviewer subagent review pass.

## Files Likely To Change

- `web_console/app.py`
- `web/index.html`
- `web/app.js`
- `web/styles.css`
- `tests/web_console/test_app.py`
- `README.md`
- `docs/plan/2026-05-27-web-console-codex-answer-mode.md`

## Test and Verification Plan

- `node --check web/app.js`
- `PYTHONPATH=. uv run --python 3.13 pytest tests/web_console/test_app.py`
- Focused Phase C.5 suite if needed:
  `PYTHONPATH=. uv run --python 3.13 pytest tests/web_console/test_app.py tests/search/test_answer_service.py tests/search/test_context_service.py`
- Browser verification at `http://127.0.0.1:8765/` for ContextWiki answer mode,
  Codex answer mode failure/success as locally available, and no raw HTML leak.
- Broader verification: `./scripts/verify_all.sh`
- Fresh five-reviewer `$subagent-review-loop` after verification.

## Architecture and ADR Constraints

- ADR 0001: keep the Web Console as a thin HTTP wrapper and avoid changing MCP
  tool contracts for this local developer console feature.
- ADR 0005: sending evidence to an external or model-like synthesizer is an
  opt-in data boundary. Codex CLI mode must remain explicit, bounded, and
  testable without live provider calls. The Web Console endpoint and CLI
  invocation are local, but Codex CLI may still use local Codex authentication or
  external model behavior depending on the user's CLI setup.

## Risks and Rollback Notes

- Codex CLI may be slow, unavailable, or configured differently per machine.
  Timeout and safe fallback are required.
- The prompt includes retrieved user content. Limit chunk count/text size and
  document that this is opt-in local developer tooling.
- CLI output is untrusted. Render it escaped in the UI and preserve citations
  from retrieved ContextWiki evidence instead of trusting CLI-invented citations.
- Rollback is removing the Codex answer endpoint and UI mode; the default
  ContextWiki answer path remains unchanged.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Continued clean existing Phase C.5 PR branch for follow-up work. | `git status --short --branch`; `git branch --show-current`; `git branch -vv`; `git worktree list` |
| Planning | completed | Scoped Codex CLI answer as opt-in Web Console mode with bounded evidence and mocked subprocess tests. | This plan |
| Implementation | completed | Added Codex CLI answer service, `/api/answer/codex`, Answer mode selector, README docs, and mocked tests for success/failure paths. | Local diff |
| Focused verification | completed | JS/Python syntax and focused web-console/search tests passed after security remediation. | `python -m py_compile web_console/app.py tests/web_console/test_app.py`; `node --check web/app.js`; `git diff --check`; `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q` -> 62 passed; `PYTHONPATH=. uv run pytest tests/search/test_answer_service.py tests/search/test_context_service.py -q` -> 16 passed |
| Browser/API verification | completed | Verified Answer mode selector and cache-busted JS in the local Web Console; browser text input was blocked by the in-app virtual clipboard, so the Codex endpoint was verified directly over the same local HTTP path. | Local HTML contains `answerModeSelect` and `Codex CLI Answer`; Korean NeetCode graph query returned safe `codex_status=skipped`/`evidence_status=insufficient`; `ContextWiki source sync citation metadata` returned `codex_status=succeeded`, concise answer, citations, and used chunks. |
| Full verification | completed | Full repository verification passed after Codex answer mode changes. | `./scripts/verify_all.sh` -> 711 passed |
| Review remediation | completed | Addressed review findings: disabled Codex tool-related feature flags, added fail-closed redaction fallback, logged runner failures without details, and added regression coverage for bounded evidence, filtered-out evidence, UI failure status, subprocess isolation args/env, and redaction fallback. | `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q` -> 66 passed; `PYTHONPATH=. uv run pytest tests/search/test_answer_service.py tests/search/test_context_service.py -q` -> 16 passed |
| Full verification after remediation | completed | Full repository verification passed after review remediation. | `./scripts/verify_all.sh` -> 715 passed |
| Isolation wording follow-up | completed | Tightened README wording to avoid hard-isolation claims and confirmed `--ignore-user-config` is part of the Codex CLI invocation. | `codex exec --help` confirmed the flag is available |
| Second review remediation | completed | Addressed follow-up review findings: bounded question/metadata prompt fields and total prompt size, documented weaker working-directory language, asserted runner output is returned, fixed macOS sandbox fallback branch coverage, and made subprocess tests assert `start_new_session=True`. | `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q` -> 70 passed |
| Full verification after second remediation | completed | Full repository verification passed after the second review remediation. | `./scripts/verify_all.sh` -> 719 passed |
| Third review remediation | completed | Addressed final review findings: fallback redaction now covers multiline PEM/private-key blocks, sandbox-unavailable fallback tests assert the same core isolation properties, and timeout tests assert `start_new_session=True`. | `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q` -> 70 passed |
| Full verification after third remediation | completed | Full repository verification passed after the third review remediation. | `./scripts/verify_all.sh` -> 719 passed |
| Fourth review remediation | completed | Addressed final follow-up findings: optional macOS sandbox hardening now fails closed when requested but unavailable, cancellation cleanup terminates the Codex process group, fallback PEM redaction tests include closing markers, subprocess tests assert temp cwd/profile/output existence at spawn time, and plan wording avoids hard secret/offline guarantees. | `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q` -> 71 passed |
| Full verification after fourth remediation | completed | Full repository verification passed after the fourth review remediation. | `./scripts/verify_all.sh` -> 720 passed |
| Fifth review remediation | completed | Addressed final documentation/UI wording findings: README now states Codex CLI may use external model behavior under the user's CLI setup, plan failure wording avoids hard secret guarantees, and empty-question UI copy is route-neutral. | `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q` -> 71 passed |
| Full verification after fifth remediation | completed | Full repository verification passed after the fifth review remediation. | `./scripts/verify_all.sh` -> 720 passed |
| Sixth review remediation | completed | Addressed final test coverage findings: cancellation tests assert temp work/output cleanup and UI validation tests structurally verify shared route-neutral validation before endpoint selection. | `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q` -> 71 passed |
| Full verification after sixth remediation | completed | Full repository verification passed after the sixth review remediation. | `./scripts/verify_all.sh` -> 720 passed |
| Seventh review remediation | completed | Addressed source UI auth metadata finding: configured sources now show only a non-sensitive `auth=configured` marker instead of rendering `auth_ref`, with regression coverage. | `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q` -> 72 passed |
| Full verification after seventh remediation | completed | Full repository verification passed after the seventh review remediation. | `./scripts/verify_all.sh` -> 721 passed |
| Eighth review remediation | completed | Addressed final UI/sandbox/redaction findings: JSON pane/downloads now use sanitized payloads, Markdown download no longer falls back to raw JSON, Codex prompt redaction always applies local fallback patterns, and optional macOS sandbox profile now uses deny-by-default rules. | `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q` -> 73 passed |
| Full verification after eighth remediation | completed | Full repository verification passed after the eighth review remediation. | `./scripts/verify_all.sh` -> 722 passed |
| Ninth review remediation | completed | Addressed final sanitizer coverage finding with a Node VM behavior test that executes `renderResult()` and verifies sanitized JSON pane/state payloads for nested auth/token fields. | `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q` -> 74 passed |
| Full verification after ninth remediation | completed | Full repository verification passed after the ninth review remediation. | `./scripts/verify_all.sh` -> 723 passed |
| Tenth review remediation | completed | Addressed final raw sync payload and sandbox write-scope findings: `runAction()` now returns sanitized payloads to sync progress consumers, and optional macOS sandbox read/write allowlists are split so broad runtime paths are read-only. | `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q` -> 74 passed |
| Full verification after tenth remediation | completed | Full repository verification passed after the tenth review remediation. | `./scripts/verify_all.sh` -> 723 passed |
| Eleventh review remediation | completed | Addressed final follow-up findings: sync-status polling sanitizes payloads before rendering progress, JSON download regression now executes `downloadJson()` and verifies the Blob content, optional sandbox write assertions cover every write rule, and README download wording matches sanitized browser state behavior. | `python -m py_compile web_console/app.py tests/web_console/test_app.py`; `node --check web/app.js`; `git diff --check`; `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q` -> 74 passed |
| Full verification after eleventh remediation | completed | Full repository verification passed after the eleventh review remediation. | `./scripts/verify_all.sh` -> 723 passed |
| Twelfth review remediation | completed | Addressed fresh review findings: sanitizer now handles camelCase secret keys, `/api/sources` payloads and source refresh errors are sanitized before rendering, and non-zero Codex CLI stderr paths have safe-message regression coverage. | `python -m py_compile web_console/app.py tests/web_console/test_app.py`; `node --check web/app.js`; `git diff --check`; `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q` -> 76 passed |
| Full verification after twelfth remediation | completed | Full repository verification passed after the twelfth review remediation. | `./scripts/verify_all.sh` -> 725 passed |
| Review gate | completed | Fresh five-reviewer review pass after twelfth remediation reported no actionable findings. | Reviewer lenses: subprocess/security, UI/sanitization, tests, docs, end-to-end contract |
