# scrapeit

Point at a web page. Get structured items.

It drives **your own already-logged-in Chrome** over CDP — no cloud browser, no
managed sessions, no headless farm. Two LLMs do two different jobs: one explores
the page and learns its structure, the other fills in the fields you asked for.
Then it POSTs the result wherever you want.

```
fetch  →  scrape (LLM #1)  →  extract (LLM #2)  →  exit (POST)
 CDP       explores the DOM     fills your data      to your endpoint
           extracts the items     model
```

## Why two LLMs

They do genuinely different work and want different models:

| | LLM #1 — scrape | LLM #2 — extract |
|---|---|---|
| job | drive the browser, find the items | fill your fields from each item |
| needs | reliable tool-calling | good structured output |
| typical | a cheap fast model | a smarter model |
| cost | only on the first run per source | once per new item |

## Let a coding agent set it up

Copy the block below into Claude Code, Cursor, Codex, or any other coding agent.
Replace the `<angle bracket>` parts first. It works because the agent reads
[`AGENTS.md`](./AGENTS.md), which tells it the three config surfaces, the module
map, and the rules it must not break.

```text
Clone https://github.com/papi656/scrapeit and read AGENTS.md and README.md before
changing anything. Set it up: create .env from .env.example for <my LLM provider,
model names, and API keys>.

Scrape <the sites I care about> — add each to sources.yaml as id + url, nothing else.
Rewrite data_model.yaml so the extractor returns <the fields I want>.

Run `uv run python -m scrapeit --count 5 --no-post` to prove scraping works, then show
me a few extracted rows before we set SINK_ENDPOINT_URL.
```

## Install

Requires Python 3.11+, [`uv`](https://docs.astral.sh/uv/) (or pip), and Chrome.

```bash
git clone https://github.com/papi656/scrapeit
cd scrapeit
uv sync
```

## Configure — three files, nothing else

### 1. `.env` — the LLMs and the exit path

```bash
cp .env.example .env
```

Fill in **two** models and, optionally, where to POST:

```env
LLM_SCRAPE_BASE_URL=https://api.openai.com/v1
LLM_SCRAPE_API_KEY=sk-...
LLM_SCRAPE_MODEL=gpt-4o-mini        # drives the browser

LLM_EXTRACT_MODEL=gpt-4o            # blank fields fall back to the scrape values
LLM_EXTRACT_API_KEY=

SINK_ENDPOINT_URL=https://your-api.example.com/questions
SINK_AUTH_TOKEN=...
```

Leave `SINK_ENDPOINT_URL` empty and nothing is POSTed — items are still written
to `_staging/`.

### 2. `sources.yaml` — where to mine

```yaml
- id: reddit:r/InterviewCoderHQ
  url: https://www.reddit.com/r/InterviewCoderHQ/
  sort_urls:
    latest: https://www.reddit.com/r/InterviewCoderHQ/new/
    hottest: https://www.reddit.com/r/InterviewCoderHQ/hot/

- id: example:blog
  url: https://example.com/blog      # no sort_urls needed for a single-URL source
```

`id` and `url` are the only required fields. `--sort` and `--count` are
**run-level** arguments, not per-source settings.

### 3. `data_model.yaml` — the shape you want out

```yaml
name: interview_question
fields:
  - name: question
    type: string
    required: true
    description: The question as asked, verbatim.
  - name: role_level
    type: enum
    values: [intern, new_grad, mid, senior, staff, unknown]
```

Types: `string` `int` `float` `bool` `date` `enum` `list` `object`.
This file generates the extraction prompt *and* validates the response, so
there is one definition and no drift.

## Run

```bash
uv run python -m scrapeit                          # latest, cap 10
uv run python -m scrapeit --sort hottest
uv run python -m scrapeit --count 25
uv run python -m scrapeit --no-post                # extract, stage, don't POST
uv run python -m scrapeit --no-extract             # raw items only
uv run python -m scrapeit --sources other.yaml --data-model other_model.yaml
```

Make sure Chrome is running and logged in to whatever sites you're scraping. A
silently expired cookie is the classic failure — scrapeit reports it rather than
trying to bypass it.

## What you get

```
_staging/     raw items + extracted rows per run (a retry buffer)
runs/         one JSON record per source per run (turns, tokens, wall time)
```

Posting is retried with exponential backoff. A 4xx is treated as "you sent
something wrong" and is not retried; 429, 5xx, and network errors are.

## Adding a source

Add `id` + `url` to `sources.yaml` and run it. No per-site code, ever.

## Design notes

- **No CSS selectors, no class names, anywhere.** Modern sites ship build-hashed
  classes (`jsx-4620f2a986d79272`) that change on every deploy. Anchor to
  structure and roles instead, so anything learned survives class renaming.
- **Missing beats guessed.** Every field except `title` and `url` is nullable.
  The extraction model is told to omit rather than invent.
- **Failure is per-source.** One dead source never costs the run; errors are
  collected and reported at the end.
- **`count` is a cap, not a quota.** It never paginates to fill a number.

## Status

Every run explores the page with LLM #1 — nothing is cached or learned yet, so
run 2 costs the same as run 1. A replay path that would avoid re-exploring a page
was designed and then removed on purpose; it is **not built**. That is the main
piece of future work.

## License

MIT
