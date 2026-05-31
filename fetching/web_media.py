from typing import Protocol
from urllib.parse import parse_qsl, unquote, urlparse
import re

from fetching.web_safety import contains_disallowed_control_text


SUPPORTED_PAGE_CONTENT_TYPES = {
    "application/markdown",
    "application/xhtml+xml",
    "text/html",
    "text/markdown",
    "text/plain",
    "text/x-markdown",
}
READABLE_RESPONSE_CONTENT_TYPES = SUPPORTED_PAGE_CONTENT_TYPES | {
    "application/atom+xml",
    "application/rss+xml",
    "application/xml",
    "text/xml",
}
SUPPORTED_PAGE_EXTENSIONS = {
    ".htm",
    ".html",
    ".markdown",
    ".md",
    ".rst",
    ".txt",
}
UNSUPPORTED_PAGE_EXTENSIONS = {
    ".7z",
    ".avi",
    ".bmp",
    ".bz2",
    ".dmg",
    ".doc",
    ".docx",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".iso",
    ".jar",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".svg",
    ".svgz",
    ".tar",
    ".tgz",
    ".webm",
    ".webp",
    ".xls",
    ".xlsx",
    ".zip",
}
HTML_DOCUMENT_ROOTS = {
    "article",
    "body",
    "div",
    "h1",
    "h2",
    "head",
    "html",
    "main",
    "p",
    "section",
    "title",
}
HTML_FRAGMENT_ROOTS = HTML_DOCUMENT_ROOTS | {
    "a",
    "abbr",
    "aside",
    "b",
    "blockquote",
    "cite",
    "code",
    "data",
    "dd",
    "details",
    "dfn",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "h3",
    "h4",
    "h5",
    "h6",
    "i",
    "kbd",
    "li",
    "mark",
    "ol",
    "pre",
    "q",
    "s",
    "samp",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
    "var",
    "wbr",
}


class WebResponseLike(Protocol):
    url: str
    text: str
    headers: dict[str, str]
    body_prefix: bytes


def supports_page_content(response: WebResponseLike) -> bool:
    content_type = content_type_header(response.headers)
    extension = url_extension(response.url)
    if response_looks_like_binary_media(response):
        return False
    if has_unsupported_media_hint(response.url):
        return body_looks_like_html_page(response.text)
    if not content_type:
        if extension in SUPPORTED_PAGE_EXTENSIONS:
            return True
        return body_looks_like_html_page(response.text)
    if content_type == "text/html":
        return True
    if content_type == "application/xhtml+xml":
        return body_looks_like_html_page(response.text, allow_fragments=True)
    if content_type == "text/plain" and markup_root_name(response.text):
        return body_looks_like_html_page(response.text)
    return content_type in SUPPORTED_PAGE_CONTENT_TYPES


def explicit_html_response_has_unsupported_xml_root(response: WebResponseLike) -> bool:
    content_type = content_type_header(response.headers)
    root_name = markup_root_name(response.text)
    if content_type == "application/xhtml+xml":
        return not body_looks_like_html_page(response.text, allow_fragments=True)
    if content_type == "text/html" and root_name in {"urlset", "sitemapindex"}:
        return True
    if content_type == "text/html" and looks_like_xml(response.url, response.text):
        return not body_looks_like_html_page(response.text, allow_fragments=True)
    return False


def response_disables_stale_cleanup(response: WebResponseLike) -> bool:
    content_type = content_type_header(response.headers)
    extension = url_extension(response.url)
    if has_unsupported_media_hint(response.url):
        return True
    if content_type == "text/html":
        return False
    if content_type == "application/xhtml+xml":
        return not body_looks_like_html_page(response.text, allow_fragments=True)
    if content_type in SUPPORTED_PAGE_CONTENT_TYPES:
        return True
    if not content_type and extension in SUPPORTED_PAGE_EXTENSIONS:
        if extension not in {".htm", ".html"}:
            return True
        return not body_looks_like_html_page(response.text, allow_fragments=True)
    return False


def should_read_response_body(url: str, headers: dict[str, str]) -> bool:
    content_type = content_type_header(headers)
    return not content_type or content_type in READABLE_RESPONSE_CONTENT_TYPES


def has_unsupported_media_hint(url: str) -> bool:
    extension = url_extension(url)
    if extension in UNSUPPORTED_PAGE_EXTENSIONS:
        return True
    return query_extension(url) in UNSUPPORTED_PAGE_EXTENSIONS


def looks_like_xml(url: str, body: str) -> bool:
    stripped = strip_leading_bom(body).lstrip().lower()
    return stripped.startswith("<?xml") or urlparse(url).path.lower().endswith(".xml")


def markup_root_name(body: str) -> str:
    stripped = _strip_markup_preamble(body)
    match = re.match(r"<\s*([A-Za-z_][\w:.-]*)\b", stripped)
    if not match:
        return ""
    return match.group(1).rsplit(":", 1)[-1].lower()


def body_looks_like_html_page(body: str, *, allow_fragments: bool = False) -> bool:
    leading = strip_leading_bom(body).lstrip().lower()
    root_name = markup_root_name(body)
    if root_name:
        html_roots = HTML_FRAGMENT_ROOTS if allow_fragments else HTML_DOCUMENT_ROOTS
        return root_name in html_roots or (
            leading.startswith("<!doctype html") and root_name in html_roots
        )
    return leading.startswith("<!doctype html")


def content_type_header(headers: dict[str, str]) -> str:
    for key, value in headers.items():
        if key.lower() == "content-type":
            return value.split(";", 1)[0].strip().lower()
    return ""


def strip_leading_bom(text: str) -> str:
    return text.lstrip("\ufeff")


def url_extension(url: str) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path).lower()
    filename = path.rsplit("/", 1)[-1]
    if "." not in filename:
        return ""
    return f".{filename.rsplit('.', 1)[-1]}"


def query_extension(url: str) -> str:
    parsed = urlparse(url)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        for part in (key, value):
            part = unquote(part).lower()
            filename = part.rsplit("/", 1)[-1]
            if "." in filename:
                extension = f".{filename.rsplit('.', 1)[-1]}"
                if extension in UNSUPPORTED_PAGE_EXTENSIONS:
                    return extension
    return ""


def response_looks_like_binary_media(response: WebResponseLike) -> bool:
    return (
        body_prefix_looks_like_binary_media(response.body_prefix)
        or body_looks_like_textual_media(response.text)
        or body_looks_like_binary_media(response.text)
    )


def body_prefix_looks_like_binary_media(prefix: bytes) -> bool:
    stripped = prefix.lstrip(b"\xef\xbb\xbf\r\n\t ")
    lowered = stripped.lower()
    control_bytes = [
        byte
        for byte in prefix[:256]
        if (byte < 32 and byte not in {9, 10, 13}) or byte == 127
    ]
    return (
        stripped.startswith(b"%PDF-")
        or stripped.startswith(b"\x89PNG")
        or stripped.startswith(b"GIF87a")
        or stripped.startswith(b"GIF89a")
        or stripped.startswith(b"\xff\xd8\xff")
        or stripped.startswith(b"PK\x03\x04")
        or stripped.startswith(b"\x1f\x8b")
        or stripped.startswith(b"BZh")
        or stripped.startswith(b"MZ")
        or stripped.startswith(b"BM")
        or stripped.startswith(b"\xd0\xcf\x11\xe0")
        or stripped.startswith(b"\xfd7zXZ\x00")
        or stripped.startswith(b"\x1aE\xdf\xa3")
        or stripped.startswith(b"\xff\xfb")
        or stripped.startswith(b"\xff\xf3")
        or stripped.startswith(b"\xff\xf2")
        or lowered.startswith(b"rar!")
        or lowered.startswith(b"7z")
        or lowered.startswith(b"id3")
        or (len(stripped) >= 12 and stripped.startswith(b"RIFF") and stripped[8:12] == b"WEBP")
        or (len(stripped) >= 8 and stripped[4:8] == b"ftyp")
        or (len(prefix) >= 262 and prefix[257:262] == b"ustar")
        or b"\x00" in prefix[:256]
        or len(control_bytes) >= 3
    )


def body_looks_like_textual_media(body: str) -> bool:
    return markup_root_name(body) in {"feed", "opml", "rdf", "rss", "svg"}


def body_looks_like_binary_media(body: str) -> bool:
    stripped = body.lstrip("\ufeff\r\n\t ")
    lowered = stripped.lower()
    return (
        stripped.startswith("%PDF-")
        or stripped.startswith("\x89PNG")
        or stripped.startswith("GIF87a")
        or stripped.startswith("GIF89a")
        or stripped.startswith("PK\x03\x04")
        or stripped.startswith("BZh")
        or stripped.startswith("MZ")
        or stripped.startswith("BM")
        or stripped.startswith("\x1aEߣ")
        or stripped.startswith("���")
        or lowered.startswith("rar!")
        or lowered.startswith("7z")
        or lowered.startswith("id3")
        or (len(stripped) >= 262 and stripped[257:262] == "ustar")
        or contains_disallowed_control_text(stripped)
    )


def _strip_markup_preamble(body: str) -> str:
    stripped = strip_leading_bom(body).lstrip()
    for _ in range(20):
        lowered = stripped.lower()
        if lowered.startswith("<?"):
            end = stripped.find("?>")
            if end == -1:
                return stripped
            stripped = stripped[end + 2 :].lstrip()
            continue
        if lowered.startswith("<!--"):
            end = stripped.find("-->")
            if end == -1:
                return stripped
            stripped = stripped[end + 3 :].lstrip()
            continue
        if lowered.startswith("<!doctype"):
            end = stripped.find(">")
            if end == -1:
                return stripped
            stripped = stripped[end + 1 :].lstrip()
            continue
        break
    return stripped
