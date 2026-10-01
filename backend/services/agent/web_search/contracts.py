"""Typed contract shared by the web-search handler and its providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SearchSource:
    title: str
    url: str
    site_name: str = ""
    snippet: str = ""
    published_at: str = ""
    citation_spans: tuple[tuple[int, int], ...] = ()

    def as_dict(self, source_id: str) -> dict[str, Any]:
        return {
            "id": source_id,
            "title": self.title,
            "url": self.url,
            "site_name": self.site_name,
            "snippet": self.snippet,
            "published_at": self.published_at,
            "citation_spans": [list(span) for span in self.citation_spans],
        }


@dataclass(frozen=True)
class SearchResponse:
    answer: str
    sources: tuple[SearchSource, ...] = ()
    search_queries: tuple[str, ...] = ()
    provider: str = ""
    search_requests: int = 0
    status: str = "success"
    status_reason: str = ""
    sources_truncated: bool = False
    usage: dict[str, Any] = field(default_factory=dict)


class SearchProviderError(RuntimeError):
    """A provider failed to perform a trustworthy search request."""

    def __init__(self, message: str, *, retryable: bool = False, status: str = "error") -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
