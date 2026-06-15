from pathlib import Path


def test_verify_all_defines_explicit_verification_layers():
    repo_root = Path(__file__).resolve().parents[2]
    script = (repo_root / "scripts" / "verify_all.sh").read_text(encoding="utf-8")

    assert 'export IS_TESTING="${IS_TESTING:-1}"' in script
    assert 'uv run --locked python -m compileall "${RETAINED_PACKAGES[@]}"' in script
    assert 'tests/contracts/test_public_mcp_contracts.py' in script
    assert "scripts/run_contextwiki_eval.py" in script
    assert "artifacts/contextwiki-evals" in script


def test_ci_runs_contracts_evals_and_functional_gate_with_testing_env():
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "Run public MCP contract tests" in workflow
    assert 'IS_TESTING: "1"' in workflow
    assert "tests/contracts/test_public_mcp_contracts.py" in workflow
    assert "scripts/run_contextwiki_eval.py" in workflow
    assert "./scripts/verify_functional_e2e.sh" in workflow


def test_readme_describes_verification_architecture_layers_truthfully():
    repo_root = Path(__file__).resolve().parents[2]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    assert "Public MCP contract layer" in readme
    assert "Deterministic functional E2E layer" in readme
    assert "Deterministic quality eval layer" in readme
    assert "Manual live smoke layer" in readme
    assert "No retained automated pytest currently uses the `live` marker." in readme
    assert "tests/scripts/test_live_query_smoke.py` only verifies the CLI contract" in readme


def test_pyproject_live_marker_is_truthful_about_current_suite():
    repo_root = Path(__file__).resolve().parents[2]
    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")

    assert (
        '    "live: reserved for future opt-in live external API smoke tests; '
        'no retained automated tests currently use this marker",'
    ) in pyproject
