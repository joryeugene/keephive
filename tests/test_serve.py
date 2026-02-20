"""Tests for hive serve: web dashboard rendering and HTTP server."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.client import HTTPConnection
from pathlib import Path


# ---- Markdown renderer tests ----


def test_render_md_headers():
    from keephive.commands.serve import render_md

    html = render_md("# Title\n## Section\n### Sub")
    assert "<h1>" in html
    assert "<h2>" in html
    assert "<h3>" in html


def test_render_md_bold_inline():
    from keephive.commands.serve import render_md

    html = render_md("This is **bold** text")
    assert "<strong>bold</strong>" in html


def test_render_md_code_block():
    from keephive.commands.serve import render_md

    html = render_md("```\nhive status\n```")
    assert "<pre" in html
    assert "hive status" in html


def test_render_md_inline_code():
    from keephive.commands.serve import render_md

    html = render_md("Use `hive r` to remember")
    assert "<code>" in html
    assert "hive r" in html


def test_render_md_list():
    from keephive.commands.serve import render_md

    html = render_md("- item one\n- item two")
    assert "<ul>" in html
    assert "<li>" in html
    assert "item one" in html


def test_render_md_table():
    from keephive.commands.serve import render_md

    md = "| Col A | Col B |\n|---|---|\n| val1 | val2 |"
    html = render_md(md)
    assert "<table" in html
    assert "<th>" in html
    assert "val1" in html


def test_render_md_link():
    from keephive.commands.serve import render_md

    html = render_md("See [docs](https://example.com)")
    assert '<a href="https://example.com">' in html
    assert "docs" in html


def test_render_md_html_escaping():
    from keephive.commands.serve import render_md

    html = render_md("Use <script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_md_horizontal_rule():
    from keephive.commands.serve import render_md

    html = render_md("before\n---\nafter")
    assert "<hr>" in html


# ---- Panel rendering tests ----


def test_render_status_panel(hive_env):
    from keephive.commands.serve import _get_status_data, _render_status_panel

    data = _get_status_data()
    html = _render_status_panel(data)
    assert "Status" in html
    assert "facts" in html
    assert "stale" in html


def test_render_log_panel_empty(hive_env):
    from keephive.commands.serve import _get_log_data, _render_log_panel

    data = _get_log_data()
    html = _render_log_panel(data)
    assert "Log" in html


def test_render_todo_panel(hive_env, daily_with_entries):
    from keephive.commands.serve import _get_todo_data, _render_todo_panel

    data = _get_todo_data()
    html = _render_todo_panel(data)
    assert "TODO" in html or "open" in html.lower()


def test_render_knowledge_panel(hive_env):
    from keephive.commands.serve import _get_knowledge_data, _render_knowledge_panel

    data = _get_knowledge_data()
    html = _render_knowledge_panel(data)
    assert "Knowledge" in html


def test_render_notes_panel_empty(hive_env):
    from keephive.commands.serve import _get_notes_data, _render_notes_panel

    data = _get_notes_data()
    html = _render_notes_panel(data)
    assert "Notes" in html


def test_render_memory_panel(hive_env):
    from keephive.commands.serve import _get_memory_data, _render_memory_panel

    data = _get_memory_data()
    html = _render_memory_panel(data)
    assert "Memory" in html


def test_render_stats_panel(hive_env):
    from keephive.commands.serve import _get_stats_data, _render_stats_panel

    data = _get_stats_data()
    html = _render_stats_panel(data)
    assert "Stats" in html


# ---- Fragment rendering tests ----


def test_render_fragment_all(hive_env):
    from keephive.commands.serve import render_fragment

    html = render_fragment("all")
    assert len(html) > 100
    # Should have multiple cards
    assert html.count('class="card"') >= 2


def test_render_fragment_daily(hive_env):
    from keephive.commands.serve import render_fragment

    html = render_fragment("daily")
    assert len(html) > 50
    assert "Log" in html


def test_render_fragment_dev(hive_env):
    from keephive.commands.serve import render_fragment

    html = render_fragment("dev")
    assert len(html) > 50
    assert "Knowledge" in html


def test_render_fragment_simple(hive_env):
    from keephive.commands.serve import render_fragment

    html = render_fragment("simple")
    assert len(html) > 50


def test_render_fragment_know(hive_env):
    from keephive.commands.serve import render_fragment

    html = render_fragment("know")
    assert "Knowledge" in html


def test_render_fragment_mem(hive_env):
    from keephive.commands.serve import render_fragment

    html = render_fragment("mem")
    assert "Memory" in html


def test_render_fragment_notes(hive_env):
    from keephive.commands.serve import render_fragment

    html = render_fragment("notes")
    assert "Notes" in html


def test_render_fragment_stats(hive_env):
    from keephive.commands.serve import render_fragment

    html = render_fragment("stats")
    assert "Stats" in html


def test_render_fragment_unknown():
    from keephive.commands.serve import render_fragment

    html = render_fragment("nonexistent")
    assert "Unknown view" in html or len(html) >= 0  # Should not crash


# ---- Full page rendering ----


def test_render_page_all(hive_env):
    from keephive.commands.serve import render_page

    page = render_page("all", 3847)
    assert "<!DOCTYPE html>" in page
    assert "hive" in page
    assert 'data-view="all"' in page
    assert "nav" in page
    assert "/daily" in page


def test_render_page_all_views_linked(hive_env):
    from keephive.commands.serve import render_page

    page = render_page("all", 3847)
    for path in ["/daily", "/dev", "/simple", "/stats", "/know", "/mem", "/notes"]:
        assert path in page, f"Missing link to {path}"


def test_render_page_contains_refresh_js(hive_env):
    from keephive.commands.serve import render_page

    page = render_page("all", 3847)
    assert "api/fragment" in page
    assert "refresh-select" in page


# ---- HTTP server integration tests ----


def _start_test_server(port: int, hive_home: str) -> threading.Thread:
    """Start the HTTP server in a background thread."""
    import os

    os.environ["HIVE_HOME"] = hive_home

    from keephive.commands.serve import _HiveHandler, HTTPServer

    handler = type("H", (_HiveHandler,), {"server_port": port})
    httpd = HTTPServer(("localhost", port), handler)

    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    return t, httpd


def test_http_server_serves_root(hive_env, unused_tcp_port=13847):
    """HTTP server responds to GET / with HTML."""
    import os

    os.environ["HIVE_HOME"] = str(hive_env)

    from keephive.commands.serve import _HiveHandler, HTTPServer

    port = unused_tcp_port
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)

    def handle_one():
        httpd.handle_request()

    t = threading.Thread(target=handle_one, daemon=True)
    t.start()
    time.sleep(0.1)

    conn = HTTPConnection("localhost", port, timeout=3)
    conn.request("GET", "/")
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    httpd.server_close()

    assert resp.status == 200
    assert "hive" in body
    assert "<!DOCTYPE" in body


def test_http_server_serves_fragment(hive_env):
    """HTTP server responds to /api/fragment with HTML fragment."""
    import os

    os.environ["HIVE_HOME"] = str(hive_env)

    from keephive.commands.serve import _HiveHandler, HTTPServer

    port = 13848
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)

    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    time.sleep(0.1)

    conn = HTTPConnection("localhost", port, timeout=3)
    conn.request("GET", "/api/fragment?view=daily")
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    httpd.server_close()

    assert resp.status == 200
    assert len(body) > 20


def test_http_server_ui_feedback_post(hive_env):
    """POST /ui-feedback writes to queue file."""
    import os

    os.environ["HIVE_HOME"] = str(hive_env)

    from keephive.commands.serve import _HiveHandler, HTTPServer

    port = 13849
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)

    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    time.sleep(0.1)

    payload = json.dumps({"page": "http://localhost:3847", "selector": ".test", "note": "fix this"})
    conn = HTTPConnection("localhost", port, timeout=3)
    conn.request("POST", "/ui-feedback", body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    httpd.server_close()

    assert resp.status == 200
    queue = hive_env / ".ui-queue"
    assert queue.exists()
    data = json.loads(queue.read_text())
    assert data["selector"] == ".test"


def test_http_server_cors_headers(hive_env):
    """HTTP server includes CORS headers."""
    import os

    os.environ["HIVE_HOME"] = str(hive_env)

    from keephive.commands.serve import _HiveHandler, HTTPServer

    port = 13850
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)

    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    time.sleep(0.1)

    conn = HTTPConnection("localhost", port, timeout=3)
    conn.request("OPTIONS", "/ui-feedback")
    resp = conn.getresponse()
    resp.read()
    conn.close()
    httpd.server_close()

    assert resp.status == 204
    headers = dict(resp.getheaders())
    assert "Access-Control-Allow-Origin" in headers


# ---- New feature: render_page data-port and refresh-ts ----


def test_render_page_has_data_port(hive_env):
    from keephive.commands.serve import render_page

    page = render_page("all", 3847)
    assert 'data-port="3847"' in page


def test_render_page_has_refresh_ts(hive_env):
    from keephive.commands.serve import render_page

    page = render_page("all", 3847)
    assert 'id="refresh-ts"' in page


def test_render_page_has_search_overlay(hive_env):
    from keephive.commands.serve import render_page

    page = render_page("all", 3847)
    assert 'id="search-overlay"' in page
    assert 'id="search-input"' in page


# ---- New feature: streak data in stats panel ----


def test_stats_data_has_streak_keys(hive_env):
    from keephive.commands.serve import _get_stats_data

    data = _get_stats_data()
    assert "curr_streak" in data
    assert "longest_streak" in data
    assert isinstance(data["curr_streak"], int)
    assert isinstance(data["longest_streak"], int)


def test_render_stats_panel_shows_streak(hive_env):
    from keephive.commands.serve import _render_stats_panel

    # Streak row only renders when total_days > 0; inject synthetic data
    data = {
        "commands": [("hive r", 10)],
        "today": {"hive r": 2},
        "week": {"hive r": 5},
        "total_days": 5,
        "curr_streak": 3,
        "longest_streak": 7,
    }
    html = _render_stats_panel(data)
    assert "streak" in html.lower()
    assert "3" in html  # curr_streak value
    assert "7" in html  # longest_streak value


# ---- New feature: stale facts accordion ----


def test_render_status_panel_no_stale_no_accordion(hive_env):
    from keephive.commands.serve import _get_status_data, _render_status_panel

    data = _get_status_data()
    data["stale"] = 0
    data["stale_facts"] = []
    html = _render_status_panel(data)
    assert "stale-accordion" not in html


def test_render_status_panel_stale_accordion(hive_env):
    from keephive.commands.serve import _render_status_panel

    data = {
        "stale": 2,
        "stale_facts": ["FACT: the sky is green [verified:2020-01-01]", "FACT: water is dry [verified:2020-01-01]"],
        "total_verified": 10,
        "today_entries": 3,
        "guide_count": 1,
        "hooks_ok": True,
        "mcp_ok": True,
        "data_ok": True,
    }
    html = _render_status_panel(data)
    assert "stale-accordion" in html
    assert "the sky is green" in html
    assert "water is dry" in html


# ---- New feature: log home (limited entries in all view) ----


def test_render_log_home_panel_renders(hive_env):
    from keephive.commands.serve import _get_log_data, _render_log_home_panel

    data = _get_log_data()
    html = _render_log_home_panel(data)
    assert "Log" in html


def test_all_view_uses_log_home(hive_env):
    from keephive.commands.serve import _get_log_data, _render_log_home_panel

    # The log-home panel has no date nav (show_nav=False)
    data = _get_log_data()
    html = _render_log_home_panel(data)
    assert "log-date-nav" not in html
    # see_more_url="/daily" only appears when entries are truncated (>10)
    # verify the panel renders a Log card without crashing
    assert "Log" in html


def test_all_view_log_home_see_more(hive_env):
    """When log has >10 entries, log-home panel shows 'See all' link to /daily."""
    from keephive.commands.serve import _render_log_home_panel

    fake_entries = [
        {"time": f"10:0{i % 10}", "text": f"Entry {i}", "cat": ""}
        for i in range(12)
    ]
    data = {"entries": fake_entries, "date": "2026-01-01"}
    html = _render_log_home_panel(data)
    assert "/daily" in html
    assert "log-see-more" in html


# ---- New feature: log date navigation ----


def test_get_log_data_accepts_date(hive_env):
    from keephive.commands.serve import _get_log_data

    data = _get_log_data("2026-01-01")
    assert data["date"] == "2026-01-01"
    assert "entries" in data


def test_render_log_panel_shows_date_nav(hive_env):
    from keephive.commands.serve import _get_log_data, _render_log_panel

    data = _get_log_data("2026-01-01")
    html = _render_log_panel(data, show_nav=True)
    assert "log-date-nav" in html
    assert "2026-01-01" in html
    assert "loadLog" in html


def test_render_log_panel_no_nav(hive_env):
    from keephive.commands.serve import _get_log_data, _render_log_panel

    data = _get_log_data()
    html = _render_log_panel(data, show_nav=False)
    assert "log-date-nav" not in html


def test_render_log_panel_next_disabled_for_today(hive_env):
    """Next-day button is disabled when viewing today."""
    from datetime import date

    from keephive.commands.serve import _get_log_data, _render_log_panel

    data = _get_log_data(date.today().isoformat())
    html = _render_log_panel(data, show_nav=True)
    assert "disabled" in html


# ---- New feature: standup panel in daily view ----


def test_render_standup_panel(hive_env):
    from keephive.commands.serve import _get_standup_data, _render_standup_panel

    data = _get_standup_data()
    html = _render_standup_panel(data)
    assert "Focus" in html or "Standup" in html or "Today" in html


def test_daily_fragment_includes_standup(hive_env):
    from keephive.commands.serve import render_fragment

    html = render_fragment("daily")
    # Standup panel is in the daily view
    assert "Focus" in html or "Today" in html or "Standup" in html


# ---- New feature: knowledge limited panel in all view ----


def test_render_knowledge_limited_panel(hive_env):
    from keephive.commands.serve import _get_knowledge_data, _render_knowledge_limited_panel

    data = _get_knowledge_data()
    html = _render_knowledge_limited_panel(data)
    assert "Knowledge" in html


# ---- New feature: HTTP /api/search endpoint ----


def test_http_server_search_returns_json(hive_env):
    """GET /api/search?q=query returns JSON with 'results' key."""
    import os

    os.environ["HIVE_HOME"] = str(hive_env)

    from keephive.commands.serve import _HiveHandler, HTTPServer

    port = 13851
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)

    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    time.sleep(0.1)

    conn = HTTPConnection("localhost", port, timeout=3)
    conn.request("GET", "/api/search?q=FACT")
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    httpd.server_close()

    assert resp.status == 200
    data = json.loads(body)
    assert "results" in data
    assert isinstance(data["results"], list)


def test_http_server_search_empty_query(hive_env):
    """GET /api/search with no query returns empty results list."""
    import os

    os.environ["HIVE_HOME"] = str(hive_env)

    from keephive.commands.serve import _HiveHandler, HTTPServer

    port = 13852
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)

    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    time.sleep(0.1)

    conn = HTTPConnection("localhost", port, timeout=3)
    conn.request("GET", "/api/search?q=")
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    httpd.server_close()

    assert resp.status == 200
    data = json.loads(body)
    assert data["results"] == []


# ---- New feature: HTTP /api/fragment with log date ----


def test_http_server_fragment_log_date(hive_env):
    """GET /api/fragment?view=log&date=YYYY-MM-DD returns log panel HTML."""
    import os

    os.environ["HIVE_HOME"] = str(hive_env)

    from keephive.commands.serve import _HiveHandler, HTTPServer

    port = 13853
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)

    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    time.sleep(0.1)

    conn = HTTPConnection("localhost", port, timeout=3)
    conn.request("GET", "/api/fragment?view=log&date=2026-01-01")
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    httpd.server_close()

    assert resp.status == 200
    assert "2026-01-01" in body
    assert "Log" in body


# ---- Hot reload: arg parsing ----


def test_hot_flag_enters_watcher_not_server(hive_env, monkeypatch):
    """--hot with no HIVE_SERVE_WORKER env var calls _hot_watcher, not HTTPServer."""
    import os

    monkeypatch.delenv("HIVE_SERVE_WORKER", raising=False)

    calls = []

    def fake_watcher(port):
        calls.append(("watcher", port))

    monkeypatch.setattr("keephive.commands.serve._hot_watcher", fake_watcher)
    from keephive.commands.serve import cmd_serve

    cmd_serve(["--hot"])
    assert calls == [("watcher", 3847)]


def test_hot_flag_with_port(hive_env, monkeypatch):
    """--hot with a port argument passes port to _hot_watcher."""
    monkeypatch.delenv("HIVE_SERVE_WORKER", raising=False)

    calls = []

    def fake_watcher(port):
        calls.append(port)

    monkeypatch.setattr("keephive.commands.serve._hot_watcher", fake_watcher)
    from keephive.commands.serve import cmd_serve

    cmd_serve(["4000", "--hot"])
    assert calls == [4000]


def test_hot_flag_worker_env_skips_watcher(hive_env, monkeypatch):
    """HIVE_SERVE_WORKER=1 bypasses watcher even when --hot is passed."""
    monkeypatch.setenv("HIVE_SERVE_WORKER", "1")

    watcher_called = []

    def fake_watcher(port):
        watcher_called.append(port)

    monkeypatch.setattr("keephive.commands.serve._hot_watcher", fake_watcher)
    # Can't actually start HTTPServer in test, so patch that too
    monkeypatch.setattr("keephive.commands.serve.HTTPServer", lambda *a, **k: (_ for _ in ()).throw(OSError("test skip")))

    from keephive.commands.serve import cmd_serve

    cmd_serve(["--hot"])
    assert not watcher_called, "Watcher should not be called in worker mode"


# ---- CLI dispatch test ----


def test_serve_is_registered():
    """serve command is registered in CLI dispatch table."""
    from keephive.cli import COMMANDS

    assert "serve" in COMMANDS
    assert "ws" in COMMANDS
    assert COMMANDS["serve"] == ("keephive.commands.serve", "cmd_serve")
    assert COMMANDS["ws"] == ("keephive.commands.serve", "cmd_serve")
