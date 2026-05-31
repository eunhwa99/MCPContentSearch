# Harness Playwright Functional E2E Gate

## User Request

- "응 해줘"
- 맥락: 구현 완료 후 기능 전체를 E2E로 확인하는 하네스를 원함
- 이번 추가 요청: 기존 기능 E2E 게이트를 Playwright 브라우저 클릭 E2E까지 포함하도록 확장

## Branch Preflight Result

- 시작 작업트리(`/Users/eunhwa/IdeaProjects/MCPContentSearch`)는 dirty 상태였음 (`.idea/*` 변경)
- dirty 작업트리에서는 switch/pull/cleanup 없이 보존
- `git fetch origin main` 후 격리 worktree 생성:
  - `/tmp/MCPContentSearch-e2e-gate`
  - branch: `feature/expand-playwright-e2e-gate` from `origin/main`
- 현재 격리 worktree 상태는 clean에서 시작

## Scope and Non-Goals

- Scope
  - 기능 E2E 전용 검증 스크립트 추가 (`verify_functional_e2e.sh`)
  - Playwright 기반 Web Console 브라우저 클릭 smoke 추가
  - `verify_all.sh`에 기능 E2E 게이트 자동 연동
  - 하네스/레포 지침 문서 업데이트 (`AGENTS.md`, harness docs, README)
- Non-goals
  - 외부 live API를 호출하는 네트워크 E2E 강제
  - 사용자 로컬 Chroma/SQLite 데이터 변경
  - MCP 도구 계약 변경

## Acceptance Criteria

1. `./scripts/verify_functional_e2e.sh`가 deterministic fake smoke + e2e test + Playwright browser smoke를 실행한다.
2. `./scripts/verify_all.sh`가 기능 E2E 게이트를 자동 실행한다.
3. Playwright smoke는 로컬 fake Web Console 앱에서 핵심 사용자 플로우를 클릭으로 검증한다.
4. 하네스 문서에 기능 E2E/Playwright 게이트가 명시된다.
5. 전체 검증 명령 실행 결과가 성공한다.

## Step Breakdown

1. 기능 E2E 게이트 스크립트 및 Playwright smoke 스크립트 구현
2. `verify_all.sh` 연동
3. 하네스/지침 문서 업데이트
4. 기능 E2E 게이트 단독 실행 + 전체 verify 실행

## Files Likely To Change

- `scripts/verify_functional_e2e.sh` (new)
- `scripts/smoke_web_console_playwright.py` (new)
- `scripts/verify_all.sh`
- `pyproject.toml`
- `AGENTS.md`
- `.agents/docs/harness-engineering.md`
- `.agents/skills/harness-test/SKILL.md`
- `README.md`
- `docs/plan/2026-06-01-harness-playwright-functional-e2e-gate.md`

## Test and Verification Plan

- `./scripts/verify_functional_e2e.sh`
- `./scripts/verify_all.sh`
- `git diff --check`

## Architecture/ADR Constraints

- `.agents/docs/architecture.md` 계층 경계 유지
- accepted ADR와 충돌 금지 (특히 metadata/store safety, live integration opt-in 원칙)
- local-only deterministic 검증 우선

## Risks and Rollback Notes

- 위험: Playwright 설치/브라우저 준비 실패 시 CI/로컬 속도 저하 또는 환경 의존성 증가
- 완화: 게이트는 local fake app만 사용, live source 미사용, 실패 시 원인 노출
- 롤백: `verify_all.sh`에서 기능 E2E 연동 제거 및 새 스크립트 revert

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | dirty main 보존 후 `origin/main` 기반 격리 worktree/branch 생성 | `git status --short --branch`; `git worktree add /tmp/MCPContentSearch-e2e-gate ...` |
| Harness context | completed | harness docs/skill + AGENTS + current verify scripts 확인 | `.agents/docs/harness-engineering.md`; `.agents/skills/harness-test/SKILL.md`; `AGENTS.md`; `scripts/verify_all.sh` |
| Worker orchestration decision | completed | 본 작업은 하네스 검증 게이트 확장 단일 경계(스크립트/문서 중심)로 atomic 처리 | 본 계획서 |
| Implementation | completed | 기능 E2E 게이트, Playwright browser-click smoke, verify_all 연동, 하네스/레포 문서 업데이트 반영 | `scripts/verify_functional_e2e.sh`; `scripts/smoke_web_console_playwright.py`; `scripts/verify_all.sh`; `pyproject.toml`; `AGENTS.md`; `.agents/docs/harness-engineering.md`; `.agents/skills/harness-test/SKILL.md`; `README.md` |
| Verification | completed | 기능 게이트 단독 실행 + 전체 verify 경로에서 기능 게이트/Playwright 자동 실행 확인 | `./scripts/verify_functional_e2e.sh` -> 176 passed + fake smoke passed + Playwright smoke passed; `./scripts/verify_all.sh` -> non-live pytest passed + functional E2E gate(176 passed) + Playwright smoke passed |
| Subagent review pass 1 remediation | completed | 리뷰어 지적 반영: Playwright 준비/실행 fallback 정합성, conditional bootstrap, verify_all 중복 실행 축소, 문서 표현 정합성 수정 | `scripts/verify_functional_e2e.sh`; `scripts/verify_all.sh`; `AGENTS.md`; `README.md`; `.agents/docs/harness-engineering.md`; `.agents/skills/harness-test/SKILL.md` |
| Subagent review pass 2 findings | completed | 5인 fresh review에서 fake smoke 런타임 혼용(plain `python`) 및 Playwright 설치 안내 런타임 정합성 이슈 확인 | reviewer findings: `scripts/verify_functional_e2e.sh:74`, `scripts/verify_functional_e2e.sh:64`, `README.md:443` |
| Pass 2 remediation + rerun | completed | fake smoke를 `run_py`로 통일하고 Playwright 설치 안내를 `uv run python -m playwright ...`로 정렬 후 영향 범위 재검증 완료 | `scripts/verify_functional_e2e.sh`; `README.md`; `./scripts/verify_functional_e2e.sh` -> 176 passed + Playwright smoke passed |
| Subagent review pass 3 findings | completed | 5인 fresh review에서 fallback 런타임 안내 분기, `verify_all` 재검증 증거, Playwright output temp 파일 정리 안전성 보완 필요 지적 | reviewer findings: `scripts/verify_functional_e2e.sh:45-70`, `README.md:443`, plan evidence gap |
| Pass 3 remediation + rerun | completed | Playwright 설치 안내를 `USE_UV` 분기 기반으로 정렬하고 temp 파일 cleanup `trap` 추가, 최신 수정 이후 `verify_all` 통합 경로 재검증 완료 | `scripts/verify_functional_e2e.sh`; `README.md`; `./scripts/verify_all.sh` -> 561 passed + functional gate(176 passed) + Playwright smoke passed |
| Subagent review pass 4 findings | completed | 5인 fresh review에서 함수 내부 `exit 1` 경로가 `RETURN` trap cleanup을 우회할 수 있다는 low finding 확인 | reviewer finding: `scripts/verify_functional_e2e.sh:69,73` |
| Pass 4 remediation + rerun | completed | 함수 내부 실패 경로를 `return 1`로 전환해 cleanup trap 동작 보장 후 기능 E2E 게이트 재검증 완료 | `scripts/verify_functional_e2e.sh`; `./scripts/verify_functional_e2e.sh` -> 176 passed + Playwright smoke passed |
| Subagent review pass 5 findings | completed | 5인 fresh review에서 Playwright bootstrap 실패 분기(`set -e`) 보호 및 `verify_all.sh` Node 선행조건 안내 메시지 보완 필요 지적 | reviewer findings: `scripts/verify_functional_e2e.sh:53-64`, `scripts/verify_all.sh:18` |
| Pass 5 remediation + rerun | completed | bootstrap/install 재시도 경로를 `if ! ...; then ... return 1`으로 안전화하고 Node 선행조건 실패 메시지를 추가한 뒤 `verify_all` 통합 경로 재검증 완료 | `scripts/verify_functional_e2e.sh`; `scripts/verify_all.sh`; `./scripts/verify_all.sh` -> 561 passed + functional gate(176 passed) + Playwright smoke passed |
| Subagent review pass 6 findings | completed | 5인 fresh review에서 compileall 문서 명령이 실제 verify 스크립트(`web_console` 포함)와 불일치하는 low finding 확인 | reviewer findings: `AGENTS.md:46-47`, `.agents/docs/harness-engineering.md:212,218` |
| Pass 6 remediation + rerun | completed | 문서 compileall 명령에 `web_console` 포함 정렬, 문서 변경 후 경량 검증(`git diff --check`) 통과 | `AGENTS.md`; `.agents/docs/harness-engineering.md`; `git diff --check` |
| Subagent review pass 7 findings | completed | 5인 fresh review에서 `harness-test` skill의 compileall 명령도 `web_console` 누락 드리프트와 docs-only 경량 검증 증거 보강 필요 지적 | reviewer findings: `.agents/skills/harness-test/SKILL.md:19`, plan evidence gap |
| Pass 7 remediation + rerun | completed | `harness-test` compileall 명령을 `web_console` 포함으로 정렬하고 docs-only 경량 검증 전체 시퀀스(`git diff --name-only`, `git status --short --branch`, `git diff --check`, docs stage, `git diff --cached --check`) 완료 | `.agents/skills/harness-test/SKILL.md`; staged docs checks |
| Review gate | in_progress | 최종 clean 판단을 위한 fresh 5인 `$subagent-review-loop` pass 8 진행 예정 | Pending pass 8 outputs |
