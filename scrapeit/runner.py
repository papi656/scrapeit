"""Walk the source list. Sequential, failure-isolated.

    fetch  -> scrape (LLM #1)  -> extract (LLM #2)  -> exit (POST)
"""

from __future__ import annotations

import time
import traceback
from datetime import datetime, timezone

from . import extract as extract_module
from .config import DEFAULT_DATA_MODEL_PATH, DEFAULT_SOURCES_PATH, sink_config
from .datamodel import DataModel, load_data_model
from .fetcher import FetchError, close_page, open_page
from .models import RunRecord, Source
from .reader import ReadError
from .sink import post, write_extracted, write_recipe, write_run_record, write_staging
from .sources import load_sources

MODE = "explore"


def run_source(
    source: Source,
    sort: str,
    count_cap: int,
    data_model: DataModel | None,
    *,
    do_extract: bool = True,
    do_post: bool = True,
) -> RunRecord:
    """Scrape one source. Never raises - failures land in the record."""
    record = RunRecord(
        source_id=source.id,
        started_at=datetime.now(timezone.utc).isoformat(),
        mode=MODE,
        sort=sort,
        count_cap=count_cap,
    )
    started = time.time()

    # Resolved outside the broad handler: a missing sort URL is an expected
    # failure, not a bug.
    try:
        url = source.url_for_sort(sort)
    except KeyError as exc:
        record.error = f"unsupported sort: {exc}"
        record.wall_seconds = round(time.time() - started, 2)
        return record

    page = None
    try:
        page = open_page(url)
        result = _read(page, source, count_cap)

        write_staging(source, sort, result)
        record.items_returned = len(result.items)
        record.turns = result.turns
        record.llm_calls = result.llm_calls
        record.tokens = result.tokens
        record.recipe_written = str(write_recipe(source, result.recipe) or "") or None

        errors: list[str] = list(result.errors)
        if not result.items:
            errors.append("0 items extracted - possible login wall or unreadable structure")

        rows: list[dict] = []
        if do_extract and result.items and data_model is not None:
            extracted = extract_module.extract(result.items, data_model)
            record.extract_llm_calls = extracted.llm_calls
            record.extract_tokens = extracted.tokens
            record.items_extracted = len(extracted.rows)
            errors.extend(extracted.errors)
            rows = extracted.rows
            write_extracted(source, sort, data_model.name, rows)
            if not rows:
                errors.append("extraction produced 0 rows")

        if do_post and rows:
            cfg = sink_config()
            posted, post_error = post(source, sort, data_model.name if data_model else "", rows, cfg)
            record.posted = posted
            if post_error:
                errors.append(post_error)

        record.error = "; ".join(errors) if errors else None
    except FetchError as exc:
        record.error = f"FetchError: {exc}"
    except ReadError as exc:
        record.error = f"ReadError: {exc}"
    except Exception as exc:
        record.error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        if page is not None:
            close_page(page)
        record.wall_seconds = round(time.time() - started, 2)
    return record


def _read(page, source: Source, count_cap: int):
    """The scrape stage. Kept as its own function so a replay reader can slot in here."""
    from . import reader as reader_module

    return reader_module.read(page, source, count_cap)


def run_all(
    sort: str,
    count_cap: int,
    sources_path: str = DEFAULT_SOURCES_PATH,
    data_model_path: str = DEFAULT_DATA_MODEL_PATH,
    *,
    do_extract: bool = True,
    do_post: bool = True,
) -> list[RunRecord]:
    """Run every source. One failure must not cost the run."""
    sources = load_sources(sources_path)
    data_model = load_data_model(data_model_path) if do_extract else None
    cfg = sink_config()
    if do_post and not cfg.endpoint_url:
        print("[sink] SINK_ENDPOINT_URL is not set - extracted rows will be staged but not POSTed")

    records: list[RunRecord] = []
    for source in sources:
        record = run_source(
            source, sort, count_cap, data_model, do_extract=do_extract, do_post=do_post
        )
        write_run_record(record)
        records.append(record)
        status = "OK" if not record.error else f"FAILED ({record.error})"
        print(
            f"[{record.source_id}] {status} - {record.items_returned} items, "
            f"{record.items_extracted} extracted, {record.turns} turns, "
            f"{record.wall_seconds}s"
        )
    return records
