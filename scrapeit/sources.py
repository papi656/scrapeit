"""Load and validate the source list."""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import Source

DEFAULT_PATH = Path("sources.yaml")


class SourceError(ValueError):
    """The source list is malformed."""


def load_sources(path: str | Path = DEFAULT_PATH) -> list[Source]:
    p = Path(path)
    if not p.exists():
        raise SourceError(f"source list not found: {p}")

    raw = yaml.safe_load(p.read_text()) or []
    if not isinstance(raw, list):
        raise SourceError(f"{p} must contain a list of sources")

    sources: list[Source] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise SourceError(f"{p}: entry {i} is not a mapping")
        for key in ("id", "url"):
            if not entry.get(key):
                raise SourceError(f"{p}: entry {i} is missing {key!r}")
        if entry["id"] in seen:
            raise SourceError(f"{p}: duplicate source id {entry['id']!r}")
        seen.add(entry["id"])
        sources.append(
            Source(
                id=entry["id"],
                url=entry["url"],
                sort_urls=entry.get("sort_urls") or {},
                recipe=entry.get("recipe"),
            )
        )
    return sources
