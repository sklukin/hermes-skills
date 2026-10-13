#!/usr/bin/env python3
"""Проверки CLI-поведения search.py против loopback HTTP-сервера (stdlib unittest).

Запуск: python3 -m unittest discover -s skills/searxng-search/scripts -p 'test_*.py'
Частные адресы не используются: только 127.0.0.1 на лету выделенном порту.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("search.py")
UNICODE_QUERY = "site:go.dev последняя стабильная версия Go"
SPECIAL_QUERY = "c++ & python + 100% (2024)"

PROXY_ENV_KEYS = {"searxng_base_url", "http_proxy", "https_proxy", "all_proxy", "no_proxy"}

SAMPLE_RESULTS = [
    {"title": f"Result {index}", "url": f"https://example.org/{index}", "content": f"snippet {index}"}
    for index in range(1, 7)
]


def run_client(args, *, base=None, home=None, extra_env=None):
    env = {key: value for key, value in os.environ.items() if key.lower() not in PROXY_ENV_KEYS}
    if base is not None:
        env["SEARXNG_BASE_URL"] = base
    if home is not None:
        env["HOME"] = str(home)
    if extra_env:
        env.update({key: str(value) for key, value in extra_env.items()})
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


class ServerState:
    def __init__(self):
        self.requests = []
        self.mode = "ok"
        self.delay = 0.0


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            form = {
                key: values[0]
                for key, values in urllib.parse.parse_qs(body.decode("utf-8")).items()
            }
            state.requests.append({"method": "POST", "path": self.path, "form": form})
            self.echo_query = form.get("q", "")
            self._respond()

        def do_GET(self):
            state.requests.append({"method": "GET", "path": self.path, "form": None})
            self.echo_query = ""
            self._respond()

        def _respond(self):
            if state.delay:
                time.sleep(state.delay)
            if self.path == "/healthz":
                self._send_bytes(200, b"OK", "text/plain; charset=utf-8")
                return
            mode = state.mode
            if mode == "ok":
                self._send_json(
                    200,
                    {
                        "query": self.echo_query,
                        "results": SAMPLE_RESULTS,
                        "unresponsive_engines": [["duckduckgo", "timeout"]],
                    },
                )
            elif mode == "empty":
                self._send_json(
                    200,
                    {
                        "query": self.echo_query,
                        "results": [],
                        "unresponsive_engines": [["google", "CAPTCHA"]],
                    },
                )
            elif mode == "http403":
                self._send_json(403, {"unresponsive_engines": [["braveapi", "suspended"]]})
            elif mode == "http429":
                body = json.dumps({"unresponsive_engines": [["braveapi", "rate limit"]]}).encode("utf-8")
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Retry-After", "120")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self._write(body)
            elif mode == "http500_html":
                self._send_bytes(500, b"<html>Server Error full body</html>", "text/html")
            elif mode == "redirect":
                self.send_response(302)
                self.send_header("Location", f"http://{self.headers.get('Host', '127.0.0.1')}/redirected")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif mode == "invalid_json":
                self._send_bytes(200, b"<!DOCTYPE html><html>not json</html>", "text/html")
            elif mode == "schema_results_not_list":
                self._send_json(200, {"results": "nope", "unresponsive_engines": []})
            elif mode == "schema_item_not_object":
                self._send_json(200, {"results": [1], "unresponsive_engines": []})
            elif mode == "schema_field_not_string":
                self._send_json(
                    200,
                    {"results": [{"title": 5, "url": "u", "content": "c"}], "unresponsive_engines": []},
                )
            elif mode == "schema_unresponsive_not_list":
                self._send_json(200, {"results": [], "unresponsive_engines": "x"})
            else:
                self._send_bytes(500, b"unknown test mode", "text/plain")

        def _send_json(self, status, payload):
            self._send_bytes(status, json.dumps(payload).encode("utf-8"), "application/json")

        def _send_bytes(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self._write(body)

        def _write(self, body):
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format, *args):
            pass

    return Handler


class LoopbackServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = ServerState()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.state))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.state.requests.clear()
        self.state.mode = "ok"
        self.state.delay = 0.0

    def posts(self):
        return [r for r in self.state.requests if r["method"] == "POST" and r["path"] == "/search"]

    def health_gets(self):
        return [r for r in self.state.requests if r["path"] == "/healthz"]


class SearchBehavior(LoopbackServerTestCase):
    def test_unicode_query_single_post_and_echo(self):
        proc = run_client(["--json", "--language", "ru", UNICODE_QUERY], base=self.base_url)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        posts = self.posts()
        self.assertEqual(len(posts), 1)
        self.assertEqual(len(self.health_gets()), 0)
        form = posts[0]["form"]
        self.assertEqual(form["q"], UNICODE_QUERY)
        self.assertEqual(form["format"], "json")
        self.assertEqual(form["language"], "ru")
        self.assertEqual(form["pageno"], "1")
        self.assertNotIn("engines", form)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], 200)
        self.assertEqual(len(payload["results"]), 5)
        self.assertEqual(payload["unresponsive_engines"], [["duckduckgo", "timeout"]])
        self.assertEqual(payload["query"], UNICODE_QUERY)

    def test_special_characters_survive_form_encoding(self):
        proc = run_client(["--json", SPECIAL_QUERY], base=self.base_url)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.posts()[0]["form"]["q"], SPECIAL_QUERY)

    def test_engines_flag_sent_only_when_selected(self):
        proc = run_client(["--json", "--engines", "braveapi", "test"], base=self.base_url)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.posts()[0]["form"].get("engines"), "braveapi")
        self.state.requests.clear()
        proc = run_client(["--json", "test"], base=self.base_url)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("engines", self.posts()[0]["form"])

    def test_rejects_empty_query_and_bad_pageno_before_network(self):
        proc = run_client(["--json", ""], base=self.base_url)
        self.assertNotEqual(proc.returncode, 0)
        proc = run_client(["--json", "--pageno", "0", "query"], base=self.base_url)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(len(self.state.requests), 0)

    def test_empty_results_with_suspended_engines_is_success(self):
        self.state.mode = "empty"
        proc = run_client(["--json", "query"], base=self.base_url)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["unresponsive_engines"], [["google", "CAPTCHA"]])
        text = run_client(["query"], base=self.base_url)
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn("No results", text.stdout)

    def test_http_error_reports_endpoint_status_and_engines(self):
        self.state.mode = "http403"
        proc = run_client(["--json", "query"], base=self.base_url)
        self.assertNotEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertIn("403", payload["error"])
        self.assertEqual(payload["endpoint"], f"{self.base_url}/search")
        self.assertEqual(payload["status"], 403)
        self.assertEqual(payload["unresponsive_engines"], [["braveapi", "suspended"]])
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(len(self.posts()), 1)

    def test_http_429_retry_after_extends_hint(self):
        self.state.mode = "http429"
        proc = run_client(["--json", "query"], base=self.base_url)
        self.assertNotEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], 429)
        self.assertIn("120", payload["retry_hint"])

    def test_http_500_html_body_not_echoed(self):
        self.state.mode = "http500_html"
        proc = run_client(["--json", "query"], base=self.base_url)
        self.assertNotEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], 500)
        self.assertNotIn("<html>", proc.stdout)
        self.assertNotIn("<html>", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_invalid_json_is_schema_error(self):
        self.state.mode = "invalid_json"
        proc = run_client(["--json", "query"], base=self.base_url)
        self.assertNotEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], 200)
        self.assertIn("invalid", payload["error"])
        self.assertNotIn("Traceback", proc.stderr)

    def test_schema_violations_fail_cleanly(self):
        modes = (
            "schema_results_not_list",
            "schema_item_not_object",
            "schema_field_not_string",
            "schema_unresponsive_not_list",
        )
        for mode in modes:
            with self.subTest(mode=mode):
                self.state.mode = mode
                proc = run_client(["--json", "query"], base=self.base_url)
                self.assertNotEqual(proc.returncode, 0)
                json.loads(proc.stdout)
                self.assertNotIn("Traceback", proc.stderr)

    def test_redirect_is_error_without_second_request(self):
        self.state.mode = "redirect"
        proc = run_client(["--json", "query"], base=self.base_url)
        self.assertNotEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertIn("redirect", payload["error"].lower())
        self.assertEqual(len(self.state.requests), 1)
        self.assertFalse(any(r["path"] == "/redirected" for r in self.state.requests))

    def test_environment_proxy_is_ignored(self):
        dead_proxy = "http://127.0.0.1:9"
        proc = run_client(
            ["--json", "query"],
            base=self.base_url,
            extra_env={
                "http_proxy": dead_proxy,
                "HTTP_PROXY": dead_proxy,
                "https_proxy": dead_proxy,
                "HTTPS_PROXY": dead_proxy,
            },
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.posts()), 1)

    def test_connection_refused_reports_null_status(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        proc = run_client(["--json", "query"], base=f"http://127.0.0.1:{port}")
        self.assertNotEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["endpoint"], f"http://127.0.0.1:{port}/search")
        self.assertIsNone(payload["status"])
        self.assertNotIn("Traceback", proc.stderr)


class HealthBehavior(LoopbackServerTestCase):
    def test_search_does_not_touch_healthz_and_vice_versa(self):
        search = run_client(["--json", "query"], base=self.base_url)
        self.assertEqual(search.returncode, 0, search.stderr)
        self.assertEqual(len(self.health_gets()), 0)
        self.state.requests.clear()
        health = run_client(["--health", "--json"], base=self.base_url)
        self.assertEqual(health.returncode, 0, health.stderr)
        self.assertEqual(len(self.health_gets()), 1)
        self.assertEqual(len(self.posts()), 0)
        payload = json.loads(health.stdout)
        self.assertTrue(payload["ok"])

    def test_health_timeout_is_5_seconds(self):
        self.state.delay = 5.5
        started = time.monotonic()
        proc = run_client(["--health", "--json"], base=self.base_url)
        elapsed = time.monotonic() - started
        self.assertNotEqual(proc.returncode, 0)
        self.assertLess(elapsed, 8)
        payload = json.loads(proc.stdout)
        self.assertIsNone(payload["status"])
        self.assertNotIn("Traceback", proc.stderr)

    def test_search_timeout_is_15_seconds(self):
        self.state.delay = 5.5
        proc = run_client(["--json", "query"], base=self.base_url)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.posts()), 1)


class ConfigResolution(LoopbackServerTestCase):
    def setUp(self):
        super().setUp()
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = home.name
        self.config_dir = Path(self.home) / ".config" / "searxng-search"

    def write_config(self, payload):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        (self.config_dir / "config.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_config_file_used_when_env_absent(self):
        self.write_config({"base_url": self.base_url})
        proc = run_client(["--json", "query"], home=self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.posts()), 1)

    def test_env_takes_precedence_over_config(self):
        self.write_config({"base_url": "http://127.0.0.1:1"})
        proc = run_client(["--json", "query"], base=self.base_url, home=self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.posts()), 1)

    def test_invalid_env_is_error_not_network(self):
        self.write_config({"base_url": self.base_url})
        proc = run_client(["--json", "query"], base="ftp://example.invalid", home=self.home)
        self.assertNotEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertIn("SEARXNG_BASE_URL", payload["error"])
        self.assertEqual(len(self.state.requests), 0)

    def test_missing_everything_falls_back_to_builtin_default(self):
        # Ни env, ни конфига: base_url() возвращает встроенный дефолт оператора,
        # без сети (модуль импортируется и разрешается локально).
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib.util as u\n"
                f"s = u.spec_from_file_location('search', {str(SCRIPT)!r})\n"
                "m = u.module_from_spec(s)\n"
                "s.loader.exec_module(m)\n"
                "print(m.base_url())\n",
            ],
            capture_output=True,
            text=True,
            env={
                key: value
                for key, value in os.environ.items()
                if key.lower() not in PROXY_ENV_KEYS
            }
            | {"HOME": self.home},
            timeout=30,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertEqual(probe.stdout.strip(), "http://10.31.0.1:8888")

    def test_broken_json_config_is_error(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        (self.config_dir / "config.json").write_text("{broken", encoding="utf-8")
        proc = run_client(["--json", "query"], home=self.home)
        self.assertNotEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertIn("cannot read configuration", payload["error"])

    def test_non_string_base_url_is_error(self):
        self.write_config({"base_url": 42})
        proc = run_client(["--json", "query"], home=self.home)
        self.assertNotEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertIn("string base_url", payload["error"])

    def test_config_url_validated(self):
        self.write_config({"base_url": "http:///nohost"})
        proc = run_client(["--json", "query"], home=self.home)
        self.assertNotEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertIn("no hostname", payload["error"])


if __name__ == "__main__":
    unittest.main()
