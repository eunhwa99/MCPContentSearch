# Plan Documents

`docs/plan/` contains implementation plan documents that must be written before running harness phases for file-changing work.

## When to Write a Plan

Create a plan document after branch preflight and before `harness-plan`, `harness-implement`, `harness-test`, or any non-plan target file edit.

This applies to feature work, bug fixes, refactoring, test work, MCP contract changes, indexing/search behavior changes, configuration changes, and instruction/docs-only harness changes.

Simple read-only questions, code review requests, command-output checks, and explanations do not need a plan unless they turn into file changes.

## File Naming

Use this format:

```text
YYYY-MM-DD-short-task-name.md
```

Keep the name stable for the work item. If the plan changes during retry loops or review fixes, update the same file instead of creating a new one.

## Required Sections

Each plan document should include:

- User request
- Branch preflight result
- Scope and non-goals
- Acceptance criteria
- Step breakdown, if the work needs multiple ordered steps
- Files likely to change
- Test and verification plan
- Functional smoke matrix or planned matrix rows before review
- Architecture/ADR constraints
- Risks and rollback notes
- Progress log

## Step Design

When a work item needs multiple ordered steps, write steps that can be executed and reviewed without relying on hidden conversation context.

- Keep each step focused on one module, contract, or behavior slice.
- Include files to read, required prior outputs, and the exact boundary of the work.
- Prefer interface-level direction over transcription. Name expected functions, classes, contracts, and invariants, then follow local patterns.
- Make acceptance criteria executable with commands or concrete smoke scenarios.
- Write constraints as specific rules. Use "Do not change X because Y" instead of broad warnings.
- Use short kebab-case labels such as `mcp-tool-contract`, `indexing-dedup`, or `fetcher-timeout`.

## Progress Tracking

Use this shape unless a smaller log is clearly enough:

```markdown
| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created `feature/example`. | `git status --short` |
| Focused verification | pending | Run compile or focused test. | Pending |
```

Status values:

- `pending`: not started
- `in_progress`: currently being worked
- `completed`: finished and verified at the planned level
- `blocked`: waiting on user input, credentials, local services, permissions, or external systems
- `error`: attempted and failed after local retry

When implementation, testing, review, or integration discovers new information, update the same progress table before continuing.

## Execution Rule

After writing the plan, run harness phases according to that plan. If implementation, testing, integration, or review discovers new information, update the plan first and then continue.
