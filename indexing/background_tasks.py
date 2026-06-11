from __future__ import annotations

import re

_SENSITIVE_KEY_PATTERN = (
    r"access[-_]?key(?:[-_]?id)?|access[-_]?token|api[-_]?key|apikey|auth|"
    r"authorization|aws[-_]?access[-_]?key[-_]?id|"
    r"aws[-_]?secret[-_]?access[-_]?key|client[-_]?secret|code|cookie|"
    r"credential|csrf[-_]?token|csrf|j[-_]?session[-_]?id|jwt[-_]?token|"
    r"jwt|key|pass|password|passwd|private[-_]?key|pwd|secret[-_]?key|"
    r"secret|session[-_]?id|session[-_]?token|session|sig|signature|"
    r"sid|ssh[-_]?private[-_]?key|token|xsrf[-_]?token|xsrf|"
    r"x[-_]?amz[-_]?access[-_]?key[-_]?id|x[-_]?amz[-_]?credential"
)
_PEM_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_QUOTED_ASSIGNMENT_SECRET_PATTERN = re.compile(
    rf"(?P<prefix>['\"]?(?:{_SENSITIVE_KEY_PATTERN})['\"]?\s*[:=]\s*)"
    r"(?P<quote>['\"])(?P<secret>(?:\\.|(?!\2).)*)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_TOKEN_SECRET_PATTERN = re.compile(
    r"(gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|"
    r"xox[baprs]-[A-Za-z0-9-]+|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
    r"(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,})",
    re.IGNORECASE,
)
_MULTIWORD_ASSIGNMENT_SECRET_PATTERN = re.compile(
    rf"(?P<prefix>['\"]?(?:{_SENSITIVE_KEY_PATTERN})['\"]?\s*[:=]\s*['\"]?)"
    rf"(?P<secret>[^'\"\n,;}}]+?)(?P<suffix>['\"]?)"
    rf"(?=(?:\s+['\"]?(?:{_SENSITIVE_KEY_PATTERN})['\"]?\s*[:=])|[\n,;}}]|$)",
    re.IGNORECASE,
)
_ASSIGNMENT_SECRET_PATTERN = re.compile(
    rf"(?P<prefix>['\"]?(?:{_SENSITIVE_KEY_PATTERN})['\"]?\s*[:=]\s*['\"]?)"
    r"(?P<secret>[^'\"\s,;}]+)(?P<suffix>['\"]?)",
    re.IGNORECASE,
)
_QUERY_SECRET_PATTERN = re.compile(
    rf"(?P<prefix>[?&](?:{_SENSITIVE_KEY_PATTERN})=)(?P<secret>[^&#\s]+)",
    re.IGNORECASE,
)
_WHITESPACE_SECRET_PATTERN = re.compile(
    rf"(?P<prefix>\b(?:{_SENSITIVE_KEY_PATTERN})\b\s+)"
    r"(?P<secret>[A-Za-z0-9._~+/=-]{8,})",
    re.IGNORECASE,
)
_FILE_URL_PATTERN = re.compile(r"file://[^\s`]+", re.IGNORECASE)
_HOME_PATH_PATTERN = re.compile(r"~/(?:[^\s`]+/)*[^\s`]+")
_HOME_BACKSLASH_PATH_PATTERN = re.compile(r"~\\(?:[^\s`\\]+\\)*[^\s`\\]+")
_WINDOWS_PATH_PATTERN = re.compile(r"\b[A-Za-z]:[\\/](?:[^\s`\\/]+[\\/])*[^\s`\\/]+")
_ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])/(?:[^\s`]+/)+[^\s`]+")


def safe_error_message(error: BaseException, max_length: int = 300) -> str:
    """Return an MCP-safe error summary without obvious credential material."""
    message = str(error) or error.__class__.__name__
    message = _PEM_BLOCK_PATTERN.sub("<redacted>", message)
    message = _QUOTED_ASSIGNMENT_SECRET_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{match.group('quote')}<redacted>{match.group('quote')}",
        message,
    )
    message = _TOKEN_SECRET_PATTERN.sub("<redacted>", message)
    message = _WHITESPACE_SECRET_PATTERN.sub(
        lambda match: f"{match.group('prefix')}<redacted>",
        message,
    )
    message = _MULTIWORD_ASSIGNMENT_SECRET_PATTERN.sub(
        lambda match: f"{match.group('prefix')}<redacted>{match.group('suffix')}",
        message,
    )
    message = _ASSIGNMENT_SECRET_PATTERN.sub(
        lambda match: f"{match.group('prefix')}<redacted>{match.group('suffix')}",
        message,
    )
    message = _QUERY_SECRET_PATTERN.sub(
        lambda match: f"{match.group('prefix')}<redacted>",
        message,
    )
    message = _FILE_URL_PATTERN.sub("<redacted>", message)
    message = _HOME_PATH_PATTERN.sub("<redacted>", message)
    message = _HOME_BACKSLASH_PATH_PATTERN.sub("<redacted>", message)
    message = _WINDOWS_PATH_PATTERN.sub("<redacted>", message)
    message = _ABSOLUTE_PATH_PATTERN.sub("<redacted>", message)
    if len(message) > max_length:
        message = f"{message[: max_length - 3]}..."
    return message
