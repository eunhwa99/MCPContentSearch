from __future__ import annotations

import re
import sys
from typing import TextIO

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
_COOKIE_HEADER_START_PATTERN = re.compile(
    r"\b(?:set-cookie|cookie)\s*[:=]",
    re.IGNORECASE,
)
_COOKIE_HEADER_SECRET_PATTERN = re.compile(
    r"(?P<prefix>\b(?:set-cookie|cookie)\s*[:=][ \t]*)"
    r"(?P<secret>[^\r\n]*(?:\r?\n[ \t]+[^\r\n]*)*)",
    re.IGNORECASE | re.DOTALL,
)
_QUOTED_ASSIGNMENT_SECRET_PATTERN = re.compile(
    rf"(?P<prefix>['\"]?(?:{_SENSITIVE_KEY_PATTERN})['\"]?\s*[:=]\s*)"
    r"(?P<quote>['\"])(?P<secret>(?:\\.|(?!\2).)*)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_TOKEN_SECRET_PATTERN = re.compile(
    r"(gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|"
    r"(?:ntn|secret)_[A-Za-z0-9_-]{16,}|"
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
_HTTP_URL_PATTERN = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_STRUCTURED_FIELD_START = (
    r"(?:[,;]\s*|\s+)[A-Za-z_][A-Za-z0-9_.-]*\s*="
)
_SENSITIVE_WHITESPACE_FIELD_START = (
    rf"[,;]?\s+(?:{_SENSITIVE_KEY_PATTERN})\b\s+"
)
_PATH_TERMINATOR = (
    rf"(?:{_STRUCTURED_FIELD_START}|{_SENSITIVE_WHITESPACE_FIELD_START}|"
    r"[\"'<>\n]|$)"
)
_PATH_CONTENT = rf"(?:(?!{_PATH_TERMINATOR}).)+?"
_PATH_PROSE_START = (
    r"[,;]?\s+(?:after|and|before|because|during|using|when|while|with)\b"
)
_PATH_EXTENSION_CONTENT = (
    rf"(?:(?![\"'<>\n]).)+?\.[A-Za-z0-9]{{1,16}}"
    rf"(?=(?:{_STRUCTURED_FIELD_START}|{_PATH_PROSE_START}|[\"'<>\n]|$))"
)
_PATH_VALUE = rf"(?:{_PATH_EXTENSION_CONTENT}|{_PATH_CONTENT})"
_LABELED_PATH_PATTERN = re.compile(
    rf"(?P<prefix>\b(?:path|directory|dir|file|filename)\s*(?:=|:)\s*)"
    rf"{_PATH_VALUE}(?={_PATH_TERMINATOR}|{_PATH_PROSE_START})",
    re.IGNORECASE,
)
_ABSOLUTE_PATH_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9:])(?:file://|~[\\/]|/|[A-Za-z]:[\\/]|"
    rf"\\\\(?:[?.]\\)?)"
    rf"{_PATH_VALUE}(?={_PATH_TERMINATOR}|{_PATH_PROSE_START})",
    re.IGNORECASE,
)


def sanitize_error_text(value: object, max_length: int = 300) -> str:
    """Redact credentials, URLs, and paths before text crosses a durable boundary."""
    message = str(value)
    message = _PEM_BLOCK_PATTERN.sub("<redacted>", message)
    message = _COOKIE_HEADER_SECRET_PATTERN.sub(
        lambda match: f"{match.group('prefix')}<redacted>",
        message,
    )
    message = _HTTP_URL_PATTERN.sub("<redacted>", message)
    message = _LABELED_PATH_PATTERN.sub(
        lambda match: f"{match.group('prefix')}<redacted>",
        message,
    )
    message = _ABSOLUTE_PATH_PATTERN.sub("<redacted>", message)
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
    if len(message) > max_length:
        message = f"{message[: max_length - 3]}..."
    return message


def safe_error_message(error: BaseException, max_length: int = 300) -> str:
    """Return an MCP-safe error summary without obvious credential material."""
    message = str(error) or error.__class__.__name__
    return sanitize_error_text(message, max_length=max_length)


def sanitize_error_stream(
    source: TextIO,
    target: TextIO,
    *,
    max_line_chars: int = 65536,
) -> None:
    """Sanitize a text stream without retaining unbounded diagnostic lines."""
    cookie_header_pending = False
    while True:
        line = source.readline(max_line_chars + 1)
        if not line:
            return
        if len(line) > max_line_chars and not line.endswith("\n"):
            cookie_header_pending = (
                cookie_header_pending and line.startswith((" ", "\t"))
            )
            scan_tail = ""
            while True:
                probe = f"{scan_tail}{line}"
                if _COOKIE_HEADER_START_PATTERN.search(probe):
                    cookie_header_pending = True
                scan_tail = probe[-16:]
                if line.endswith("\n"):
                    break
                line = source.readline(max_line_chars + 1)
                if not line:
                    break
            target.write("<redacted oversized diagnostic>")
            if line.endswith("\n"):
                target.write("\n")
            target.flush()
            if not line:
                return
            continue
        is_cookie_continuation = cookie_header_pending and line.startswith(
            (" ", "\t")
        )
        if is_cookie_continuation:
            indentation = line[: len(line) - len(line.lstrip(" \t"))]
            synthetic_header = f"Cookie: {line[len(indentation):]}"
            sanitized = sanitize_error_text(
                synthetic_header,
                max_length=max(300, len(synthetic_header) + 1),
            )
            target.write(f"{indentation}{sanitized.removeprefix('Cookie: ')}")
        else:
            target.write(
                sanitize_error_text(
                    line,
                    max_length=max(300, len(line) + 1),
                )
            )
        target.flush()
        if _COOKIE_HEADER_START_PATTERN.search(line):
            cookie_header_pending = True
        elif is_cookie_continuation:
            cookie_header_pending = True
        else:
            cookie_header_pending = False


def _main() -> int:
    if sys.argv[1:] != ["--stream"]:
        return 2
    sanitize_error_stream(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
