from email.utils import parsedate_to_datetime
import re
from urllib.parse import parse_qsl, unquote, urldefrag, urljoin, urlparse


SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "cookie",
    "code",
    "csrf",
    "jwt",
    "key",
    "pass",
    "password",
    "secret",
    "session",
    "session_id",
    "sessionid",
    "sig",
    "signature",
    "sid",
    "token",
    "xsrf",
}
CREDENTIAL_LIKE_RE = re.compile(
    r"(?:"
    r"gh[pousr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AIza[A-Za-z0-9_-]{30,}|"
    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|"
    r"(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"
    r")",
    re.IGNORECASE,
)
SAFE_ETAG_RE = re.compile(r'^(?:W/)?"[\x20-\x21\x23-\x7e]*"$')
SENSITIVE_ASSIGNMENT_KEY_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])"
    r"(?:access[-_]?key(?:[-_]?id)?|access[-_]?token|api[-_]?key|"
    r"auth|authorization|aws[-_]?access[-_]?key[-_]?id|client[-_]?secret|"
    r"code|cookie|credential|csrf[-_]?token|csrf|j[-_]?session[-_]?id|"
    r"jwt[-_]?token|jwt|key|pass|password|php[-_]?sess[-_]?id|secret|"
    r"session[-_]?id|session[-_]?token|session|sig|signature|sid|token|"
    r"xsrf[-_]?token|xsrf|"
    r"x[-_]?amz[-_]?access[-_]?key[-_]?id|x[-_]?amz[-_]?credential|"
    r"x[-_]?amz[-_]?signature)"
    r"\s*[:=]",
    re.IGNORECASE,
)
SENSITIVE_PATH_SEGMENT_KEYS = {
    "accesskey",
    "accesskeyid",
    "accesstoken",
    "apikey",
    "authorization",
    "awsaccesskeyid",
    "clientsecret",
    "cookie",
    "credential",
    "csrf",
    "csrftoken",
    "jwt",
    "jwttoken",
    "jsessionid",
    "password",
    "phpsessid",
    "secret",
    "session",
    "sessionid",
    "sessiontoken",
    "sid",
    "token",
    "xamzaccesskeyid",
    "xamzcredential",
    "xamzsignature",
    "xsrf",
    "xsrftoken",
}


def normalize_url(url: str) -> str:
    normalized, _ = urldefrag(url.strip())
    parsed = urlparse(normalized)
    path = parsed.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return parsed._replace(path=path).geturl()


def fetch_url(url: str) -> str:
    normalized, _ = urldefrag(url.strip())
    return normalized


def seed_url(url: str) -> str:
    try:
        normalized = fetch_url(url)
    except ValueError as exc:
        raise ValueError(f"Invalid website seed URL: {redact_url_credentials(url)}") from exc
    if not valid_fetch_url(normalized):
        raise ValueError(f"Invalid website seed URL: {redact_url_credentials(normalized)}")
    return normalized


def join_fetch_url(base_url: str, value: str) -> str:
    try:
        joined_url = fetch_url(urljoin(base_url, value))
    except ValueError:
        return ""
    if not valid_fetch_url(joined_url):
        return ""
    return joined_url


def valid_fetch_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and not _has_url_credentials(parsed)
        and not _has_sensitive_query(parsed)
        and not contains_credential_like_value(url)
        and origin_host_port(parsed) is not None
    )


def canonical_url(url: str) -> str:
    normalized = normalize_url(url)
    parsed = urlparse(normalized)
    origin = origin_url(parsed)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{origin}{path}{query}"


def crawl_key(url: str) -> str:
    return canonical_url(url)


def has_non_fetchable_scheme(value: str) -> bool:
    try:
        scheme = urlparse(value.strip()).scheme.lower()
    except ValueError:
        return False
    return bool(scheme) and scheme not in {"http", "https"}


def same_origin(left: str, right: str) -> bool:
    try:
        left_parsed = urlparse(left)
        right_parsed = urlparse(right)
    except ValueError:
        return False
    left_origin = origin_host_port(left_parsed)
    right_origin = origin_host_port(right_parsed)
    return (
        left_parsed.scheme in {"http", "https"}
        and right_parsed.scheme in {"http", "https"}
        and left_parsed.scheme == right_parsed.scheme
        and left_origin is not None
        and right_origin is not None
        and left_origin == right_origin
    )


def origin_host_port(parsed) -> tuple[str, int | None] | None:
    if _has_url_credentials(parsed):
        return None
    try:
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return None
        port = parsed.port
    except ValueError:
        return None
    if port is None and parsed.scheme == "http":
        port = 80
    elif port is None and parsed.scheme == "https":
        port = 443
    return hostname, port


def origin_url(parsed) -> str:
    origin = origin_host_port(parsed)
    if origin is None:
        raise ValueError(f"Invalid URL origin: {redact_url_credentials(parsed.geturl())}")
    hostname, port = origin
    default_port = (parsed.scheme == "http" and port == 80) or (
        parsed.scheme == "https" and port == 443
    )
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host if default_port or port is None else f"{host}:{port}"
    return f"{parsed.scheme}://{netloc}"


def safe_response_header_value(headers: dict[str, str], name: str) -> str:
    value = _header_value(headers, name)
    if (
        not value
        or contains_disallowed_control_text(value)
        or contains_credential_like_value(value)
    ):
        return ""
    normalized_name = name.lower()
    if normalized_name == "etag" and not _valid_etag(value):
        return ""
    if normalized_name == "last-modified" and not _valid_http_date(value):
        return ""
    return value


def contains_credential_like_value(value: str) -> bool:
    return any(
        CREDENTIAL_LIKE_RE.search(variant)
        or _contains_sensitive_key_marker(variant)
        or _contains_sensitive_path_segment(variant)
        for variant in _decoded_variants(value)
    )


def contains_disallowed_control_text(value: str) -> bool:
    return any(
        (ord(character) < 32 and character not in {"\t", "\n", "\r"})
        or ord(character) == 127
        for character in value
    )


def redact_url_credentials(url: str) -> str:
    if not url:
        return url
    if contains_credential_like_value(url):
        return "<redacted>"
    try:
        parsed = urlparse(url)
    except ValueError:
        return _redact_raw_url_credentials(url)
    if "@" not in parsed.netloc:
        redacted = parsed._replace(
            query=_redact_query_secrets(parsed.query),
            fragment=_redact_fragment(parsed.fragment),
        ).geturl()
        if "@" in redacted and not parsed.netloc:
            return _redact_raw_url_credentials(redacted)
        return redacted
    host_part = parsed.netloc.rsplit("@", 1)[1]
    return parsed._replace(
        netloc=f"<credentials>@{host_part}",
        query=_redact_query_secrets(parsed.query),
        fragment=_redact_fragment(parsed.fragment),
    ).geturl()


def _has_url_credentials(parsed) -> bool:
    return bool(parsed.username or parsed.password)


def _has_sensitive_query(parsed) -> bool:
    return any(
        _is_sensitive_query_key(key) or contains_credential_like_value(value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    )


def _is_sensitive_query_key(key: str) -> bool:
    for variant in _decoded_variants(key.strip()):
        normalized = variant.lower().replace("-", "_")
        compact = re.sub(r"[^a-z0-9]", "", variant.lower())
        if (
            normalized in SENSITIVE_QUERY_KEYS
            or normalized.endswith("_token")
            or normalized.endswith("_session")
            or normalized.endswith("_cookie")
            or normalized.endswith("_jwt")
            or normalized.endswith("_csrf")
            or normalized.endswith("_xsrf")
            or normalized.endswith("_secret")
            or normalized.endswith("_key")
            or normalized.endswith("_signature")
            or normalized.endswith("_credential")
            or "access_key" in normalized
            or compact in {
                "accesskey",
                "accesskeyid",
                "awsaccesskeyid",
                "cookie",
                "csrf",
                "csrftoken",
                "jwt",
                "jwttoken",
                "jsessionid",
                "phpsessid",
                "session",
                "sessionid",
                "sessiontoken",
                "sid",
                "xsrf",
                "xsrftoken",
            }
            or compact.endswith("token")
            or compact.endswith("session")
            or compact.endswith("sessionid")
            or compact.endswith("cookie")
            or compact.endswith("csrf")
            or compact.endswith("xsrf")
            or compact.endswith("jwt")
            or compact.endswith("accesskey")
            or compact.endswith("accesskeyid")
        ):
            return True
    return False


def _contains_sensitive_key_marker(value: str) -> bool:
    return bool(SENSITIVE_ASSIGNMENT_KEY_RE.search(value))


def _contains_sensitive_path_segment(value: str) -> bool:
    segments = [segment for segment in re.split(r"[\\/]+", value) if segment]
    for index, segment in enumerate(segments[:-1]):
        if _is_sensitive_path_segment_key(segment) and segments[index + 1]:
            return True
    return False


def _is_sensitive_path_segment_key(value: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", value.lower())
    return compact in SENSITIVE_PATH_SEGMENT_KEYS


def _decoded_variants(value: str) -> tuple[str, ...]:
    variants = [value]
    current = value
    for _ in range(3):
        decoded = unquote(current)
        if decoded == current:
            break
        variants.append(decoded)
        current = decoded
    return tuple(variants)


def _redact_query_secrets(query: str) -> str:
    if not query:
        return query
    return "redacted"


def _redact_fragment(fragment: str) -> str:
    return "<redacted>" if fragment else fragment


def _redact_raw_url_credentials(url: str) -> str:
    scheme_end = url.find("://")
    authority_start = scheme_end + 3 if scheme_end != -1 else 0
    at_index = url.find("@", authority_start)
    redacted = url
    if at_index != -1:
        redacted = f"{url[:authority_start]}<credentials>@{url[at_index + 1:]}"
    return _redact_raw_query_secrets(redacted)


def _redact_raw_query_secrets(url: str) -> str:
    query_start = url.find("?")
    fragment_start = url.find("#")
    if query_start == -1:
        if fragment_start == -1:
            return url
        return f"{url[:fragment_start]}#<redacted>"
    fragment_start = url.find("#", query_start)
    query_end = len(url) if fragment_start == -1 else fragment_start
    query = url[query_start + 1 : query_end]
    redacted_query = _redact_query_secrets(query)
    redacted_url = f"{url[:query_start + 1]}{redacted_query}{url[query_end:]}"
    redacted_fragment_start = redacted_url.find("#", query_start)
    if redacted_fragment_start == -1:
        return redacted_url
    return f"{redacted_url[:redacted_fragment_start]}#<redacted>"


def _valid_etag(value: str) -> bool:
    return bool(SAFE_ETAG_RE.fullmatch(value))


def _valid_http_date(value: str) -> bool:
    try:
        return parsedate_to_datetime(value) is not None
    except (TypeError, ValueError, IndexError, OverflowError):
        return False


def _header_value(headers: dict[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name:
            return value.strip()
    return ""
