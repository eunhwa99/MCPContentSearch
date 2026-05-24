import os


def get_env_secret(name: str) -> str:
    """Read a runtime secret by environment variable name."""
    return os.getenv(name, "")
