"""Stage 3: the extraction LLM.

Takes the raw :class:`~scrapeit.models.Item` objects that the scrape model found
and fills the fields defined in ``data_model.yaml``. One call per item -- item
bodies can be long, and a batch call would either truncate them or blow the
context window.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from .config import LLMConfig, llm_config
from .datamodel import DataModel, validate
from .models import Item

SYSTEM_PROMPT = """You extract structured data from web content.

You are given one content item and a set of fields to fill. Return a single JSON
object with exactly those field names.

Rules:
- Fill a field only from the text you were given. Never invent, infer, or
  embellish a value.
- Omit a field entirely rather than guessing or writing a placeholder.
- For `enum` fields, use one of the listed values verbatim.
- For `list` fields, return a JSON array of strings.
- Return the JSON object and nothing else. No prose, no code fences.
"""


@dataclass
class ExtractResult:
    rows: list[dict[str, Any]]
    errors: list[str] = field(default_factory=list)
    llm_calls: int = 0
    tokens: int = 0


def _client(cfg: LLMConfig) -> OpenAI:
    return OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)


def _item_prompt(model: DataModel, item: Item) -> str:
    header = ""
    if model.description:
        header = f"WHAT THIS IS: {model.description}\n\n"
    return (
        f"{header}FIELDS TO FILL:\n{model.prompt_block()}\n\n"
        f"CONTENT ITEM:\n"
        f"title: {item.title}\n"
        f"url: {item.url}\n"
        f"author: {item.author or ''}\n"
        f"published_at: {item.published_at or ''}\n"
        f"body:\n{item.body or '(no body text)'}\n"
    )


def _parse_json(text: str) -> dict[str, Any]:
    """Parse a model response that should be JSON but might be fenced."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    try:
        parsed = json.loads(text.strip())
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def extract(items: list[Item], model: DataModel) -> ExtractResult:
    """Fill ``model`` from each item. Never raises -- per-item failures are recorded."""
    cfg = llm_config("extract")
    client = _client(cfg)

    result = ExtractResult(rows=[])
    for i, item in enumerate(items):
        try:
            response = client.chat.completions.create(
                model=cfg.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _item_prompt(model, item)},
                ],
                temperature=0,
            )
        except Exception as exc:
            result.errors.append(f"item {i}: extract call failed: {type(exc).__name__}: {exc}")
            continue

        result.llm_calls += 1
        if response.usage and response.usage.total_tokens:
            result.tokens += response.usage.total_tokens

        content = response.choices[0].message.content or ""
        try:
            payload = _parse_json(content)
        except (json.JSONDecodeError, ValueError) as exc:
            result.errors.append(f"item {i}: extract returned unparseable JSON ({exc})")
            continue

        row, problems = validate(model, payload)
        result.errors.extend(f"item {i}: {p}" for p in problems)
        if row:
            # Keep provenance the model does not own.
            row.setdefault("_source_url", item.url)
            result.rows.append(row)

    return result
