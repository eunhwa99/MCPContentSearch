import math


class ContentSearchError(Exception):
    """Base exception for content search application"""
    pass


class IndexingError(ContentSearchError):
    """Indexing related errors"""
    pass


class FetchError(ContentSearchError):
    """Content fetching errors"""
    pass


class SearchError(ContentSearchError):
    """Search operation errors"""
    pass


class ParsingError(ContentSearchError):
    """Supported local document could not be parsed safely."""

    pass


class CareerManifestParsingError(ParsingError):
    """Career snapshot parse failure with bounded, content-free progress."""

    def __init__(
        self,
        message: str,
        *,
        attempted_documents: int,
        completed_documents: int,
        parsing_latency_ms: float,
    ):
        valid_counts = (
            not isinstance(attempted_documents, bool)
            and isinstance(attempted_documents, int)
            and attempted_documents >= 0
            and not isinstance(completed_documents, bool)
            and isinstance(completed_documents, int)
            and 0 <= completed_documents <= attempted_documents
        )
        valid_latency = (
            not isinstance(parsing_latency_ms, bool)
            and isinstance(parsing_latency_ms, (int, float))
            and math.isfinite(parsing_latency_ms)
            and parsing_latency_ms >= 0
        )
        if not valid_counts or not valid_latency:
            raise ValueError("Invalid career manifest parsing progress")
        self.attempted_documents = attempted_documents
        self.completed_documents = completed_documents
        self.parsing_latency_ms = float(parsing_latency_ms)
        super().__init__(message)


class EvidenceSearchError(SearchError):
    """Base error for the additive extractive evidence contract."""

    error_type = "evidence_error"

    def __init__(self, message: str, *, error_type: str | None = None):
        if error_type:
            self.error_type = error_type
        super().__init__(message)


class InvalidEvidenceRequestError(EvidenceSearchError):
    error_type = "invalid_request"


class EvidenceRetrievalError(EvidenceSearchError):
    error_type = "internal_error"


class ConfigurationError(ContentSearchError):
    """Configuration related errors"""
    pass


class APIError(FetchError):
    """External API errors"""
    def __init__(self, service: str, status_code: int, message: str):
        self.service = service
        self.status_code = status_code
        super().__init__(f"{service} API error (HTTP {status_code}): {message}")
