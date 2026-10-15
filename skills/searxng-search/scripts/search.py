#!/usr/bin/env python3
"""Compact client for a private SearXNG instance.

Один запуск — максимум один HTTP-запрос: поиск или /healthz, никогда оба.
Клиент не повторяет запросы, не следует redirect'ам и не читает env-proxy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 15
HEALTH_TIMEOUT_SECONDS = 5
ENV_VAR = "SEARXNG_BASE_URL"
CONFIG_RELATIVE_PATH = os.path.join(".config", "searxng-search", "config.json")
RETRY_HINT_SECONDS = 30

# Встроенный дефолт — шлюз оператора на сети агентских VM. Не маршрутизируется
# извне этой сети и не несёт авторизации; опубликован осознанно (исключение
# из правила «никаких адресов внутренних систем», README). Переопределяется
# env SEARXNG_BASE_URL или локальным config.json, см. base_url().
DEFAULT_BASE_URL = "http://10.31.0.1:8888"

# Платные движки (Yandex Cloud Search API: 488₽/1000 дневных запросов,
# ночью 00:00-08:00 МСК — 366₽). Запрашиваются только явным --engines.
PAID_ENGINES = frozenset({"yandexapi"})


class ConfigError(Exception):
    """base_url отсутствует или некорректен: стоп до любого сетевого вызова."""


class RequestFailure(Exception):
    """Единственный HTTP-запрос не удался; клиент не повторяет его сам."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        unresponsive: list | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.unresponsive = unresponsive or []
        self.retry_after = retry_after


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect — честная ошибка, а не второй запрос."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, f"redirect to {newurl} rejected", headers, None
        )


# Opener без env-proxy (ProxyHandler({})) и без автоматических redirect'ов.
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _NoRedirectHandler()
)


def _validated(value: str, source: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        raise ConfigError(f"base_url from {source} is empty")
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ("http", "https"):
        raise ConfigError(
            f"base_url from {source} must start with http:// or https://: {value!r}"
        )
    if not parts.hostname:
        raise ConfigError(f"base_url from {source} has no hostname: {value!r}")
    if parts.username is not None or parts.password is not None:
        raise ConfigError(f"base_url from {source} must not contain userinfo: {value!r}")
    if parts.query or parts.fragment:
        raise ConfigError(
            f"base_url from {source} must not contain a query or fragment: {value!r}"
        )
    return value


def base_url() -> str:
    env_value = os.environ.get(ENV_VAR, "")
    if env_value.strip():
        # Непустая env-настройка не откатывается к локальному конфигу, даже если она неверна.
        return _validated(env_value, f"environment variable {ENV_VAR}")
    config_path = os.path.expanduser(os.path.join("~", CONFIG_RELATIVE_PATH))
    try:
        with open(config_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        # Нет ни env, ни конфига — работает встроенный дефолт оператора:
        # новая VM получает рабочий поиск сразу после установки скилла.
        # Повреждённый конфиг остаётся явной ошибкой, а не молчаливым откатом.
        return _validated(DEFAULT_BASE_URL, "built-in default")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read configuration {config_path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("base_url"), str):
        raise ConfigError(
            f"configuration {config_path} must be a JSON object with a string base_url"
        )
    return _validated(payload["base_url"], f"configuration {config_path}")


def request(
    url: str, data: bytes | None = None, *, timeout: int = TIMEOUT_SECONDS
) -> tuple[int, bytes, str]:
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    with _OPENER.open(req, timeout=timeout) as response:
        return response.status, response.read(), response.headers.get("Content-Type", "")


def _retry_after_seconds(exc: urllib.error.HTTPError) -> int | None:
    raw = exc.headers.get("Retry-After") if exc.headers is not None else None
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        # HTTP-date и мусор игнорируем: остаётся базовый лимит ожидания.
        return None


def _http_failure(exc: urllib.error.HTTPError) -> RequestFailure:
    fp = getattr(exc, "fp", None)
    try:
        body = fp.read() if fp is not None else b""
    except OSError:
        body = b""
    unresponsive: list = []
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        candidate = payload.get("unresponsive_engines")
        if isinstance(candidate, list):
            unresponsive = candidate
    reason = str(exc.reason or exc).strip()
    return RequestFailure(
        f"HTTP {exc.code}: {reason}",
        status=exc.code,
        unresponsive=unresponsive,
        retry_after=_retry_after_seconds(exc),
    )


def fetch(
    url: str, data: bytes | None = None, *, timeout: int = TIMEOUT_SECONDS
) -> tuple[int, bytes, str]:
    try:
        return request(url, data=data, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise _http_failure(exc) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise RequestFailure(f"{type(exc).__name__}: {reason}") from exc


def compact(text: object, limit: int = 260) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def extract_payload(body: bytes) -> tuple[list[dict], list, str]:
    """Валидирует структуру ответа до любых .get/итераций по нему."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("top-level value is not a JSON object")
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        raise ValueError("field results is not a list")
    results: list[dict] = []
    for item in raw_results[:5]:
        if not isinstance(item, dict):
            raise ValueError("results contains a non-object item")
        for name in ("title", "url", "content"):
            value = item.get(name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"result field {name} is not a string")
        results.append(item)
    unresponsive = payload.get("unresponsive_engines")
    if not isinstance(unresponsive, list):
        raise ValueError("field unresponsive_engines is not a list")
    echoed = payload.get("query")
    query_echo = echoed if isinstance(echoed, str) else ""
    return results, unresponsive, query_echo


def report_error(
    *,
    endpoint: str | None,
    error: str,
    as_json: bool,
    status: int | None = None,
    unresponsive: list | None = None,
    retry_after: int | None = None,
) -> None:
    wait = max(RETRY_HINT_SECONDS, retry_after or 0)
    hint = (
        f"one retry is allowed only on an explicit user request, not earlier than "
        f"{wait} seconds; the client itself never retries"
    )
    unresponsive = unresponsive or []
    if as_json:
        # При --json stdout всегда один JSON-object, без prose-примесей.
        print(
            json.dumps(
                {
                    "error": error,
                    "endpoint": endpoint,
                    "status": status,
                    "unresponsive_engines": unresponsive,
                    "retry_hint": hint,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    print(f"SearXNG error: {error}", file=sys.stderr)
    status_text = "null" if status is None else status
    print(f"Endpoint: {endpoint} (HTTP status: {status_text})", file=sys.stderr)
    if unresponsive:
        print(
            "Unresponsive engines: " + json.dumps(unresponsive, ensure_ascii=False),
            file=sys.stderr,
        )
    print(f"Retry: {hint}", file=sys.stderr)


def check_health(base: str, *, as_json: bool = False) -> int:
    endpoint = f"{base}/healthz"
    try:
        status, body, _ = fetch(endpoint, timeout=HEALTH_TIMEOUT_SECONDS)
    except RequestFailure as failure:
        report_error(
            endpoint=endpoint,
            error=str(failure),
            as_json=as_json,
            status=failure.status,
            unresponsive=failure.unresponsive,
            retry_after=failure.retry_after,
        )
        return 3
    text = body.decode("utf-8", errors="replace").strip()
    if status == 200 and text == "OK":
        if as_json:
            print(
                json.dumps(
                    {"ok": True, "base_url": base, "status": status, "body": "OK"},
                    ensure_ascii=False,
                )
            )
        else:
            print(f"GET {endpoint} -> 200 OK")
        return 0
    report_error(
        endpoint=endpoint,
        error=f"unexpected healthz response: HTTP {status}, body={text!r}",
        as_json=as_json,
        status=status,
    )
    return 3


def main() -> int:
    parser = argparse.ArgumentParser(description="Search private SearXNG")
    parser.add_argument("query", nargs="?", help="one meaningful search query")
    parser.add_argument("--language", choices=("en", "ru"), default="en")
    parser.add_argument("--pageno", type=int, default=1)
    parser.add_argument(
        "--engines",
        choices=("braveapi", "yandexapi"),
        help="pin the search to one engine; yandexapi is paid per request",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()

    if args.pageno < 1:
        parser.error("--pageno must be >= 1")
    if not args.health and not (args.query or "").strip():
        parser.error("query is required unless --health is used")

    try:
        base = base_url()
    except ConfigError as exc:
        report_error(endpoint=None, error=str(exc), as_json=args.as_json)
        return 2

    if args.health:
        return check_health(base, as_json=args.as_json)

    endpoint = f"{base}/search"
    form_fields = {
        "q": args.query,
        "format": "json",
        "language": args.language,
        "pageno": args.pageno,
    }
    if args.engines:
        form_fields["engines"] = args.engines
    form = urllib.parse.urlencode(form_fields).encode("utf-8")

    try:
        status, body, content_type = fetch(endpoint, data=form)
    except RequestFailure as failure:
        report_error(
            endpoint=endpoint,
            error=str(failure),
            as_json=args.as_json,
            status=failure.status,
            unresponsive=failure.unresponsive,
            retry_after=failure.retry_after,
        )
        return 3

    try:
        raw_results, unresponsive, query_echo = extract_payload(body)
    except ValueError as exc:
        report_error(
            endpoint=endpoint,
            error=f"invalid SearXNG response (HTTP {status}, {content_type}): {exc}",
            as_json=args.as_json,
            status=status,
        )
        return 4

    results = [
        {
            "title": compact(item.get("title")),
            "url": str(item.get("url") or "").strip(),
            "content": compact(item.get("content")),
        }
        for item in raw_results
    ]
    paid_engine = args.engines in PAID_ENGINES
    output = {
        "status": status,
        "base_url": base,
        "query": query_echo or args.query,
        "language": args.language,
        "engines": args.engines or "default",
        "paid_engine": paid_engine,
        "results": results,
        "unresponsive_engines": unresponsive,
    }

    if args.as_json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    print(f"POST {endpoint} -> HTTP {status}")
    print(f"Query: {output['query']} (language={args.language}, pageno={args.pageno})")
    if paid_engine:
        print(
            "Paid engine: results come from a metered engine "
            "(~0.49 RUB per daytime request); mention this in the answer."
        )
    if results:
        for index, item in enumerate(results, 1):
            print(f"\n{index}. {item['title'] or '[no title]'}")
            print(item["url"] or "[no URL]")
            print(item["content"] or "[no snippet]")
    else:
        print("\nNo results returned (a valid empty response, not a client failure).")
    print(
        "\nUnresponsive engines: "
        + (json.dumps(unresponsive, ensure_ascii=False) if unresponsive else "[]")
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        # Потребитель закрыл pipe (head/less): не ошибка поиска и не трейсбек.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        raise SystemExit(141) from None
