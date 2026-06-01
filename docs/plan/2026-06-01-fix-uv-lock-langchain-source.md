# Fix uv.lock langchain source parse error

## User Request

- `error: Failed to parse uv.lock ... Dependency langchain has missing source field ...`
- 요청: 잠금 파일 파싱 오류 버그 수정

## Branch Preflight Result

- 시작 브랜치: `main` (clean)
- 최신화: `git fetch origin main`, `git pull --ff-only origin main`
- 작업 브랜치 생성: `feature/fix-uv-lock-langchain-source`

## Scope and Non-Goals

- Scope
  - `uv.lock` 파싱 실패 원인 복구
  - `uv lock --check` 및 최소 동기화 검증
- Non-goals
  - 애플리케이션 로직/기능 변경
  - 의존성 정책 변경

## Acceptance Criteria

1. `uv.lock` 파싱 오류가 재현되지 않는다.
2. `uv lock --check`가 성공한다.
3. `uv sync --dev`가 정상 수행된다(환경 의존 이슈 없을 때).

## Files Likely To Change

- `uv.lock`
- `docs/plan/2026-06-01-fix-uv-lock-langchain-source.md`

## Verification Plan

- `uv lock --check`
- `uv sync --dev`

## Architecture / ADR Notes

- 의존성 잠금 파일 복구 작업으로 아키텍처/ADR 변경 없음.

## Risks and Rollback

- Risk: lock 재생성 시 transitive dependency 해시/메타가 광범위 변경될 수 있음.
- Mitigation: `pyproject.toml`은 변경하지 않고 lock만 재생성.
- Rollback: `uv.lock`을 직전 커밋 상태로 되돌림.

## Progress Log

| Phase | Status | Notes |
| --- | --- | --- |
| Branch preflight | completed | clean `main`에서 최신화 후 `feature/fix-uv-lock-langchain-source` 생성 |
| Planning | completed | 본 계획 문서 작성 |
| Implementation decision | completed | 본 수정은 lock 파일 단일 복구 성격의 atomic 작업으로 메인 에이전트가 직접 수행 |
| Implementation | completed | 손상된 `uv.lock` 제거 후 `uv lock`으로 잠금 파일 재생성 |
| Verification | completed | `uv lock --check` 성공, `uv sync --dev` 성공 |
