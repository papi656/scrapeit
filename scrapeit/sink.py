"""Where scraped content goes.

Writes local staging files (a retry buffer: everything needed to re-POST without
re-scraping), then -- if an endpoint is configured -- POSTs the extracted rows.

The exit path is configured entirely by environment variables; see
:func:`scrapeit.config.sink_config`.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import SinkConfig
from .models import RunRecord, Source
from .reader import ReadResult

STAGING_DIR = Path("_staging")
RUNS_DIR = Path("runs")


def _slug(source_id: str) -> str:
    return source_id.replace("/", "_").replace(":", "__")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_staging(source: Source, sort: str, result: ReadResult) -> Path:
    """Persist the raw items for one source run."""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    path = STAGING_DIR / f"{_slug(source.id)}__{sort}__{_stamp()}.json"

    payload = {
        "source_id": source.id,
        "source_url": source.url,
        "sort": sort,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "items": [i.to_dict() for i in result.items],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def write_extracted(source: Source, sort: str, model_name: str, rows: list[dict[str, Any]]) -> Path:
    """Persist the extraction output, independent of whether the POST succeeds."""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    path = STAGING_DIR / f"{_slug(source.id)}__{sort}__{_stamp()}__extracted.json"
    payload = {
        "source_id": source.id,
        "data_model": model_name,
        "sort": sort,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "items": rows,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def write_run_record(record: RunRecord) -> Path:
    path = RUNS_DIR / f"{_slug(record.source_id)}__{record.started_at.replace(':', '')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.to_dict(), indent=2, ensure_ascii=False))
    return path


def _headers(cfg: SinkConfig) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if cfg.auth_token:
        value = f"{cfg.auth_scheme} {cfg.auth_token}".strip() if cfg.auth_scheme else cfg.auth_token
        headers[cfg.auth_header] = value
    return headers


def _payload(source: Source, sort: str, model_name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "data_model": model_name,
        "source": {"id": source.id, "url": source.url, "sort": sort},
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "count": len(rows),
        "items": rows,
    }


class PermanentPostError(RuntimeError):
    """The endpoint rejected the payload. Retrying will not help."""


def _post_once(cfg: SinkConfig, body: dict[str, Any]) -> None:
    """One attempt. Raises on failure. Raises :class:`PermanentPostError` on 4xx."""
    response = httpx.post(
        cfg.endpoint_url,  # type: ignore[arg-type]
        headers=_headers(cfg),
        json=body,
        timeout=cfg.timeout,
    )
    # 4xx means we sent something wrong -- retrying will not help, except 429.
    if 400 <= response.status_code < 500 and response.status_code != 429:
        raise PermanentPostError(
            f"endpoint rejected the payload: HTTP {response.status_code} {response.text[:200]}"
        )
    response.raise_for_status()


def post(source: Source, sort: str, model_name: str, rows: list[dict[str, Any]], cfg: SinkConfig) -> tuple[bool, str | None]:
    """POST the extracted rows. Returns ``(posted, error)``.

    ``batch`` sends one request per source; ``item`` sends one per row. Retries
    with exponential backoff on transient failures.
    """
    if not cfg.endpoint_url or not rows:
        return False, None

    batches = [rows] if cfg.mode == "batch" else [[row] for row in rows]

    for batch in batches:
        body = _payload(source, sort, model_name, batch)
        last_error: str | None = None
        for attempt in range(cfg.max_retries + 1):
            try:
                _post_once(cfg, body)
                last_error = None
                break
            except PermanentPostError as exc:
                # Do not retry: the payload or the auth is wrong.
                return False, str(exc)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < cfg.max_retries:
                    time.sleep(min(2**attempt, 10))
        if last_error:
            return False, f"POST failed after {cfg.max_retries + 1} attempts: {last_error}"

    return True, None
