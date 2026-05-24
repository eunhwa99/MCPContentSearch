import base64
import binascii
from dataclasses import dataclass
import json
import re
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlparse

import httpx

from core.models import DocumentModel
from environments.config import AppConfig


TEXT_EXTENSIONS = {
    ".md",
    ".mdx",
    ".markdown",
    ".txt",
    ".rst",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".kt",
    ".kts",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".swift",
    ".scala",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".xml",
    ".html",
    ".css",
}
SAFE_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
SAFE_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_GITHUB_OBJECT_ID_RE = re.compile(r"^(?:[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64})$")
UNSAFE_REF_CHARS = set("\\ ~^:?*[]")
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "cookie",
    "csrf",
    "code",
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


@dataclass(frozen=True)
class GitHubRepositorySpec:
    owner: str
    repo: str
    ref: str


@dataclass(frozen=True)
class GitHubRepositorySnapshot:
    commit_sha: str
    tree_sha: str


class GitHubHTTPClient:
    def __init__(self, timeout: float, *, transport=None):
        self.timeout = timeout
        self.transport = transport

    async def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()

    async def get_blob_json(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        max_response_bytes: int,
    ) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            async with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                if _content_length_exceeds(response.headers, max_response_bytes):
                    raise RuntimeError("GitHub blob response exceeded byte limit")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_response_bytes:
                        raise RuntimeError("GitHub blob response exceeded byte limit")
                try:
                    return json.loads(bytes(body).decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    raise RuntimeError("GitHub blob response was not valid JSON") from None

    async def get_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.text


class GitHubRepositoryFetcher:
    """Fetch bounded text/code files from configured GitHub repositories."""

    def __init__(
        self,
        repositories: tuple[str, ...],
        config: AppConfig,
        *,
        token: str = "",
        http_client=None,
    ):
        self.repository_specs = [
            parse_repository_spec(repository, config.github_default_ref)
            for repository in repositories
        ]
        _ensure_unique_repository_specs(self.repository_specs)
        self.config = config
        self.token = token
        self.http_client = http_client or GitHubHTTPClient(config.request_timeout)
        self.snapshot_complete = True

    async def fetch_documents(self) -> list[DocumentModel]:
        self.snapshot_complete = True
        documents: list[DocumentModel] = []
        for spec in self.repository_specs:
            snapshot = await self._resolve_snapshot(spec)
            tree = await self._fetch_tree(spec, snapshot)
            for entry in self._select_entries(tree):
                content = await self._fetch_blob_text(spec, entry["sha"], entry["size"])
                if content is None:
                    self.snapshot_complete = False
                    continue
                documents.append(self._to_document(spec, snapshot, entry, content))
        return documents

    async def _resolve_snapshot(
        self,
        spec: GitHubRepositorySpec,
    ) -> GitHubRepositorySnapshot:
        owner = quote(spec.owner, safe="")
        repo = quote(spec.repo, safe="")
        ref = quote(spec.ref, safe="")
        url = f"https://api.github.com/repos/{owner}/{repo}/commits/{ref}"
        payload = await self.http_client.get_json(url, headers=self._headers())
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"GitHub commit response for {spec.owner}/{spec.repo}@{spec.ref} was invalid"
            )
        commit_sha = payload.get("sha")
        commit_payload = payload.get("commit")
        tree_payload = commit_payload.get("tree") if isinstance(commit_payload, dict) else None
        tree_sha = tree_payload.get("sha") if isinstance(tree_payload, dict) else None
        if not isinstance(commit_sha, str) or not _valid_github_object_id(commit_sha):
            raise RuntimeError(
                f"GitHub commit sha for {spec.owner}/{spec.repo}@{spec.ref} was invalid"
            )
        if not isinstance(tree_sha, str) or not _valid_github_object_id(tree_sha):
            raise RuntimeError(
                f"GitHub tree sha for {spec.owner}/{spec.repo}@{spec.ref} was invalid"
            )
        return GitHubRepositorySnapshot(commit_sha=commit_sha, tree_sha=tree_sha)

    async def _fetch_tree(
        self,
        spec: GitHubRepositorySpec,
        snapshot: GitHubRepositorySnapshot,
    ) -> list[dict[str, Any]]:
        owner = quote(spec.owner, safe="")
        repo = quote(spec.repo, safe="")
        tree_sha = quote(snapshot.tree_sha, safe="")
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{tree_sha}?recursive=1"
        payload = await self.http_client.get_json(url, headers=self._headers())
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"GitHub tree response for {spec.owner}/{spec.repo}@{spec.ref} was invalid"
            )
        if payload.get("truncated"):
            raise RuntimeError(
                f"GitHub tree response for {spec.owner}/{spec.repo}@{spec.ref} was truncated"
            )
        tree = payload.get("tree")
        if not isinstance(tree, list):
            raise RuntimeError(
                f"GitHub tree response for {spec.owner}/{spec.repo}@{spec.ref} "
                "was missing tree"
            )
        return list(tree)

    async def _fetch_blob_text(
        self,
        spec: GitHubRepositorySpec,
        blob_sha: str,
        expected_size: int,
    ) -> str | None:
        owner = quote(spec.owner, safe="")
        repo = quote(spec.repo, safe="")
        if not _valid_github_object_id(blob_sha):
            raise RuntimeError(
                f"GitHub blob sha for {spec.owner}/{spec.repo}@{spec.ref} was invalid"
            )
        if expected_size > self.config.github_max_file_bytes:
            return None
        sha = quote(blob_sha, safe="")
        url = f"https://api.github.com/repos/{owner}/{repo}/git/blobs/{sha}"
        get_blob_json = getattr(self.http_client, "get_blob_json", None)
        if callable(get_blob_json):
            payload = await get_blob_json(
                url,
                headers=self._headers(),
                max_response_bytes=_github_blob_response_byte_limit(
                    self.config.github_max_file_bytes
                ),
            )
        else:
            payload = await self.http_client.get_json(url, headers=self._headers())
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"GitHub blob response for {spec.owner}/{spec.repo}@{spec.ref} was invalid"
            )
        payload_size = _payload_size(payload)
        if payload_size > self.config.github_max_file_bytes:
            return None
        if payload_size != expected_size:
            raise RuntimeError(
                f"GitHub blob response for {spec.owner}/{spec.repo}@{spec.ref} "
                "size did not match tree"
            )
        encoding = str(payload.get("encoding", "")).lower()
        content = payload.get("content")
        if content is None:
            raise RuntimeError(
                f"GitHub blob response for {spec.owner}/{spec.repo}@{spec.ref} "
                "was missing content"
            )
        if not isinstance(content, str):
            raise RuntimeError(
                f"GitHub blob response for {spec.owner}/{spec.repo}@{spec.ref} "
                "had non-text content"
            )
        if encoding == "base64":
            compact = "".join(content.split())
            if _encoded_base64_exceeds(compact, self.config.github_max_file_bytes):
                return None
            try:
                raw_content = base64.b64decode(compact, validate=True)
            except (binascii.Error, ValueError):
                raise RuntimeError(
                    f"GitHub blob response for {spec.owner}/{spec.repo}@{spec.ref} "
                    "had invalid base64 content"
                ) from None
            if len(raw_content) > self.config.github_max_file_bytes:
                return None
            if len(raw_content) != expected_size:
                raise RuntimeError(
                    f"GitHub blob response for {spec.owner}/{spec.repo}@{spec.ref} "
                    "size did not match content"
                )
            if _looks_like_binary_bytes(raw_content):
                return None
            try:
                return raw_content.decode("utf-8")
            except UnicodeDecodeError:
                return None
        if encoding not in {"", "utf-8", "text"}:
            raise RuntimeError(
                f"GitHub blob response for {spec.owner}/{spec.repo}@{spec.ref} "
                f"used unsupported encoding {encoding!r}"
            )
        raw_content = content.encode("utf-8")
        if len(raw_content) > self.config.github_max_file_bytes:
            return None
        if len(raw_content) != expected_size:
            raise RuntimeError(
                f"GitHub blob response for {spec.owner}/{spec.repo}@{spec.ref} "
                "size did not match content"
            )
        if _looks_like_binary_text(content):
            return None
        return content

    def _select_entries(self, tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
        eligible = []
        for entry in sorted(tree, key=_tree_entry_sort_key):
            if not isinstance(entry, dict):
                self.snapshot_complete = False
                continue
            path = entry.get("path", "")
            sha = entry.get("sha", "")
            if not isinstance(path, str) or _contains_control_character(path):
                self.snapshot_complete = False
                continue
            if entry.get("type") != "blob":
                continue
            if not isinstance(sha, str) or not _valid_github_object_id(sha):
                self.snapshot_complete = False
                continue
            if not self._is_supported_file(path):
                continue
            if _contains_credential_like_value(path):
                self.snapshot_complete = False
                continue
            size = _tree_entry_size(entry)
            if size is None:
                self.snapshot_complete = False
                continue
            if size > self.config.github_max_file_bytes:
                self.snapshot_complete = False
                continue
            eligible.append({**entry, "size": size})
        if len(eligible) > self.config.github_max_files:
            self.snapshot_complete = False
        return eligible[: self.config.github_max_files]

    @staticmethod
    def _is_supported_file(path: str) -> bool:
        lower = path.lower()
        return any(lower.endswith(extension) for extension in TEXT_EXTENSIONS)

    def _to_document(
        self,
        spec: GitHubRepositorySpec,
        snapshot: GitHubRepositorySnapshot,
        entry: dict[str, Any],
        content: str,
    ) -> DocumentModel:
        path = entry["path"]
        identity_owner, identity_repo = _repository_identity(spec)
        external_id = f"github:{identity_owner}/{identity_repo}:{path}"
        canonical_url = self._canonical_url(spec, snapshot.commit_sha, path)
        return DocumentModel(
            id=external_id,
            document_id=external_id,
            external_id=external_id,
            title=f"{spec.owner}/{spec.repo}/{path}",
            content=content,
            url=canonical_url,
            canonical_url=canonical_url,
            platform="GitHub",
            source_id="source_github",
            path=path,
            updated_at=entry.get("sha", ""),
            version_id=entry.get("sha", ""),
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": self.config.web_user_agent,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @staticmethod
    def _canonical_url(
        spec: GitHubRepositorySpec,
        commit_sha: str,
        path: str,
    ) -> str:
        return (
            f"https://github.com/{spec.owner}/{spec.repo}/blob/"
            f"{quote(commit_sha, safe='')}/{quote(path, safe='/')}"
        )


def parse_repository_spec(value: str, default_ref: str = "main") -> GitHubRepositorySpec:
    normalized = value.strip()
    if "://" in normalized:
        parsed = urlparse(normalized)
        if parsed.username or parsed.password or _has_sensitive_query(parsed):
            raise ValueError(
                f"Invalid GitHub repository spec: {_redact_url_credentials(normalized)}"
            )
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != "github.com":
            raise ValueError(
                f"Invalid GitHub repository spec: {_redact_url_credentials(normalized)}"
            )
        if parsed.query or parsed.fragment:
            raise ValueError(
                f"Invalid GitHub repository spec: {_redact_url_credentials(normalized)}"
            )
        path = parsed.path.strip("/")
        if path.endswith(".git"):
            repo_part = path[:-4]
            ref = ""
        else:
            repo_part, separator, maybe_ref = path.partition(".git@")
            ref = maybe_ref if separator else ""
            if not separator:
                repo_part = path
                repo_name, separator, maybe_ref = repo_part.partition("@")
                if separator:
                    repo_part = repo_name
                    ref = maybe_ref
    else:
        repo_part, _, ref = normalized.partition("@")

    if repo_part.endswith(".git"):
        repo_part = repo_part[:-4]
    pieces = [piece for piece in repo_part.strip("/").split("/") if piece]
    effective_ref = (ref or default_ref).strip()
    if len(pieces) != 2 or not _valid_owner_repo_ref(
        pieces[0],
        pieces[1],
        effective_ref,
    ):
        raise ValueError(f"Invalid GitHub repository spec: {_redact_url_credentials(value)}")
    return GitHubRepositorySpec(owner=pieces[0], repo=pieces[1], ref=effective_ref)


def _ensure_unique_repository_specs(specs: list[GitHubRepositorySpec]) -> None:
    seen = set()
    for spec in specs:
        key = (spec.owner.lower(), spec.repo.lower())
        if key in seen:
            raise ValueError(f"Duplicate GitHub repository spec: {spec.owner}/{spec.repo}")
        seen.add(key)


def _repository_identity(spec: GitHubRepositorySpec) -> tuple[str, str]:
    return spec.owner.lower(), spec.repo.lower()


def _valid_owner_repo_ref(owner: str, repo: str, ref: str) -> bool:
    if not SAFE_OWNER_RE.match(owner) or not SAFE_REPO_RE.match(repo):
        return False
    if _contains_credential_like_value(owner) or _contains_credential_like_value(repo):
        return False
    if any(part in {".", ".."} for part in (owner, repo)):
        return False
    if any(character in repo for character in "/?#@:"):
        return False
    if not _valid_git_ref(ref):
        return False
    return True


def _valid_git_ref(ref: str) -> bool:
    if not ref:
        return False
    components = ref.split("/")
    if (
        ref == "@"
        or ref.startswith("/")
        or ref.endswith("/")
        or ref.endswith(".")
        or ref.endswith(".lock")
        or "#" in ref
        or _contains_control_character(ref)
        or _contains_credential_like_value(ref)
        or any(character in UNSAFE_REF_CHARS for character in ref)
        or ".." in ref
        or "//" in ref
        or "@{" in ref
        or any(
            not component
            or component.startswith(".")
            or component.endswith(".lock")
            for component in components
        )
    ):
        return False
    return True


def _contains_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _looks_like_binary_bytes(value: bytes) -> bool:
    return any(byte < 32 and byte not in {9, 10, 13} or byte == 127 for byte in value)


def _looks_like_binary_text(value: str) -> bool:
    return any(
        (ord(character) < 32 and character not in {"\t", "\n", "\r"})
        or ord(character) == 127
        for character in value
    )


def _payload_size(payload: dict[str, Any]) -> int:
    if "size" not in payload:
        raise RuntimeError("GitHub blob response was missing size") from None
    size = payload["size"]
    if isinstance(size, bool) or not isinstance(size, int):
        raise RuntimeError("GitHub blob response had invalid size") from None
    if size < 0:
        raise RuntimeError("GitHub blob response had invalid size")
    return size


def _tree_entry_sort_key(item: Any) -> str:
    path = item.get("path", "") if isinstance(item, dict) else ""
    return path if isinstance(path, str) else ""


def _tree_entry_size(entry: dict[str, Any]) -> int | None:
    size = entry.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return None
    return size


def _github_blob_response_byte_limit(max_file_bytes: int) -> int:
    return max(1024, max_file_bytes * 2 + 4096)


def _encoded_base64_exceeds(compact_content: str, max_file_bytes: int) -> bool:
    max_encoded_length = ((max_file_bytes + 2) // 3) * 4
    return len(compact_content) > max_encoded_length


def _content_length_exceeds(headers, max_response_bytes: int) -> bool:
    content_length = ""
    for key, value in headers.items():
        if key.lower() == "content-length":
            content_length = str(value).strip()
            break
    if not content_length:
        return False
    try:
        return int(content_length) > max_response_bytes
    except ValueError:
        return False


def _valid_github_object_id(value: str) -> bool:
    return bool(SAFE_GITHUB_OBJECT_ID_RE.fullmatch(value))


def _contains_credential_like_value(value: str) -> bool:
    return any(
        CREDENTIAL_LIKE_RE.search(variant)
        or _contains_sensitive_key_marker(variant)
        or _contains_sensitive_path_segment(variant)
        for variant in _decoded_variants(value)
    )


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


def _has_sensitive_query(parsed) -> bool:
    return any(
        _is_sensitive_query_key(key)
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
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


def _redact_url_credentials(url: str) -> str:
    if _contains_credential_like_value(url):
        return "<redacted>"
    try:
        parsed = urlparse(url)
    except ValueError:
        return _redact_raw_url(url)
    if "@" not in parsed.netloc:
        if "@" in url and not parsed.netloc:
            return _redact_raw_url(url)
        return parsed._replace(
            query=_redact_query_secrets(parsed.query),
            fragment=_redact_fragment(parsed.fragment),
        ).geturl()
    host_part = parsed.netloc.rsplit("@", 1)[1]
    return parsed._replace(
        netloc=f"<credentials>@{host_part}",
        query=_redact_query_secrets(parsed.query),
        fragment=_redact_fragment(parsed.fragment),
    ).geturl()


def _redact_query_secrets(query: str) -> str:
    if not query:
        return query
    return "redacted"


def _redact_fragment(fragment: str) -> str:
    return "<redacted>" if fragment else fragment


def _redact_raw_url(url: str) -> str:
    redacted = url
    scheme_end = redacted.find("://")
    authority_start = scheme_end + 3 if scheme_end != -1 else 0
    at_index = redacted.find("@", authority_start)
    if at_index != -1:
        redacted = f"{redacted[:authority_start]}<credentials>@{redacted[at_index + 1:]}"
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
