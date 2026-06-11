import re
from urllib.parse import urlparse


DEBUG_HTTP_URL_RE = re.compile(r"https?://[^\s`]+", re.IGNORECASE)
DEBUG_FILE_URL_RE = re.compile(r"file://[^\s`]+", re.IGNORECASE)
DEBUG_HOME_PATH_RE = re.compile(r"~/(?:[^\s`]+/)*[^\s`]+")
DEBUG_HOME_BACKSLASH_PATH_RE = re.compile(r"~\\(?:[^\s`\\]+\\)*[^\s`\\]+")
DEBUG_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:[\\/](?:[^\s`\\/]+[\\/])*[^\s`\\/]+")
DEBUG_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9])/(?:[^\s`]+/)+[^\s`]+")
DEBUG_URL_FRAGMENT_PATH_RE = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}/")
DEBUG_URL_FRAGMENT_TOKEN_RE = re.compile(r"\b[A-Za-z0-9.-]+\.[A-Za-z]{2,}/[^\s`]+")
DEBUG_TLD_FRAGMENT_RE = re.compile(r"\b(?:com|net|org|io|dev|app|ai|co)/[^\s`]+")
DEBUG_SENSITIVE_KEY_PATTERN = (
    r"access[-_ ]?key(?:[-_ ]?id)?|access[-_ ]?token|api[-_ ]?key|apikey|auth|"
    r"authorization|aws[-_ ]?access[-_ ]?key[-_ ]?id|"
    r"aws[-_ ]?secret[-_ ]?access[-_ ]?key|client[-_ ]?secret|code|cookie|"
    r"credential|csrf[-_ ]?token|csrf|j[-_ ]?session[-_ ]?id|jwt[-_ ]?token|"
    r"jwt|key|pass|password|passwd|private[-_ ]?key|pwd|secret[-_ ]?key|"
    r"secret|session[-_ ]?id|session[-_ ]?token|session|sig|signature|"
    r"sid|ssh[-_ ]?private[-_ ]?key|token|xsrf[-_ ]?token|xsrf|"
    r"x[-_ ]?amz[-_ ]?access[-_ ]?key[-_ ]?id|x[-_ ]?amz[-_ ]?credential"
)
DEBUG_QUOTED_ASSIGNMENT_SECRET_RE = re.compile(
    rf"(?P<prefix>['\"]?(?:{DEBUG_SENSITIVE_KEY_PATTERN})['\"]?\s*[:=]\s*)"
    r"(?P<quote>['\"])(?P<secret>(?:\\.|(?!\2).)*)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
DEBUG_SECRET_ASSIGNMENT_RE = re.compile(
    rf"(?P<prefix>['\"]?(?:{DEBUG_SENSITIVE_KEY_PATTERN})['\"]?\s*[:=]\s*['\"]?)"
    r"(?P<secret>[^'\"\s,;}&]+)(?P<suffix>['\"]?)",
    re.IGNORECASE,
)
DEBUG_SECRET_QUERY_RE = re.compile(
    rf"(?P<prefix>[?&](?:{DEBUG_SENSITIVE_KEY_PATTERN})=)(?P<secret>[^&#\s]+)",
    re.IGNORECASE,
)
DEBUG_SECRET_WHITESPACE_RE = re.compile(
    rf"(?P<prefix>\b(?:{DEBUG_SENSITIVE_KEY_PATTERN})\b\s+)"
    r"(?P<secret>[A-Za-z0-9._~+/=-]{8,})",
    re.IGNORECASE,
)
DEBUG_SECRET_LIKE_RE = re.compile(
    r"(gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|"
    r"xox[baprs]-[A-Za-z0-9-]+|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
DEBUG_AUTH_SCHEME_SECRET_RE = re.compile(
    r"\b(?P<scheme>bearer|basic)\s+(?P<secret>[A-Za-z0-9._~+/=-]{8,})",
    re.IGNORECASE,
)
DEBUG_SECRET_VALUE_SHAPE_RE = re.compile(
    r"\b[A-Za-z0-9]{2,}(?:[-_][A-Za-z0-9]{2,}){2,}\b"
)
DEBUG_SECRET_OPAQUE_VALUE_RE = re.compile(r"[\d._~+/=-]")


def redact_debug_query_text(value: str) -> str:
    text = str(value or "")
    text = _redact_sensitive_query_parts(text)
    return DEBUG_SECRET_VALUE_SHAPE_RE.sub("[REDACTED]", text)


def redact_prompt_query_text(value: str) -> str:
    """Redact prompt payload secrets without dropping benign technical IDs."""
    return _redact_sensitive_query_parts(str(value or ""))


def _redact_sensitive_query_parts(text: str) -> str:
    text = DEBUG_HTTP_URL_RE.sub(
        lambda match: safe_debug_location(match.group(0)),
        text,
    )
    text = DEBUG_FILE_URL_RE.sub("redacted", text)
    text = DEBUG_HOME_PATH_RE.sub("redacted", text)
    text = DEBUG_HOME_BACKSLASH_PATH_RE.sub("redacted", text)
    text = DEBUG_WINDOWS_PATH_RE.sub("redacted", text)
    text = DEBUG_ABSOLUTE_PATH_RE.sub("redacted", text)
    text = DEBUG_URL_FRAGMENT_TOKEN_RE.sub("redacted", text)
    text = DEBUG_TLD_FRAGMENT_RE.sub("redacted", text)
    text = DEBUG_QUOTED_ASSIGNMENT_SECRET_RE.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote')}"
            f"[REDACTED]{match.group('quote')}"
        ),
        text,
    )
    text = DEBUG_SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]{match.group('suffix')}",
        text,
    )
    text = DEBUG_SECRET_QUERY_RE.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]",
        text,
    )
    text = DEBUG_SECRET_WHITESPACE_RE.sub(_redact_whitespace_secret_match, text)
    text = DEBUG_AUTH_SCHEME_SECRET_RE.sub(_redact_auth_scheme_secret_match, text)
    return DEBUG_SECRET_LIKE_RE.sub("[REDACTED]", text)


def _redact_whitespace_secret_match(match: re.Match) -> str:
    secret = match.group("secret")
    if not _is_secret_like_value(secret):
        return match.group(0)
    return f"{match.group('prefix')}[REDACTED]"


def _redact_auth_scheme_secret_match(match: re.Match) -> str:
    secret = match.group("secret")
    if not _is_auth_scheme_secret_like(secret):
        return match.group(0)
    return f"{match.group('scheme')} [REDACTED]"


def _is_secret_like_value(value: str) -> bool:
    normalized = str(value or "").strip()
    return len(normalized) >= 20 or bool(DEBUG_SECRET_OPAQUE_VALUE_RE.search(normalized))


def _is_auth_scheme_secret_like(value: str) -> bool:
    normalized = str(value or "").strip()
    return (
        len(normalized) >= 20
        or bool(DEBUG_SECRET_OPAQUE_VALUE_RE.search(normalized))
        or any(character.isupper() for character in normalized)
    )


def redact_debug_term(value: str) -> str:
    text = str(value or "")
    if DEBUG_URL_FRAGMENT_PATH_RE.match(text) or DEBUG_TLD_FRAGMENT_RE.match(text):
        return "redacted"
    return redact_debug_query_text(text)


def safe_debug_location(value: str) -> str:
    text = str(value or "")
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"}:
        if parsed.username or parsed.password:
            return "redacted"
        suffix = "/..." if parsed.path and parsed.path != "/" else ""
        return f"{parsed.scheme}://{parsed.netloc}{suffix}"
    if parsed.scheme or text.startswith("/"):
        return "redacted"
    return text
