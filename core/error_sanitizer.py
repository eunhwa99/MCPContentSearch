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
_AUTHORIZATION_HEADER_START_PATTERN = re.compile(
    r"\bauthorization[ \t]*[:=][ \t]*(?:bearer|basic)\b",
    re.IGNORECASE,
)
_LINE_END_PATTERN = r"(?:\r\n|\r|\n)"
_AUTHORIZATION_HEADER_NAME_ONLY_PATTERN = re.compile(
    r"^[ \t]*authorization(?:[ \t]*[:=])?[ \t]*$",
    re.IGNORECASE,
)
_COOKIE_HEADER_NAME_ONLY_PATTERN = re.compile(
    rf"^[ \t]*(?:set-cookie|cookie)[ \t]*(?:{_LINE_END_PATTERN})?$",
    re.IGNORECASE,
)
_COOKIE_HEADER_NAME_ONLY_BLOCK_PATTERN = re.compile(
    rf"(?P<header>(?<![^\r\n])[ \t]*(?:set-cookie|cookie)[ \t]*"
    rf"{_LINE_END_PATTERN})"
    rf"(?P<folded>(?:[ \t]+[^\r\n]*(?:{_LINE_END_PATTERN}|\Z))+)",
    re.IGNORECASE,
)
_COOKIE_FOLDED_LINE_PATTERN = re.compile(
    rf"(?<![^\r\n])(?P<indent>[ \t]+)[^\r\n]*"
    rf"(?P<ending>{_LINE_END_PATTERN}|\Z)",
)
_COOKIE_HEADER_SECRET_PATTERN = re.compile(
    r"(?P<prefix>\b(?:set-cookie|cookie)\s*[:=][ \t]*)"
    rf"(?P<secret>[^\r\n]*(?:{_LINE_END_PATTERN}[ \t]+[^\r\n]*)*)",
    re.IGNORECASE | re.DOTALL,
)
_AUTHORIZATION_FOLDED_BLOCK_PATTERN = re.compile(
    r"(?P<header>\bauthorization[ \t]*[:=][ \t]*"
    rf"(?:(?:bearer|basic)\b[^\r\n]*{_LINE_END_PATTERN}|"
    rf"{_LINE_END_PATTERN}[ \t]+(?:bearer|basic)[ \t]*"
    rf"{_LINE_END_PATTERN}))"
    rf"(?P<folded>(?:[ \t]+[^\r\n]*(?:{_LINE_END_PATTERN}|\Z))+)",
    re.IGNORECASE,
)
_AUTHORIZATION_BARE_NAME_FOLDED_BLOCK_PATTERN = re.compile(
    rf"(?P<header>(?<![^\r\n])[ \t]*authorization[ \t]*"
    rf"{_LINE_END_PATTERN}[ \t]+(?:bearer|basic)[ \t]*"
    rf"{_LINE_END_PATTERN})"
    rf"(?P<folded>(?:[ \t]+[^\r\n]*(?:{_LINE_END_PATTERN}|\Z))+)",
    re.IGNORECASE,
)
_AUTH_SCHEME_SECRET_PATTERN = re.compile(
    r"(?P<prefix>\b(?:Bearer|Basic)[ \t]+)"
    r"(?P<secret>(?!(?:authentication|troubleshooting)\b)[^ \t\r\n,;}]+)",
    re.IGNORECASE,
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
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_MULTIWORD_ASSIGNMENT_SECRET_PATTERN = re.compile(
    rf"(?P<prefix>['\"]?(?:{_SENSITIVE_KEY_PATTERN})['\"]?\s*[:=]\s*['\"]?)"
    rf"(?P<secret>[^'\"\r\n,;}}]+?)(?P<suffix>['\"]?)"
    rf"(?=(?:\s+['\"]?(?:{_SENSITIVE_KEY_PATTERN})['\"]?\s*[:=])|[\r\n,;}}]|$)",
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
    r"[\"'<>\r\n]|$)"
)
_PATH_CONTENT = rf"(?:(?!{_PATH_TERMINATOR}).)+?"
_PATH_PROSE_START = (
    r"[,;]?\s+(?:after|and|before|because|during|using|when|while|with)\b"
)
_PATH_EXTENSION_CONTENT = (
    rf"(?:(?![\"'<>\r\n]).)+?\.[A-Za-z0-9]{{1,16}}"
    rf"(?=(?:{_STRUCTURED_FIELD_START}|{_PATH_PROSE_START}|[\"'<>\r\n]|$))"
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


def _redact_name_only_cookie_block(match: re.Match[str]) -> str:
    folded = _COOKIE_FOLDED_LINE_PATTERN.sub(
        lambda line: (
            f"{line.group('indent')}<redacted>{line.group('ending')}"
        ),
        match.group("folded"),
    )
    return f"{match.group('header')}{folded}"


def _redact_folded_authorization_block(match: re.Match[str]) -> str:
    folded = _COOKIE_FOLDED_LINE_PATTERN.sub(
        lambda line: (
            f"{line.group('indent')}<redacted-auth>{line.group('ending')}"
        ),
        match.group("folded"),
    )
    return f"{match.group('header')}{folded}"


def sanitize_error_text(value: object, max_length: int = 300) -> str:
    """Redact credentials, URLs, and paths before text crosses a durable boundary."""
    message = str(value)
    message = _PEM_BLOCK_PATTERN.sub("<redacted>", message)
    message = _COOKIE_HEADER_NAME_ONLY_BLOCK_PATTERN.sub(
        _redact_name_only_cookie_block,
        message,
    )
    message = _AUTHORIZATION_BARE_NAME_FOLDED_BLOCK_PATTERN.sub(
        _redact_folded_authorization_block,
        message,
    )
    message = _AUTHORIZATION_FOLDED_BLOCK_PATTERN.sub(
        _redact_folded_authorization_block,
        message,
    )
    message = _COOKIE_HEADER_SECRET_PATTERN.sub(
        lambda match: f"{match.group('prefix')}<redacted>",
        message,
    )
    message = _AUTH_SCHEME_SECRET_PATTERN.sub(
        lambda match: f"{match.group('prefix')}<redacted-auth>",
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


def _cookie_header_pending_after_chunk(
    chunk: str,
    *,
    was_pending: bool,
) -> bool:
    pending = was_pending
    logical_line_start = 0
    for line_ending in re.finditer(_LINE_END_PATTERN, chunk):
        logical_line = chunk[logical_line_start : line_ending.start()]
        is_continuation = pending and logical_line.startswith((" ", "\t"))
        if (
            _COOKIE_HEADER_START_PATTERN.search(logical_line)
            or _COOKIE_HEADER_NAME_ONLY_PATTERN.fullmatch(logical_line)
        ):
            pending = True
        elif is_continuation:
            pending = True
        else:
            pending = False
        logical_line_start = line_ending.end()

    if logical_line_start < len(chunk):
        logical_line = chunk[logical_line_start:]
        is_continuation = pending and logical_line.startswith((" ", "\t"))
        if (
            _COOKIE_HEADER_START_PATTERN.search(logical_line)
            or _COOKIE_HEADER_NAME_ONLY_PATTERN.fullmatch(logical_line)
        ):
            pending = True
        elif is_continuation:
            pending = True
        else:
            pending = False
    return pending


def _authorization_header_pending_after_chunk(
    chunk: str,
    *,
    was_pending: bool,
) -> bool:
    pending = was_pending
    logical_line_start = 0
    for line_ending in re.finditer(_LINE_END_PATTERN, chunk):
        logical_line = chunk[logical_line_start : line_ending.start()]
        if (
            _AUTHORIZATION_HEADER_START_PATTERN.search(logical_line)
            or _AUTHORIZATION_HEADER_NAME_ONLY_PATTERN.fullmatch(logical_line)
        ):
            pending = True
        elif pending and logical_line.startswith((" ", "\t")):
            pending = True
        else:
            pending = False
        logical_line_start = line_ending.end()

    if logical_line_start < len(chunk):
        logical_line = chunk[logical_line_start:]
        if (
            _AUTHORIZATION_HEADER_START_PATTERN.search(logical_line)
            or _AUTHORIZATION_HEADER_NAME_ONLY_PATTERN.fullmatch(logical_line)
        ):
            pending = True
        elif pending and logical_line.startswith((" ", "\t")):
            pending = True
        else:
            pending = False
    return pending


def sanitize_error_stream(
    source: TextIO,
    target: TextIO,
    *,
    max_line_chars: int = 65536,
) -> None:
    """Sanitize a text stream without retaining unbounded diagnostic lines."""
    cookie_header_pending = False
    authorization_header_pending = False
    cookie_continuation_fragment_pending = False
    authorization_continuation_fragment_pending = False
    pending_chunk = ""
    trailing_cr_pending = False
    while True:
        if pending_chunk:
            line = pending_chunk
            pending_chunk = ""
        else:
            line = source.readline(max_line_chars + 1)
        if not line:
            return
        if trailing_cr_pending:
            trailing_cr_pending = False
            if line.startswith("\n"):
                target.write("\n")
                target.flush()
                line = line[1:]
                if not line:
                    continue
        if len(line) > max_line_chars and not line.endswith("\n"):
            line_ending = re.search(_LINE_END_PATTERN, line)
            while True:
                if line_ending is not None:
                    break
                line = source.readline(max_line_chars + 1)
                if not line:
                    break
                line_ending = re.search(_LINE_END_PATTERN, line)
            # Chunking can separate a sensitive header name from its delimiter,
            # so continuation lines after any oversized diagnostic fail closed.
            cookie_header_pending = True
            authorization_header_pending = True
            cookie_continuation_fragment_pending = False
            authorization_continuation_fragment_pending = False
            target.write("<redacted oversized diagnostic>")
            if line_ending is not None:
                target.write(line_ending.group())
                pending_chunk = line[line_ending.end() :]
                trailing_cr_pending = (
                    line_ending.group() == "\r"
                    and line_ending.end() == len(line)
                )
            target.flush()
            if not line:
                return
            continue
        is_cookie_continuation = cookie_header_pending and (
            cookie_continuation_fragment_pending
            or line.startswith((" ", "\t"))
        )
        is_authorization_continuation = (
            authorization_header_pending
            and (
                authorization_continuation_fragment_pending
                or line.startswith((" ", "\t"))
            )
        )
        if is_cookie_continuation or is_authorization_continuation:
            indentation = line[: len(line) - len(line.lstrip(" \t"))]
            header = (
                "Cookie:"
                if is_cookie_continuation
                else "Authorization: Bearer"
            )
            synthetic_header = f"{header} {line[len(indentation):]}"
            sanitized = sanitize_error_text(
                synthetic_header,
                max_length=max(300, len(synthetic_header) + 1),
            )
            target.write(
                f"{indentation}{sanitized.removeprefix(f'{header} ')}"
            )
        else:
            target.write(
                sanitize_error_text(
                    line,
                    max_length=max(300, len(line) + 1),
                )
            )
        target.flush()
        ends_with_line_ending = (
            re.search(rf"{_LINE_END_PATTERN}\Z", line) is not None
        )
        cookie_state_chunk = (
            f" {line}" if cookie_continuation_fragment_pending else line
        )
        authorization_state_chunk = (
            f" {line}"
            if authorization_continuation_fragment_pending
            else line
        )
        cookie_header_pending = _cookie_header_pending_after_chunk(
            cookie_state_chunk,
            was_pending=cookie_header_pending,
        )
        authorization_header_pending = _authorization_header_pending_after_chunk(
            authorization_state_chunk,
            was_pending=authorization_header_pending,
        )
        cookie_continuation_fragment_pending = (
            cookie_header_pending and not ends_with_line_ending
        )
        authorization_continuation_fragment_pending = (
            authorization_header_pending and not ends_with_line_ending
        )
        trailing_cr_pending = line.endswith("\r")


def _main() -> int:
    if sys.argv[1:] != ["--stream"]:
        return 2
    sanitize_error_stream(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
