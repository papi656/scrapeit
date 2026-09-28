"""Data shapes shared across scrapeit."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Item:
    """One piece of content read from a source.

    ``title`` and ``url`` are required; everything else is nullable because
    missing beats guessed.
    """

    title: str
    url: str
    body: str = ""
    published_at: str | None = None
    author: str | None = None
    score: int | None = None
    comments: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Source:
    """A place to mine. Carries no count or sort - those are run-level."""

    id: str
    url: str
    sort_urls: dict[str, str] = field(default_factory=dict)

    def url_for_sort(self, sort: str) -> str:
        """Resolve a run-level sort to a concrete URL.

        Falls back to the base ``url`` when the source declares no map for this
        sort, so a single-URL source (no "latest"/"hottest" variants) still
        works. Raises only if a sort map exists but does not contain the key.
        """
        if not self.sort_urls:
            return self.url
        try:
            return self.sort_urls[sort]
        except KeyError:
            raise KeyError(
                f"source {self.id!r} has no URL for sort {sort!r} "
                f"(known: {sorted(self.sort_urls)})"
            ) from None


@dataclass
class RunRecord:
    """One record per source per run. The evaluation substrate."""

    source_id: str
    started_at: str
    mode: str
    sort: str
    count_cap: int
    items_returned: int = 0
    items_extracted: int = 0
    turns: int = 0
    llm_calls: int = 0
    tokens: int = 0
    extract_llm_calls: int = 0
    extract_tokens: int = 0
    posted: bool = False
    wall_seconds: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
