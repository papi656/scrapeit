"""Deterministic page fetching over CDP. No LLM belongs in this module.

Wraps the browser_harness library, which drives the user's already-logged-in
Chrome. Sessions must stay logged in; a silently expired cookie is the known
failure mode (spec: Failure Handling).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from browser_harness import helpers as H
from browser_harness.admin import ensure_daemon

# Bounded by design: both validated sources deliver >10 items in the initial
# DOM, so scrolling is a fallback, never the mechanism (spec: Fetcher).
MAX_SCROLLS = 3
SCROLL_DY = 1200


class FetchError(RuntimeError):
    """The page could not be loaded or read."""


@dataclass
class Page:
    """A loaded tab. Carries its own scroll budget."""

    url: str
    target_id: str
    title: str = ""
    scrolls_used: int = 0
    console: list[str] = field(default_factory=list)

    @property
    def scrolls_remaining(self) -> int:
        return max(0, MAX_SCROLLS - self.scrolls_used)


def open_page(url: str, settle_seconds: float = 1.0) -> Page:
    """Attach to Chrome, open `url` in a new tab, and wait for it to settle."""
    ensure_daemon()
    H.new_tab(url)
    H.wait_for_load()
    try:
        H.wait_for_network_idle(timeout=10.0, idle_ms=600)
    except Exception:
        # Network idle is a nicety; a chatty page should not fail the run.
        pass
    time.sleep(settle_seconds)

    tab = H.current_tab()
    info = H.page_info()
    if "dialog" in info:
        raise FetchError(
            f"native dialog open on {url}: {info['dialog']} - "
            "dismiss it in the browser and re-run"
        )
    return Page(
        url=tab.get("url", url), target_id=tab["targetId"], title=info.get("title", "")
    )


def inspect(expr: str) -> Any:
    """Evaluate a JS expression in the attached tab and return its decoded value."""
    return H.js(expr)


def scroll_once(page: Page, dy: int = SCROLL_DY) -> str:
    """Scroll the viewport down by one screen.

    Returns a short human-readable string, because the result is fed straight
    back to the LLM as a tool result. Enforces the budget so the model cannot
    scroll forever.
    """
    if page.scrolls_remaining <= 0:
        return f"scroll budget exhausted ({MAX_SCROLLS} used); call finish with what you have"

    info = H.page_info()
    if "dialog" in info:
        return f"cannot scroll: native dialog open ({info['dialog']})"

    x, y = int(info["w"]) // 2, int(info["h"]) // 2
    H.scroll(x, y, dy=dy)
    page.scrolls_used += 1
    time.sleep(0.8)
    after = H.page_info()
    return (
        f"scrolled down {dy}px; scrollY={after.get('sy')} "
        f"of {after.get('ph')}; {page.scrolls_remaining} scrolls remaining"
    )


def close_page(page: Page) -> None:
    """Close the tab. Never raises - cleanup must not fail a run."""
    try:
        H.close_tab(page.target_id)
    except Exception:
        pass
