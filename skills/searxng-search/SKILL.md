---
name: searxng-search
description: Use private SearXNG as the default internet search.
version: 1.1.0
author: sklukin, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [web, search, searxng]
---

# SearXNG Search

Use a user-provided private SearXNG instance as the default internet search mechanism. Report real results and upstream failures without silently substituting another search provider.

## Prerequisites

Set `SEARXNG_BASE_URL` to the instance base URL without a trailing slash. Never write private instance addresses into the skill or repository.

## How to run

Invoke the bundled client through `terminal`:

```bash
SEARXNG_BASE_URL="<private-base-url>" python <skill_dir>/scripts/search.py --language en "site:docs.python.org asyncio"
```

Options:

- `--language en|ru` (default: `en`)
- `--pageno N` (default: `1`)
- `--json` for structured output
- `--health` to check only `/healthz`

The client:

- sends exactly one search request as `POST /search` with `application/x-www-form-urlencoded` data;
- always sends `format=json`;
- uses a 15-second timeout for every HTTP request;
- prints at most five results with title, URL, and compact snippet;
- reports `unresponsive_engines` and empty results honestly.

## Procedure

1. Form one meaningful query; preserve operators such as `site:`.
2. Run the client once with the requested result language.
3. Do not issue loops or aggressive retries. If it times out, fails the health check, or returns no results, report that directly and wait before retrying.
4. Include the source URL for every result used in the answer.
5. Fetch source pages separately only when the task needs verification beyond snippets.

## Pitfalls

- `/healthz` proves only that SearXNG is running, not that upstream engines work.
- Suspended engines in `unresponsive_engines` are not a total service failure when results exist.
- Empty results stay empty; never invent replacements.
- Do not send JSON request bodies, omit `format=json`, or request `format=csv`.
- Do not commit private SearXNG URLs, credentials, or internal network details.

## Verification

Run `--health` first when diagnosing availability. A valid search exits with code 0, reports HTTP 200, and prints either real results or an explicit empty-results message plus upstream engine status. A service failure exits nonzero and states the timeout or endpoint failure.
