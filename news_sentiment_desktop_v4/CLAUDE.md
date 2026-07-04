# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Note on repo state**: `app/` and `tests/` were reconstructed from the README on 2026-07-04
> (the original source tree was lost before being committed). The six fixes recorded in
> `HANDOFF.md` (Playwright EPIPE cleanup_fn, prompt editor upgrade, Word export layout,
> model-output artifact stripping, 180-char summary cap, manual body editing) have been
> re-implemented on top of the reconstruction — read `HANDOFF.md` first for details and caveats.

## Project

Windows desktop app (PySide6) for a news sentiment/public-opinion workflow, integrating the
Anthropic Claude API. Covers: news import → retention triage → article scraping → topic
clustering → manual topic adjustment → topic summarization → stance analysis → Word morning-report
export → feedback/case/rule-base management.

## Commands

```bat
REM First run creates .venv and installs requirements automatically
run_desktop.bat            REM normal startup
run_desktop_debug.bat      REM debug mode; logs to %APPDATA%\NewsSentimentDesktopV4\logs\app.log

REM Manual run
python run_desktop.py
python run_desktop.py --debug

REM Tests
call .venv\Scripts\activate.bat
pytest tests -v
pytest tests/test_scraping.py -v      REM single file
pytest tests/test_scraping.py::test_name -v   REM single test
```

Tests requiring a Qt event loop (`test_retention_table_model.py`) use a `qapp` fixture and
auto-`skip` (not fail) when PySide6 isn't installed.

First-run setup also runs `playwright install chromium` (~150MB, skippable with Ctrl+C).

## Architecture

Layered: **UI → Controllers (AppContext) → Services → Repositories → SQLite**.

- `app/ui/` — PySide6 views: `main_window` + `pages/` + `widgets/`
- `app/controllers/` — `AppContext` is the composition root that wires Repositories to the
  `ModelGateway`
- `app/services/ai/` — `ModelGateway` (`model_gateway.py`) is the **sole** entry point for all
  Anthropic API calls; no page ever calls the API directly
- `app/services/{importer,retention,scraping,clustering,summarization,stance,feedback}/` —
  one service package per workflow stage
- `app/repositories/` — SQLite access layer (news / topic / job / prompt / settings...)
- `app/models/` — dataclasses
- `app/workers/` — QThread background jobs (progress/cancel/resume)
- `app/prompts/` — default prompts, tool schemas, version management
- `app/exporters/` — Word report export (python-docx)

### ModelGateway (`app/services/ai/model_gateway.py`)

- Streams by default (`messages.stream` + `get_final_message()`) so long generations (large-topic
  Opus summarization) aren't cut off by a non-streaming read timeout; falls back to non-streaming
  on older SDKs.
- Tool Use structured output, with a one-time fallback to `json_mode` (strict JSON via system
  prompt) if the model fails to return valid tool-use output; `stop_reason` is tagged
  `json_mode_fallback` when this happens.
- Exponential backoff retry, with error classification: `authentication` / `rate_limit` /
  `overloaded` / `invalid_request` / `timeout` / `other`. `invalid_request_error` (400) never
  retries — the request is malformed and retrying won't help.
- Parameter self-healing: on a 400 indicating an unsupported/deprecated param (e.g.
  `temperature` on a given model), strips that param, resends, and caches the incompatibility
  per-model so future calls to that model skip the param entirely (`model_capabilities.py`).
- All parsers of model structured output (clustering, cross-batch topic merge, retention triage,
  stance, rule drafting) defensively validate/normalize types — a malformed individual item
  (e.g. a string where an object was expected) is skipped with a warning, not a crash for the
  whole batch.

### Job/Batch resumability

Jobs use a persisted state machine (`pending/running/completed/failed/cancelled/retryable`) in
SQLite. On restart, incomplete batches are detected and can be resumed; `BatchJobWorker` rebuilds
batch contents from the DB's stored `item_ids` so batch indices stay aligned with the prior run
even if the current list order/order-of-batching differs. Items no longer eligible (e.g. marked
not-retained) auto-complete their batch as skipped.

### Scraping (`app/services/scraping/`)

Two-stage: `requests` + BeautifulSoup first; only escalates to Playwright browser rendering
(`playwright_scraper.py`, headless Chromium + GNE extraction) when the failure reason is "could
not identify a clean main-content container" — NOT for robots.txt disallow, paywall, 403, SSL,
or timeout (those are compliance/connection failures, not something browser rendering should
bypass). Per-domain rate limiting, robots.txt check, paywall keyword detection, JSON-LD
`articleBody` preferred, "further reading" stop-markers to truncate trailing content. Post-scrape
quality check flags suspiciously short (<80 chars) or title/body-non-overlapping content as status
"可疑" (suspicious) — kept for manual review but excluded from clustering/summarization.
Site-specific CSS selectors (`scraping.site_selectors` in settings) allow skipping Playwright
entirely on known-good domains.

### Topic clustering (`app/services/clustering/`)

Candidate bucketing → AI batch clustering → cross-batch merge → items with insufficient body text
are not force-merged. Supports "incremental clustering" (auto-checked when existing topics are
detected): only unclustered news is processed, injected with existing topic examples so the model
prefers reusing `topic_id` over creating new topics — keeps human-confirmed topic structure stable
across daily reruns. Confidence scores (low-confidence flagged in UI, clears on manual assignment)
and a few-shot loop that injects up to 10 recent human corrections (from feedback log) into the
clustering prompt so the model learns editor reclassification preferences without fine-tuning.

### Data/config locations (runtime, not in repo)

```
%APPDATA%\NewsSentimentDesktopV4\
├── news_sentiment.db      # news, topics, stance, feedback, rules, prompts, job queue
├── logs\app.log           # never contains the API key in plaintext
├── exports\               # Word report output
└── prompt_versions\       # prompt version history
```

API key is stored via `keyring` (Windows Credential Manager / DPAPI), never in a plain settings
file. `app/repositories/db.py` uses `schema_version` + `CREATE TABLE IF NOT EXISTS` / `ALTER` only
— migrations never `DROP` existing tables; add new migrations in `_run_migrations()`.

## Known incomplete/simplified areas (per README, not hidden)

- Message Batches API: settings/data model exist, but `ModelGateway` only implements the
  synchronous Messages API — async submit→poll→retrieve is not implemented.
- Manual topic adjustment UI is a three-pane drag-and-drop list (unassigned ↔ selected topic
  members + action buttons), not the full multi-column Kanban view described in the spec.
- No PyInstaller `.spec` / packaged exe yet, though `pyinstaller` is in requirements.
- PySide6 interaction (drag/drop, checkbox state) has not been manually verified in a real Qt
  event loop by the original author (dev environment had no Windows/display); verify on first use.
