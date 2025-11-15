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


class ConfigurationError(ContentSearchError):
    """Configuration related errors"""
    pass


class APIError(FetchError):
    """External API errors"""
    def __init__(self, service: str, status_code: int, message: str):
        self.service = service
        self.status_code = status_code
        super().__init__(f"{service} API error (HTTP {status_code}): {message}")
