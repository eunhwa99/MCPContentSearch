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
DEBUG_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?P<prefix>(?:access[-_]?token|api[-_]?key|apikey|auth|authorization|"
    r"client[-_]?secret|credential|key|password|secret|session|sig|signature|token)"
    r"\s*[:=]\s*['\"]?)(?P<secret>[^'\"\s,;}]+)(?P<suffix>['\"]?)",
    re.IGNORECASE,
)
DEBUG_SECRET_QUERY_RE = re.compile(
    r"(?P<prefix>[?&](?:access[-_]?token|api[-_]?key|apikey|auth|authorization|"
    r"client[-_]?secret|credential|key|password|secret|session|sig|signature|token)=)"
    r"(?P<secret>[^&#\s]+)",
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
DEBUG_SECRET_VALUE_SHAPE_RE = re.compile(
    r"\b[A-Za-z0-9]{2,}(?:[-_][A-Za-z0-9]{2,}){2,}\b"
)


def redact_debug_query_text(value: str) -> str:
    text = str(value or "")
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
    text = DEBUG_SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]{match.group('suffix')}",
        text,
    )
    text = DEBUG_SECRET_QUERY_RE.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]",
        text,
    )
    text = DEBUG_SECRET_LIKE_RE.sub("[REDACTED]", text)
    return DEBUG_SECRET_VALUE_SHAPE_RE.sub("[REDACTED]", text)


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
