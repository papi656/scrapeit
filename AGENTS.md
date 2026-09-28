# AGENTS.md — working on scrapeit

If you are an AI agent asked to use, extend, or debug this repo, read this first.
It tells you the three places configuration lives, the pipeline, and the rules
that must not be broken.

## The three customization surfaces

Everything a user changes lives in exactly three files. Do not add a fourth
without a very good reason.

| file | what it controls | format |
|---|---|---|
| `.env` | LLM #1 (scrape), LLM #2 (extract), exit endpoint + auth | dotenv, see `.env.example` |
| `sources.yaml` | where to mine | list of `{id, url, sort_urls?, recipe?}` |
| `data_model.yaml` | the fields the extractor fills | `{name, description, fields[]}` |

If a task is "make it scrape X" — that is `sources.yaml`.
If a task is "change what comes out" — that is `data_model.yaml`.
If a task is "use a different model / POST somewhere else" — that is `.env`.
Only touch `scrapeit/*.py` when the user wants a new *behaviour*, not new config.

## Pipeline

```
runner.run_all
  └─ per source (sequential, failure-isolated)
       fetcher.open_page(url)            # CDP into the user's logged-in Chrome
       reader.read(page, source, cap)    # LLM #1: explore, return items + recipe
       sink.write_staging(...)           # raw items  -> _staging/
       sink.write_recipe(...)            # recipe     -> recipes/<id>/recipe.json
       extract.extract(items, model)     # LLM #2: fill data_model.yaml
       sink.write_extracted(...)         # rows       -> _staging/
       sink.post(...)                    # rows       -> SINK_ENDPOINT_URL
       sink.write_run_record(...)        # metrics    -> runs/
```

Module responsibilities:

| module | owns | must never |
|---|---|---|
| `config.py` | reading `.env` | do I/O beyond the environment |
| `models.py` | `Item`, `Source`, `RunRecord` | know about HTTP or LLMs |
| `sources.py` | loading/validating `sources.yaml` | fetch anything |
| `datamodel.py` | loading `data_model.yaml`, building the extraction prompt, validating rows | call an LLM |
| `fetcher.py` | CDP + scrolling | contain any LLM call |
| `reader.py` | LLM #1 exploration + recipe emission | touch Chrome except through `fetcher` |
| `extract.py` | LLM #2 field filling | fetch or scrape |
| `sink.py` | staging files, recipe files, run records, the POST | parse HTML |
| `runner.py` | orchestration + per-source error isolation | contain prompt text |

## Hard rules

1. **No CSS selectors and no class names in recipes.** Ever. Build-hashed
   classes change on every deploy. Use the semantic finder vocabulary that
   `reader.SYSTEM_PROMPT` defines: `first_heading`, `first_link`,
   `first_link_href`, `text_matching`, `attr`, `largest_text_block`, `nth_text`.
2. **No LLM in `fetcher.py`.** It is deterministic by design.
3. **A bad recipe is worse than no recipe.** If a run yields 0 items, discard the
   recipe rather than saving it (`reader.read` already enforces this).
4. **Missing beats guessed.** Nullable fields stay null; the extractor is told to
   omit rather than invent. Do not add "fallback" values.
5. **Failure is per-source.** A crash in one source must be recorded in its
   `RunRecord` and must not stop the run. `run_source` never raises.
6. **`count` is a cap, not a quota.** Never paginate to fill a number.
7. **Never retry a 4xx** other than 429 — the payload is wrong, not the network.
8. **Never commit `.env`.** It is gitignored; keep it that way.

## Common tasks

**Add a source.** Append to `sources.yaml`:
```yaml
- id: some:site
  url: https://some.site/page
```
Then `uv run python -m scrapeit --sources sources.yaml`. No code changes.

**Change the output fields.** Edit `data_model.yaml`. The extraction prompt is
generated from it by `datamodel.DataModel.prompt_block()`; validation comes from
`datamodel.validate()`. Keep types loose — this is a prompt, not a schema.

**Point it at a different API.** Set `SINK_ENDPOINT_URL` +
`SINK_AUTH_HEADER`/`SINK_AUTH_SCHEME`/`SINK_AUTH_TOKEN` in `.env`. `SINK_MODE=batch`
sends one request per source; `SINK_MODE=item` sends one per row.

**Debug a failing source.** Read `runs/<slug>__<timestamp>.json` — it has the
error, turn count, token count, and wall time. Recipes and staging payloads
live under `recipes/` and `_staging/`.

## Known gaps (do not pretend these work)

- `RecipeReader` — deterministic **replay** of a saved recipe — is **not built**.
  Every run currently pays full LLM #1 cost. `runner._read()` is the seam where a
  replay reader belongs. The recipe format is fixed in `reader.SYSTEM_PROMPT`.
- `sort_urls` are currently hand-written. They are intended to be discovered by
  the first scrape run and written back to `sources.yaml`; that is not done yet.
- Extraction runs one LLM call per item. Batching is possible if cost demands it.
- No tests yet. The cheapest useful test: replay a stored recipe offline.
