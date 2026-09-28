"""The Reader seam.

`read(page, source, count_cap)` returns Items. v1 has one implementation:
ExploreReader, an LLM tool-calling loop. RecipeReader (deterministic replay)
is deferred; the recipe format is fixed now so ExploreReader can emit it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import OpenAI

from .config import llm_config
from .fetcher import Page, inspect, scroll_once
from .models import Item, Source

# Bounded so a confused model cannot spend unbounded money. This budget is
# also the metric (spec: Metrics).
MAX_TURNS = 15
TOOL_RESULT_CHAR_CAP = 4000
SEED_TEXT_CHAR_CAP = 1200


class ReadError(RuntimeError):
    """Exploration failed. No items are returned and no recipe is written."""


@dataclass
class ReadResult:
    items: list[Item]
    recipe: dict[str, Any] | None
    turns: int
    llm_calls: int
    tokens: int
    errors: list[str] = field(default_factory=list)


class Reader(Protocol):
    replay: bool

    def read(self, page: Page, source: Source, count_cap: int) -> ReadResult: ...


SYSTEM_PROMPT = """You read a web page and extract the list of content items it shows.

The page has already been loaded in a browser. You cannot click or navigate. You have three tools:

- inspect(js_expr): evaluate a JavaScript expression in the page and see the result.
  Use this to test hypotheses about the page structure. Prefer expressions that return
  JSON.stringify(...) of a small, summarised result - never dump the whole DOM.
- scroll(): scroll down one screen. Budgeted; you cannot scroll much.
- finish(items, recipe): call this when you have the items. This ends the task.

TARGET: the feed of content items on this page, most prominent first.

Rules for `items` - each item is an object with these fields:
  title         (required) the item's headline text
  url           (required) absolute link to the item
  body          (optional) the item's full text, "" if there is none
  published_at  (optional) ISO date if parseable, else the raw string as shown
  author        (optional)
  score         (optional) integer upvotes/points
  comments      (optional) integer comment count
  extras        (optional) object for anything that does not fit above

  Omit a field rather than guessing it. Never invent values.

Rules for `recipe` - how to find these items again WITHOUT an LLM. It must use only
this vocabulary, mechanically executable:

  {"source_id": str, "version": 1,
   "steps": [
     {"op": "load"},
     {"op": "select_group",
      "match": {"repeating": true, "each_contains": ["heading", "link"], "min_count": 1}},
     {"op": "extract", "fields": {"<item field>": {"find": "<finder>"}}},
     {"op": "paginate", "kind": "none"}
   ]}

  Allowed finders. Use these EXACT shapes - the argument names are part of the
  contract and anything else cannot be replayed:
    {"find": "first_heading"}
    {"find": "first_link"}
    {"find": "first_link_href"}
    {"find": "text_matching", "pattern": "<regex>"}
    {"find": "attr", "name": "<attribute name>"}
    {"find": "largest_text_block"}
    {"find": "nth_text", "index": <integer>}

  Example field entry: "author": {"find": "attr", "name": "author"}

  NEVER use CSS class names or CSS selectors. Class names on modern sites are
  build-hashed and change on every deploy. Anchor to structure and roles only.

When you are confident, call finish. If you cannot identify the items, still call
finish with an empty items list and a short explanation in `extras` of the first item.
"""


def _client() -> OpenAI:
    cfg = llm_config("scrape")
    return OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)


def _model() -> str:
    return llm_config("scrape").model


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "inspect",
            "description": "Evaluate a JavaScript expression in the loaded page and return its value.",
            "parameters": {
                "type": "object",
                "properties": {
                    "js_expr": {
                        "type": "string",
                        "description": (
                            "A JS expression, e.g. \"document.title\" or "
                            "\"JSON.stringify(Array.from(document.querySelectorAll('h3')).slice(0,5).map(h=>h.textContent))\""
                        ),
                    }
                },
                "required": ["js_expr"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll the page down one screen. Budgeted to a few calls.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Finish the task with the extracted items and a replay recipe.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "url": {"type": "string"},
                                "body": {"type": "string"},
                                "published_at": {"type": "string"},
                                "author": {"type": "string"},
                                "score": {"type": "integer"},
                                "comments": {"type": "integer"},
                                "extras": {"type": "object"},
                            },
                            "required": ["title", "url"],
                        },
                    },
                    "recipe": {"type": "object"},
                },
                "required": ["items", "recipe"],
            },
        },
    },
]


def _seed(source: Source, page: Page, count_cap: int) -> str:
    """Give the model a cheap first look so it does not spend turns orienting."""

    def safe(expr: str, default: Any = "") -> Any:
        try:
            return inspect(expr)
        except Exception as exc:  # a broken probe must not kill the run
            return f"<probe failed: {type(exc).__name__}>"

    text = safe("document.body.innerText.slice(0, %d)" % SEED_TEXT_CHAR_CAP, "")
    counts = safe(
        "JSON.stringify({anchors:document.querySelectorAll('a').length,"
        "headings:document.querySelectorAll('h1,h2,h3,h4,h5,h6').length,"
        "lists:document.querySelectorAll('ul,ol').length,"
        "articleish:document.querySelectorAll('article,[role=article]').length})",
        "{}",
    )
    return (
        f"SOURCE: {source.id}\n"
        f"URL: {page.url}\n"
        f"TITLE: {page.title}\n"
        f"WANT AT MOST: {count_cap} items\n\n"
        f"ELEMENT COUNTS: {counts}\n\n"
        f"PAGE TEXT (first {SEED_TEXT_CHAR_CAP} chars):\n{text}\n\n"
        "Find the content items. Test the DOM with inspect, then call finish."
    )


def _render(value: Any) -> str:
    if isinstance(value, str):
        out = value
    else:
        out = json.dumps(value, default=str)
    if len(out) > TOOL_RESULT_CHAR_CAP:
        out = out[:TOOL_RESULT_CHAR_CAP] + f"... [truncated; {len(out)} chars total]"
    return out


def _run_tool(name: str, args: dict[str, Any], page: Page) -> tuple[str, ReadResult | None]:
    """Execute one tool call. Returns (result_text, final_result_or_None)."""
    if name == "inspect":
        try:
            return _render(inspect(args.get("js_expr", ""))), None
        except Exception as exc:
            return f"ERROR: {type(exc).__name__}: {exc}", None

    if name == "scroll":
        return scroll_once(page), None

    if name == "finish":
        raw_items = args.get("items") or []
        items: list[Item] = []
        errors: list[str] = []
        for i, ri in enumerate(raw_items):
            if not isinstance(ri, dict) or not ri.get("title") or not ri.get("url"):
                errors.append(f"item {i} dropped: title and url are required")
                continue
            items.append(
                Item(
                    title=str(ri["title"]),
                    url=str(ri["url"]),
                    body=str(ri.get("body") or ""),
                    published_at=ri.get("published_at"),
                    author=ri.get("author"),
                    score=ri.get("score"),
                    comments=ri.get("comments"),
                    extras=ri.get("extras") or {},
                )
            )
        result = ReadResult(
            items=items,  # count_cap is applied by the caller
            recipe=args.get("recipe"),
            turns=0,
            llm_calls=0,
            tokens=0,
            errors=errors,
        )
        return f"accepted {len(items)} items", result

    return f"ERROR: unknown tool {name!r}", None


def read(page: Page, source: Source, count_cap: int) -> ReadResult:
    """Explore `page` and return up to `count_cap` items plus a candidate recipe."""
    client = _client()
    model = _model()

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _seed(source, page, count_cap)},
    ]

    turns = llm_calls = tokens = 0

    for _ in range(MAX_TURNS):
        response = client.chat.completions.create(
            model=model, messages=messages, tools=TOOLS, tool_choice="auto"
        )
        llm_calls += 1
        turns += 1
        if response.usage and response.usage.total_tokens:
            tokens += response.usage.total_tokens

        msg = response.choices[0].message
        calls = msg.tool_calls or []

        assistant_msg: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.function.name, "arguments": c.function.arguments},
                }
                for c in calls
            ]
        messages.append(assistant_msg)

        if not calls:
            messages.append(
                {"role": "user", "content": "Use a tool: inspect, scroll, or finish."}
            )
            continue

        for call in calls:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": f"ERROR: arguments were not valid JSON ({exc})",
                    }
                )
                continue

            text, final = _run_tool(call.function.name, args, page)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": text})

            if final is not None:
                final.turns = turns
                final.llm_calls = llm_calls
                final.tokens = tokens
                final.items = final.items[:count_cap]
                # Spec: a bad recipe is worse than none. Zero items means the
                # structure was not understood, so any recipe emitted here is
                # invented. Observed in practice: on about:blank the model
                # returned 0 items with a confident fabricated recipe.
                final.recipe = _stamp_recipe(final.recipe, source) if final.items else None
                return final

    raise ReadError(
        f"exploration exceeded {MAX_TURNS} turns for {source.id} "
        f"({llm_calls} llm calls, {tokens} tokens)"
    )


def _stamp_recipe(recipe: dict[str, Any] | None, source: Source) -> dict[str, Any] | None:
    """Attach identity the model does not own."""
    if not recipe:
        return None
    return {**recipe, "source_id": source.id, "version": 1}
