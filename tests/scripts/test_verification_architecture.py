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


def test_ci_artifact_upload_is_guarded_when_eval_output_is_missing():
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "hashFiles('artifacts/contextwiki-evals/**') != ''" in workflow
    assert "if-no-files-found: warn" in workflow


def test_pyproject_live_marker_is_truthful_about_current_suite():
    repo_root = Path(__file__).resolve().parents[2]
    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")

    assert (
        '    "live: reserved for future opt-in live external API smoke tests; '
        'no retained automated tests currently use this marker",'
    ) in pyproject


def test_dockerfile_copies_shared_worker_runtime_import_boundary():
    repo_root = Path(__file__).resolve().parents[2]
    dockerfile = (repo_root / "Dockerfile").read_text(encoding="utf-8")
    worker_module = (repo_root / "indexing" / "sync_worker.py").read_text(
        encoding="utf-8"
    )

    assert "from app_runtime import build_ingestion_runtime" in worker_module
    assert "COPY app_runtime.py ./app_runtime.py" in dockerfile
    assert "COPY indexing ./indexing" in dockerfile


def test_readme_docker_worker_uses_bounded_local_log_driver():
    repo_root = Path(__file__).resolve().parents[2]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    assert "docker run -d --name contextwiki-sync-worker" in readme
    assert "--log-driver local" in readme
    assert "--log-opt max-size=5m" in readme
    assert "--log-opt max-file=3" in readme


def test_docs_require_both_processes_to_reload_source_configuration():
    repo_root = Path(__file__).resolve().parents[2]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    architecture = (repo_root / ".agents" / "docs" / "architecture.md").read_text(
        encoding="utf-8"
    )

    assert "Both FastMCP and the durable worker snapshot source configuration" in readme
    assert "fully restart the MCP" in readme
    assert "./scripts/restart_sync_worker_launch_agent.sh" in readme
    assert "Operators must restart both processes" in architecture
