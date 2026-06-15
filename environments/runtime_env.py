from pathlib import Path
import os

from dotenv import load_dotenv


def _repo_dotenv_path() -> Path:
    return Path(__file__).resolve().parents[1] / ".env"


def load_repo_dotenv() -> bool:
    """Load the repository-local .env file without relying on process cwd."""
    return load_dotenv(dotenv_path=_repo_dotenv_path(), override=False)


def get_env_secret(name: str) -> str:
    """Read a runtime secret by environment variable name."""
    load_repo_dotenv()
    return os.getenv(name, "")
