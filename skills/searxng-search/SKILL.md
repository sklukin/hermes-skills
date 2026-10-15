---
name: searxng-search
description: Use private SearXNG as the default internet search.
version: 1.4.1
author: sklukin, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [web, search, searxng]
---

# SearXNG Search

Use a user-provided private SearXNG instance as the default internet search mechanism. Report real results and upstream failures without silently substituting another search provider. This client is the search mechanism of this skill; when it fails, do not fall back to a native web-search tool or another search service.

## Prerequisites

The client resolves the instance base URL from the first non-empty source:

1. environment variable `SEARXNG_BASE_URL`;
2. local config `~/.config/searxng-search/config.json` with a string field `base_url`;
3. the built-in default — the operator's gateway shipped in `scripts/search.py`.

On a provisioned VM the skill works out of the box: nothing to configure before the first search. Use `SEARXNG_BASE_URL` (or the local config) only to point the client elsewhere, for diagnostics or tests. A broken config file or an invalid value is reported as an explicit error, never silently replaced; do not write additional private instance addresses or credentials into the skill or the repository.

## How to run

Invoke the bundled client [scripts/search.py](scripts/search.py) through `terminal` with the Python of the installed Hermes — on a provisioned VM `/home/agent/.hermes/hermes-agent/venv/bin/python`. The script uses only the standard library; installing new dependencies is not required.

```bash
/home/agent/.hermes/hermes-agent/venv/bin/python <skill_dir>/scripts/search.py --language ru "site:go.dev последняя стабильная версия Go"
```

Options:

- `--language en|ru` (default: `en`)
- `--pageno N` (default: `1`)
- `--engines braveapi|yandexapi` to pin the search to one engine
- `--json` for structured output
- `--health` to check only `/healthz`

The client:

- sends exactly one search request as `POST /search` with `application/x-www-form-urlencoded` data and `format=json`;
- performs no automatic retries, redirects, paging, or engine loops, and ignores environment proxy settings;
- requests `/healthz` only with `--health`; a search run never touches it;
- uses a 15-second timeout for search and 5 seconds for `--health`;
- prints at most five results with title, URL, and compact snippet;
- reports `unresponsive_engines` and empty results honestly.

`/healthz` is a one-shot precheck or diagnostic, not part of every search: HTTP 200 proves only that SearXNG itself is running, not that upstream engines answer.

## Engine tiers and cost

| Tier | How | Cost | When |
|---|---|---|---|
| Free merge | no flag | free | default choice for every search |
| Stable core | `--engines braveapi` | free within the shared monthly quota | deterministic results without scraping noise |
| RU core | `--engines yandexapi` | **paid**: ~0.49 ₽ per daytime request (488 ₽/1000), ~0.37 ₽ at night 00:00–08:00 MSK; auth/5xx errors are not billed | only when the free tier demonstrably fails on a Russian-language topic |

The paid engine is never queried implicitly: it participates only when pinned explicitly. The client marks such runs with `paid_engine: true` (JSON) or a `Paid engine:` line (text) — carry that mark into the answer, so the user knows the query cost money.

## Procedure

1. Form one meaningful query; preserve operators such as `site:`.
2. Pick the tier: start with the free merge; escalate to `--engines yandexapi` only when the free results are insufficient for a Russian-language topic, never for routine or English queries.
3. Run the client once with the requested result language.
4. On any failure, report the error to the user immediately, including the endpoint and the HTTP status the client printed. Retry only on an explicit user request: at most one retry per user task, not earlier than 30 seconds after the failure (a longer server `Retry-After` extends the wait). After that report the search as unavailable and stop.
5. Empty results or suspended engines are valid responses, not failures and not reasons to retry.
6. Do not work around a failure via a native web-search tool, another search service, another instance address, or query rephrasing; do not stand up a replacement (local SearXNG, Docker, new packages) just to satisfy this skill.
7. A missing `scripts/search.py` or a configuration error means the install is broken: report it and stop instead of searching the filesystem for the address or the script.
8. Include the source URL for every result used in the answer; cite only what the actual output contains. When the run is marked paid, say so in the answer.
9. Fetch source pages separately only when the task needs verification beyond snippets.

## Rate budget

The private instance is shared by the whole fleet with a budget of about 1 request per second to the core and roughly 2000 requests per month in total. Run searches strictly one at a time: no batch queries, no parallel runs, no walking multiple pages. `yandexapi` requests are metered in rubles on top of that quota — each one is a deliberate, user-visible expense.

## Pitfalls

- `/healthz` proves only that SearXNG is running, not that upstream engines work.
- Suspended engines in `unresponsive_engines` are not a total service failure when results exist.
- Empty results stay empty; never invent replacements.
- Do not send JSON request bodies, omit `format=json`, or request other formats; the client always sends `format=json`.
- Do not send secrets or private user data in queries.
- Do not commit private SearXNG URLs, credentials, or internal network details.
- Pin the paid engine only via `--engines yandexapi`; do not put `!yda` into the query — bangs belong to manual API calls, the client pins engines by flag.

## Verification

A complete skill install contains both `SKILL.md` and `scripts/search.py` in the skill directory; a pinned install delivers referenced support files, so check the script too, not only the instruction. A valid search exits with code 0, reports HTTP 200, and prints either real results or an explicit empty-results message plus upstream engine status. A service failure exits nonzero, names the endpoint and the HTTP status (or null when there was no response), and never prints a traceback.
