---
name: searxng-search
description: Use private SearXNG as the default internet search.
version: 1.2.0
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
2. local config `~/.config/searxng-search/config.json` with a string field `base_url`.

Without either source the client stops with a configuration error and never guesses an address. On a provisioned VM the local config already points at the private gateway; keep it that way and never write private instance addresses into the skill, the repository, or the Hermes environment.

## How to run

Invoke the bundled client [scripts/search.py](scripts/search.py) through `terminal` with the Python of the installed Hermes — on a provisioned VM `/home/agent/.hermes/hermes-agent/venv/bin/python`. The script uses only the standard library; installing new dependencies is not required.

```bash
/home/agent/.hermes/hermes-agent/venv/bin/python <skill_dir>/scripts/search.py --language ru "site:go.dev последняя стабильная версия Go"
```

Options:

- `--language en|ru` (default: `en`)
- `--pageno N` (default: `1`)
- `--engines braveapi` to pin the search to the managed engine
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

## Procedure

1. Form one meaningful query; preserve operators such as `site:`.
2. Run the client once with the requested result language.
3. On any failure, report the error to the user immediately, including the endpoint and the HTTP status the client printed. Retry only on an explicit user request: at most one retry per user task, not earlier than 30 seconds after the failure (a longer server `Retry-After` extends the wait). After that report the search as unavailable and stop.
4. Empty results or suspended engines are valid responses, not failures and not reasons to retry.
5. Do not work around a failure via a native web-search tool, another search service, another instance address, or query rephrasing; do not stand up a replacement (local SearXNG, Docker, new packages) just to satisfy this skill.
6. A missing `scripts/search.py` or a configuration error means the install is broken: report it and stop instead of searching the filesystem for the address or the script.
7. Include the source URL for every result used in the answer; cite only what the actual output contains.
8. Fetch source pages separately only when the task needs verification beyond snippets.

## Rate budget

The private instance is shared by the whole fleet with a budget of about 1 request per second to the core and roughly 2000 requests per month in total. Run searches strictly one at a time: no batch queries, no parallel runs, no walking multiple pages.

## Pitfalls

- `/healthz` proves only that SearXNG is running, not that upstream engines work.
- Suspended engines in `unresponsive_engines` are not a total service failure when results exist.
- Empty results stay empty; never invent replacements.
- Do not send JSON request bodies, omit `format=json`, or request other formats; the client always sends `format=json`.
- Do not send secrets or private user data in queries.
- Do not commit private SearXNG URLs, credentials, or internal network details.

## Verification

A complete skill install contains both `SKILL.md` and `scripts/search.py` in the skill directory; a pinned install delivers referenced support files, so check the script too, not only the instruction. A valid search exits with code 0, reports HTTP 200, and prints either real results or an explicit empty-results message plus upstream engine status. A service failure exits nonzero, names the endpoint and the HTTP status (or null when there was no response), and never prints a traceback.
