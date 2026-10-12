#!/usr/bin/env python3
"""Compact client for a private SearXNG instance."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 15


def base_url() -> str:
    value = os.environ.get("SEARXNG_BASE_URL", "").strip().rstrip("/")
    if not value:
        raise ValueError("SEARXNG_BASE_URL is required")
    if not value.startswith(("http://", "https://")):
        raise ValueError("SEARXNG_BASE_URL must start with http:// or https://")
    return value


def request(url: str, data: bytes | None = None) -> tuple[int, bytes, str]:
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
        return response.status, response.read(), response.headers.get("Content-Type", "")


def check_health(base: str) -> tuple[bool, str]:
    try:
        status, body, _ = request(f"{base}/healthz")
        text = body.decode("utf-8", errors="replace").strip()
        if status == 200 and text == "OK":
            return True, "OK"
        return False, f"HTTP {status}, body={text!r}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def compact(text: object, limit: int = 260) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def main() -> int:
    parser = argparse.ArgumentParser(description="Search private SearXNG")
    parser.add_argument("query", nargs="?", help="one meaningful search query")
    parser.add_argument("--language", choices=("en", "ru"), default="en")
    parser.add_argument("--pageno", type=int, default=1)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()

    if args.pageno < 1:
        parser.error("--pageno must be >= 1")
    if not args.health and not args.query:
        parser.error("query is required unless --health is used")

    try:
        base = base_url()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    healthy, health_detail = check_health(base)
    if not healthy:
        print(f"SearXNG unavailable: {base}/healthz -> {health_detail}", file=sys.stderr)
        return 2

    if args.health:
        output = {"ok": True, "base_url": base, "status": 200, "body": "OK"}
        print(json.dumps(output, ensure_ascii=False) if args.as_json else f"GET {base}/healthz -> 200 OK")
        return 0

    form = urllib.parse.urlencode(
        {
            "q": args.query,
            "format": "json",
            "language": args.language,
            "pageno": args.pageno,
        }
    ).encode("utf-8")

    try:
        status, body, content_type = request(f"{base}/search", data=form)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"SearXNG search failed without retry: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"SearXNG returned invalid JSON (HTTP {status}, {content_type}): {exc}", file=sys.stderr)
        return 4

    raw_results = payload.get("results") or []
    results = [
        {
            "title": compact(item.get("title")),
            "url": str(item.get("url") or "").strip(),
            "content": compact(item.get("content")),
        }
        for item in raw_results[:5]
    ]
    unresponsive = payload.get("unresponsive_engines") or []
    output = {
        "status": status,
        "base_url": base,
        "query": payload.get("query", args.query),
        "language": args.language,
        "results": results,
        "unresponsive_engines": unresponsive,
    }

    if args.as_json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    print(f"POST {base}/search -> HTTP {status}")
    print(f"Query: {output['query']} (language={args.language}, pageno={args.pageno})")
    if results:
        for index, item in enumerate(results, 1):
            print(f"\n{index}. {item['title'] or '[no title]'}")
            print(item["url"] or "[no URL]")
            print(item["content"] or "[no snippet]")
    else:
        print("\nNo results returned.")
    print("\nUnresponsive engines: " + (json.dumps(unresponsive, ensure_ascii=False) if unresponsive else "[]"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
