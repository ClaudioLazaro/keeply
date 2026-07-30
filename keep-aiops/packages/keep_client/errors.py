"""Typed errors raised by the Keep API client."""


class KeepClientError(Exception):
    """Base error for keep_client (transport or API failures)."""


class KeepApiError(KeepClientError):
    """Non-2xx response from the Keep API."""

    def __init__(self, status_code: int, detail: str, method: str = "", url: str = "") -> None:
        self.status_code = status_code
        self.detail = detail
        self.method = method
        self.url = url
        super().__init__(f"Keep API {method} {url} failed with {status_code}: {detail}")


class KeepNotFoundError(KeepApiError):
    """404 from the Keep API (incident not found / not visible to this key)."""
