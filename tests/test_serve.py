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


# ---- Fix 7: DONE/AUTO-PROMOTED log classification ----


def test_log_data_classifies_done(hive_env):
    from datetime import date

    from keephive.commands.serve import _get_log_data
    from keephive.storage import daily_file

    today = date.today().isoformat()
    daily_file().write_text(
        f"- [10:00:00] DONE: finished the auth module\n"
        f"- [10:01:00] AUTO-PROMOTED: old fact promoted\n"
    )
    data = _get_log_data()
    cats = {e["cat"] for e in data["entries"]}
    assert "done" in cats
    assert "auto" in cats


def test_log_data_done_cat_not_blank(hive_env):
    from datetime import date

    from keephive.commands.serve import _get_log_data
    from keephive.storage import daily_file

    daily_file().write_text("- [09:00:00] DONE: shipped feature\n")
    data = _get_log_data()
    assert data["entries"][0]["cat"] == "done"


# ---- Fix 1: Accordion toggle animation ----


def test_css_has_accordion_transition(hive_env):
    from keephive.commands.serve import _CSS

    assert "transition:transform" in _CSS
    assert ".acc-header.open .acc-toggle" in _CSS
    assert "rotate(90deg)" in _CSS


def test_js_toggles_open_class_on_header(hive_env):
    from keephive.commands.serve import _JS

    # JS must toggle 'open' on the header element, not just the body
    assert "h.classList.toggle" in _JS
    assert "isOpen" in _JS


# ---- Fix 5: Auto-expand first accordion on /mem and /notes ----


def test_js_auto_expand_mem_notes(hive_env):
    from keephive.commands.serve import _JS

    assert "view==='mem'" in _JS or "view==\\'mem\\'" in _JS
    assert "firstBody" in _JS
    assert "firstHdr" in _JS


# ---- Fix 2: Memory panel uses markdown rendering ----


def test_memory_panel_uses_md_class(hive_env):
    """Memory panel renders content via render_md, not raw mem-line divs."""
    from keephive.commands.serve import _render_memory_panel

    data = {"memory": "## User Preferences\n- Use uv\n- Research first", "rules": ""}
    html = _render_memory_panel(data)
    assert 'class="md"' in html
    # render_md should produce h2 for ##
    assert "<h2>" in html
    assert "mem-line" not in html


def test_memory_panel_rules_use_md_class(hive_env):
    from keephive.commands.serve import _render_memory_panel

    data = {"memory": "", "rules": "- Always verify\n- No assumptions"}
    html = _render_memory_panel(data)
    assert 'class="md"' in html
    assert "mem-line" not in html


def test_memory_panel_empty_shows_empty_div(hive_env):
    from keephive.commands.serve import _render_memory_panel

    data = {"memory": "", "rules": ""}
    html = _render_memory_panel(data)
    assert "Empty" in html


# ---- Fix 3: Log entry type badges ----


def test_log_panel_fact_shows_badge():
    from keephive.commands.serve import _render_log_panel

    data = {"entries": [{"time": "10:00", "text": "FACT: the sky is blue", "cat": "fact"}], "date": "2026-01-01"}
    html = _render_log_panel(data, show_nav=False)
    assert 'log-tag-fact' in html
    assert 'FACT' in html
    # Text span has category class for line coloring AND badge inside
    assert 'class="log-text fact"' in html


def test_log_panel_decision_badge():
    from keephive.commands.serve import _render_log_panel

    data = {"entries": [{"time": "10:01", "text": "DECISION: chose X", "cat": "decision"}], "date": "2026-01-01"}
    html = _render_log_panel(data, show_nav=False)
    assert 'log-tag-decision' in html
    assert 'DEC' in html


def test_log_panel_done_badge():
    from keephive.commands.serve import _render_log_panel

    data = {"entries": [{"time": "10:02", "text": "DONE: finished it", "cat": "done"}], "date": "2026-01-01"}
    html = _render_log_panel(data, show_nav=False)
    assert 'log-tag-done' in html
    assert 'DONE' in html


def test_log_panel_auto_badge():
    from keephive.commands.serve import _render_log_panel

    data = {"entries": [{"time": "10:03", "text": "AUTO-PROMOTED: old fact", "cat": "auto"}], "date": "2026-01-01"}
    html = _render_log_panel(data, show_nav=False)
    assert 'log-tag-auto' in html
    assert 'AUTO' in html


def test_log_panel_no_cat_no_badge():
    from keephive.commands.serve import _render_log_panel

    data = {"entries": [{"time": "10:04", "text": "some plain note", "cat": ""}], "date": "2026-01-01"}
    html = _render_log_panel(data, show_nav=False)
    assert 'log-tag' not in html


def test_css_has_both_badge_and_line_color_classes():
    """Both badge classes AND line-color classes should exist (badge + colored line)."""
    from keephive.commands.serve import _CSS

    # Badge classes for the pill
    assert "log-tag-fact" in _CSS
    assert "log-tag-decision" in _CSS
    # Line-color classes are restored (badge + color = best scannability)
    assert ".fact{color:" in _CSS
    assert ".decision{color:" in _CSS
    # New categories also covered
    assert "done-cat" in _CSS
    assert "auto-cat" in _CSS


# ---- Fix 4: Knowledge section dividers ----


def test_knowledge_panel_shows_guide_divider(hive_env):
    from keephive.commands.serve import _render_knowledge_panel

    data = {
        "guides": [{"name": "my-guide", "content": "# Guide"}],
        "prompts": [],
        "skills": [],
    }
    html = _render_knowledge_panel(data)
    assert 'know-divider' in html
    assert 'Guides' in html


def test_knowledge_panel_shows_all_dividers(hive_env):
    from keephive.commands.serve import _render_knowledge_panel

    data = {
        "guides": [{"name": "g1", "content": "guide"}],
        "prompts": [{"name": "p1", "content": "prompt"}],
        "skills": [{"name": "skill-one", "content": ""}],
    }
    html = _render_knowledge_panel(data)
    assert html.count('know-divider') == 3
    assert 'Guides' in html
    assert 'Prompts' in html
    assert 'Skills' in html


def test_knowledge_panel_no_divider_for_empty_section(hive_env):
    from keephive.commands.serve import _render_knowledge_panel

    data = {"guides": [], "prompts": [], "skills": []}
    html = _render_knowledge_panel(data)
    assert 'know-divider' not in html


# ---- Fix 6: Search result cleanup ----


def test_search_filters_session_lines(hive_env):
    """Session log lines are filtered from search results server-side."""
    import os

    os.environ["HIVE_HOME"] = str(hive_env)
    import threading
    import time
    from http.client import HTTPConnection

    from keephive.commands.serve import _HiveHandler, HTTPServer
    from keephive.storage import fts_search

    # Monkey-patch fts_search to return a mix of real and session results
    import keephive.commands.serve as serve_mod
    orig_search = None

    def fake_search(query, limit=20):
        return [
            {"date": "2026-01-01", "line": "- [10:00:00] FACT: useful memory"},
            {"date": "2026-01-01", "line": "- [13:31:47] session [proj] /Users/test"},
        ]

    port = 13860
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)
    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    time.sleep(0.1)

    import unittest.mock as mock
    with mock.patch("keephive.storage.fts_search", fake_search):
        conn = HTTPConnection("localhost", port, timeout=3)
        conn.request("GET", "/api/search?q=test")
        resp = conn.getresponse()
        body = resp.read().decode()
    conn.close()
    httpd.server_close()

    import json as _json
    data = _json.loads(body)
    lines = [r["line"] for r in data["results"]]
    assert all("session" not in ln for ln in lines), f"Session line leaked: {lines}"
    assert any("FACT" in ln for ln in lines)


def test_search_strips_log_prefix(hive_env):
    """Search results strip the `- [HH:MM:SS] ` prefix."""
    import os
    import threading
    import time
    import unittest.mock as mock
    from http.client import HTTPConnection

    import json as _json

    os.environ["HIVE_HOME"] = str(hive_env)

    from keephive.commands.serve import _HiveHandler, HTTPServer

    def fake_search(query, limit=20):
        return [{"date": "2026-01-01", "line": "- [10:00:00] FACT: clean result"}]

    port = 13861
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)
    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    time.sleep(0.1)

    with mock.patch("keephive.storage.fts_search", fake_search):
        conn = HTTPConnection("localhost", port, timeout=3)
        conn.request("GET", "/api/search?q=fact")
        resp = conn.getresponse()
        body = resp.read().decode()
    conn.close()
    httpd.server_close()

    data = _json.loads(body)
    assert data["results"]
    line = data["results"][0]["line"]
    assert not line.startswith("- ["), f"Prefix not stripped: {line!r}"
    assert "FACT: clean result" in line


# ---- Status panel metric improvements ----


def test_status_data_has_todo_count(hive_env):
    from keephive.commands.serve import _get_status_data

    data = _get_status_data()
    assert "todo_count" in data
    assert isinstance(data["todo_count"], int)


def test_render_status_panel_shows_open_todos(hive_env):
    from keephive.commands.serve import _render_status_panel

    data = {
        "stale": 0, "total_verified": 5, "today_entries": 3,
        "guide_count": 2, "hooks_ok": True, "mcp_ok": True, "data_ok": True,
        "stale_facts": [], "todo_count": 7,
    }
    html = _render_status_panel(data)
    assert "open todos" in html
    assert "7" in html


def test_render_status_panel_clearer_labels(hive_env):
    from keephive.commands.serve import _render_status_panel

    data = {
        "stale": 0, "total_verified": 10, "today_entries": 4,
        "guide_count": 3, "hooks_ok": True, "mcp_ok": True, "data_ok": True,
        "stale_facts": [], "todo_count": 2,
    }
    html = _render_status_panel(data)
    assert "verified facts" in html
    assert "stale facts" in html
    assert "logged today" in html
    # Old cryptic single-word labels should be gone from the metric cells
    assert '<span class="stat-label">facts</span>' not in html
    assert '<span class="stat-label">today</span>' not in html
    assert '<span class="stat-label">guides</span>' not in html


def test_render_status_brief_panel_clearer_labels(hive_env):
    from keephive.commands.serve import _render_status_brief_panel

    data = {
        "stale": 0, "total_verified": 8, "today_entries": 2,
        "guide_count": 1, "todo_count": 3,
    }
    html = _render_status_brief_panel(data)
    assert "verified facts" in html
    assert "logged today" in html
    assert "open todos" in html


# ---- logged yesterday hero stat ----


def test_status_panel_shows_logged_yesterday(hive_env):
    """Status panel must show a 'logged yesterday' stat box."""
    from keephive.commands.serve import _render_status_panel

    data = {
        "stale": 0, "total_verified": 30, "today_entries": 66,
        "yesterday_entries": 42, "guide_count": 12, "todo_count": 15,
        "hooks_ok": True, "mcp_ok": True, "data_ok": True, "stale_facts": [],
    }
    html = _render_status_panel(data)
    assert "logged yesterday" in html
    assert "42" in html


def test_status_data_has_yesterday_entries(hive_env):
    """_get_status_data must include 'yesterday_entries' key."""
    from keephive.commands.serve import _get_status_data

    data = _get_status_data()
    assert "yesterday_entries" in data
    assert isinstance(data["yesterday_entries"], int)


def test_all_view_todos_full_width():
    """In the All view, 'todos' is a single-item row (full-width, no height imbalance)."""
    from keephive.commands.serve import VIEWS

    all_rows = VIEWS["all"]["rows"]
    # Find the row containing "todos"
    todos_row = next((r for r in all_rows if "todos" in r), None)
    assert todos_row is not None, "No row with 'todos' in All view"
    assert len(todos_row) == 1, f"'todos' should be full-width in All view, got row: {todos_row}"


# ---- Round 3: Fix 1 - accordion animation (no textContent swap) ----


def test_js_no_textcontent_swap_in_accordion():
    """JS must not swap ▶/▼ characters — CSS rotation handles the visual."""
    from keephive.commands.serve import _JS

    # The textContent swap was the broken approach; CSS rotate(90deg) is correct
    assert "t.textContent" not in _JS
    assert "\\u25bc" not in _JS or "acc-toggle" not in _JS  # no ▼ assignment


# ---- Round 3: Fix 2 - knowledge guide height cap ----


def test_css_acc_body_md_max_height():
    """acc-body.md must have max-height so expanded guides stay bounded."""
    from keephive.commands.serve import _CSS

    assert ".acc-body.md{" in _CSS
    assert "max-height:480px" in _CSS
    assert "overflow-y:auto" in _CSS


# ---- Round 3: Fix 3 - align-items:start on grid-2 ----


def test_css_grid2_align_items_start():
    """grid-2 must use align-items:start to prevent dead space below short panels."""
    from keephive.commands.serve import _CSS

    assert "align-items:start" in _CSS


# ---- Round 3: Fix 4 - split-pane ----


def test_render_fragment_two_col_uses_split_pane(hive_env):
    """Two-column rows must emit split-pane structure, not grid-2."""
    from keephive.commands.serve import render_fragment

    html = render_fragment("stats")  # stats view has ["stats", "ps"] — a 2-col row
    assert "split-pane" in html
    assert "split-left" in html
    assert "split-right" in html
    assert "split-divider" in html


def test_render_fragment_split_pane_not_grid2(hive_env):
    """Two-column rows must NOT use grid-2."""
    from keephive.commands.serve import render_fragment

    html = render_fragment("stats")
    assert "grid-2" not in html


def test_css_has_split_pane_rules():
    from keephive.commands.serve import _CSS

    assert ".split-pane{" in _CSS
    assert ".split-divider{" in _CSS
    assert ".split-divider::after{" in _CSS


def test_js_has_split_pane_drag():
    from keephive.commands.serve import _JS

    assert "split-divider" in _JS
    assert "mousedown" in _JS
    assert "mousemove" in _JS
    assert "mouseup" in _JS


# ---- Round 3: Fix 6 - no show-more ----


def test_knowledge_panel_no_show_more_btn(hive_env):
    """Knowledge panel must never emit show-more-btn buttons."""
    from keephive.commands.serve import _render_knowledge_panel

    data = {
        "guides": [{"name": f"guide-{i}", "content": f"# Guide {i}"} for i in range(10)],
        "prompts": [{"name": f"prompt-{i}", "content": "text"} for i in range(5)],
        "skills": [],
    }
    html = _render_knowledge_panel(data)
    assert "show-more-btn" not in html
    assert "guide-overflow" not in html


def test_knowledge_panel_shows_all_guides(hive_env):
    """All guides are rendered without truncation."""
    from keephive.commands.serve import _render_knowledge_panel

    data = {
        "guides": [{"name": f"guide-{i}", "content": f"# Guide {i}"} for i in range(5)],
        "prompts": [],
        "skills": [],
    }
    html = _render_knowledge_panel(data)
    for i in range(5):
        assert f"guide-{i}" in html


def test_css_no_show_more_btn_rule():
    from keephive.commands.serve import _CSS

    assert "show-more-btn" not in _CSS
    assert "guide-overflow" not in _CSS


def test_js_no_show_more_handler():
    from keephive.commands.serve import _JS

    assert "show-more-btn" not in _JS


# ---- Round 3: badge prefix stripping ----


def test_log_panel_strips_fact_prefix():
    """When FACT badge is shown, 'FACT: ' prefix is stripped from display text."""
    from keephive.commands.serve import _render_log_panel

    data = {"entries": [{"time": "10:00", "text": "FACT: the sky is blue", "cat": "fact"}], "date": "2026-01-01"}
    html = _render_log_panel(data, show_nav=False)
    assert "log-tag-fact" in html  # badge present
    assert "FACT: the sky is blue" not in html  # full prefixed text gone
    assert "the sky is blue" in html  # payload preserved


def test_log_panel_strips_todo_prefix():
    from keephive.commands.serve import _render_log_panel

    data = {"entries": [{"time": "10:00", "text": "TODO: fix the thing", "cat": "todo"}], "date": "2026-01-01"}
    html = _render_log_panel(data, show_nav=False)
    assert "log-tag-todo" in html
    assert "TODO: fix the thing" not in html
    assert "fix the thing" in html


def test_log_panel_strips_done_prefix():
    from keephive.commands.serve import _render_log_panel

    data = {"entries": [{"time": "10:00", "text": "DONE: shipped feature", "cat": "done"}], "date": "2026-01-01"}
    html = _render_log_panel(data, show_nav=False)
    assert "log-tag-done" in html
    assert "DONE: shipped feature" not in html
    assert "shipped feature" in html


def test_log_panel_strips_auto_promoted_prefix():
    from keephive.commands.serve import _render_log_panel

    data = {"entries": [{"time": "10:00", "text": "AUTO-PROMOTED: old fact text", "cat": "auto"}], "date": "2026-01-01"}
    html = _render_log_panel(data, show_nav=False)
    assert "log-tag-auto" in html
    assert "AUTO-PROMOTED: old fact text" not in html
    assert "old fact text" in html


def test_log_panel_no_cat_no_strip():
    """Entries without a category keep their text unchanged."""
    from keephive.commands.serve import _render_log_panel

    data = {"entries": [{"time": "10:00", "text": "plain note text", "cat": ""}], "date": "2026-01-01"}
    html = _render_log_panel(data, show_nav=False)
    assert "plain note text" in html


# ---- CLI dispatch test ----


def test_serve_is_registered():
    """serve command is registered in CLI dispatch table."""
    from keephive.cli import COMMANDS

    assert "serve" in COMMANDS
    assert "ws" in COMMANDS
    assert COMMANDS["serve"] == ("keephive.commands.serve", "cmd_serve")
    assert COMMANDS["ws"] == ("keephive.commands.serve", "cmd_serve")


# ---- Part 1: Layout fix — memory+notes paired, knowledge full-width ----


def test_all_view_memory_notes_paired():
    """In the All view, memory and notes share a row (2 items)."""
    from keephive.commands.serve import VIEWS

    all_rows = VIEWS["all"]["rows"]
    memory_notes_row = next((r for r in all_rows if "memory" in r and "notes" in r), None)
    assert memory_notes_row is not None, "No row with both 'memory' and 'notes' in All view"
    assert len(memory_notes_row) == 2


def test_all_view_knowledge_full_width():
    """In the All view, knowledge-limited is alone in its row (full-width)."""
    from keephive.commands.serve import VIEWS

    all_rows = VIEWS["all"]["rows"]
    know_row = next((r for r in all_rows if any("knowledge" in p for p in r)), None)
    assert know_row is not None, "No knowledge row in All view"
    assert len(know_row) == 1


# ---- Part 2: Web CRUD — POST endpoints ----


def test_http_post_remember(hive_env):
    """POST /api/remember appends entry to daily log."""
    import os

    os.environ["HIVE_HOME"] = str(hive_env)

    from keephive.commands.serve import _HiveHandler, HTTPServer

    port = 13870
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)

    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    time.sleep(0.1)

    payload = json.dumps({"text": "FACT: test from web"})
    conn = HTTPConnection("localhost", port, timeout=3)
    conn.request("POST", "/api/remember", body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    httpd.server_close()

    assert resp.status == 200
    data = json.loads(body)
    assert data["ok"] is True

    # Entry should appear in today's daily log
    from keephive.storage import daily_file
    log_text = daily_file().read_text()
    assert "FACT: test from web" in log_text


def test_http_post_todo_add(hive_env):
    """POST /api/todo/add appends TODO entry to daily log."""
    import os

    os.environ["HIVE_HOME"] = str(hive_env)

    from keephive.commands.serve import _HiveHandler, HTTPServer

    port = 13871
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)

    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    time.sleep(0.1)

    payload = json.dumps({"text": "fix the widget"})
    conn = HTTPConnection("localhost", port, timeout=3)
    conn.request("POST", "/api/todo/add", body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    httpd.server_close()

    assert resp.status == 200
    data = json.loads(body)
    assert data["ok"] is True

    from keephive.storage import daily_file
    log_text = daily_file().read_text()
    assert "TODO: fix the widget" in log_text


def test_http_post_todo_done(hive_env):
    """POST /api/todo/done marks a matching TODO as complete."""
    import os

    os.environ["HIVE_HOME"] = str(hive_env)

    # Pre-populate a TODO in the daily log
    from datetime import datetime

    from keephive.storage import append_to_daily, daily_file

    ts = datetime.now().strftime("%H:%M:%S")
    append_to_daily(f"- [{ts}] TODO: complete this specific task")

    from keephive.commands.serve import _HiveHandler, HTTPServer

    port = 13872
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)

    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    time.sleep(0.1)

    payload = json.dumps({"pattern": "complete this specific task"})
    conn = HTTPConnection("localhost", port, timeout=3)
    conn.request("POST", "/api/todo/done", body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    httpd.server_close()

    assert resp.status == 200
    data = json.loads(body)
    assert data["ok"] is True

    # DONE entry should be in the log
    log_text = daily_file().read_text()
    assert "DONE: complete this specific task" in log_text


def test_http_post_note_append(hive_env):
    """POST /api/note/append appends text to the active note slot."""
    import os

    os.environ["HIVE_HOME"] = str(hive_env)

    from keephive.commands.serve import _HiveHandler, HTTPServer

    port = 13873
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)

    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    time.sleep(0.1)

    payload = json.dumps({"text": "a new note line"})
    conn = HTTPConnection("localhost", port, timeout=3)
    conn.request("POST", "/api/note/append", body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    httpd.server_close()

    assert resp.status == 200
    data = json.loads(body)
    assert data["ok"] is True

    from keephive.storage import active_slot, slot_file
    note_text = slot_file(active_slot()).read_text()
    assert "a new note line" in note_text


def test_http_post_remember_empty_text(hive_env):
    """POST /api/remember with empty text returns ok=False."""
    import os

    os.environ["HIVE_HOME"] = str(hive_env)

    from keephive.commands.serve import _HiveHandler, HTTPServer

    port = 13874
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)

    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    time.sleep(0.1)

    payload = json.dumps({"text": ""})
    conn = HTTPConnection("localhost", port, timeout=3)
    conn.request("POST", "/api/remember", body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    httpd.server_close()

    assert resp.status == 200
    data = json.loads(body)
    assert data["ok"] is False


def test_http_post_unknown_path(hive_env):
    """POST to unknown path returns 404."""
    import os

    os.environ["HIVE_HOME"] = str(hive_env)

    from keephive.commands.serve import _HiveHandler, HTTPServer

    port = 13875
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)

    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    time.sleep(0.1)

    payload = json.dumps({"text": "hello"})
    conn = HTTPConnection("localhost", port, timeout=3)
    conn.request("POST", "/api/nonexistent", body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    resp.read()
    conn.close()
    httpd.server_close()

    assert resp.status == 404


# ---- Part 2: Frontend forms in HTML ----


def test_log_panel_has_quick_remember_form(hive_env):
    """Log panel HTML includes the quick-remember input form."""
    from keephive.commands.serve import _get_log_data, _render_log_panel

    data = _get_log_data()
    html = _render_log_panel(data)
    assert "panel-input" in html
    assert "/api/remember" in html
    assert 'placeholder' in html


def test_todo_panel_has_add_form(hive_env):
    """TODO panel HTML includes the add-todo input form."""
    from keephive.commands.serve import _get_todo_data, _render_todo_panel

    data = _get_todo_data()
    html = _render_todo_panel(data)
    assert "panel-input" in html
    assert "/api/todo/add" in html


def test_todo_panel_has_done_buttons(hive_env):
    """TODO panel renders ✓ button for each TODO item."""
    from keephive.commands.serve import _render_todo_panel

    from datetime import date
    today = date.today().isoformat()
    data = {
        "todos": [(today, "10:00", "fix the thing"), (today, "10:01", "do the other")],
        "due": [],
    }
    html = _render_todo_panel(data)
    assert "todo-done-btn" in html
    assert "fix the thing" in html
    assert "do the other" in html


def test_notes_panel_has_append_form(hive_env):
    """Notes panel HTML includes the note-append input form."""
    from keephive.commands.serve import _get_notes_data, _render_notes_panel

    data = _get_notes_data()
    html = _render_notes_panel(data)
    assert "panel-input" in html
    assert "/api/note/append" in html


def test_css_has_panel_input_styles():
    """CSS includes panel-input and todo-done-btn rules."""
    from keephive.commands.serve import _CSS

    assert ".panel-input{" in _CSS
    assert ".panel-input input{" in _CSS
    assert ".todo-done-btn{" in _CSS


def test_js_has_crud_form_handler():
    """JS includes form submission handler for panel-input forms."""
    from keephive.commands.serve import _JS

    assert "panel-input" in _JS
    assert "/api/remember" not in _JS  # generic handler, not hardcoded URL
    assert "dataset.action" in _JS  # reads action from form's data-action attribute
    assert "dataset.field" in _JS


def test_js_has_todo_done_handler():
    """JS includes click handler for todo-done-btn."""
    from keephive.commands.serve import _JS

    assert "todo-done-btn" in _JS
    assert "/api/todo/done" in _JS
    assert "dataset.pattern" in _JS


# ---- Part 3: Skill content — expandable skills with SKILL.md ----


def test_knowledge_data_reads_skill_md(hive_env, monkeypatch, tmp_path):
    """_get_knowledge_data reads SKILL.md from skill directories."""
    from pathlib import Path

    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    skill = skills_dir / "my-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# My Skill\nDoes things.")

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    from keephive.commands.serve import _get_knowledge_data

    data = _get_knowledge_data()
    skill_item = next((s for s in data["skills"] if s["name"] == "my-skill"), None)
    assert skill_item is not None
    assert "Does things" in skill_item["content"]


def test_knowledge_data_skill_without_skill_md(hive_env, monkeypatch, tmp_path):
    """Skills without SKILL.md get empty content string."""
    from pathlib import Path

    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    skill = skills_dir / "bare-skill"
    skill.mkdir()
    # No SKILL.md

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    from keephive.commands.serve import _get_knowledge_data

    data = _get_knowledge_data()
    skill_item = next((s for s in data["skills"] if s["name"] == "bare-skill"), None)
    assert skill_item is not None
    assert skill_item["content"] == ""


def test_knowledge_panel_skills_expandable_with_content(hive_env):
    """Skills with content render with chevron toggle and body."""
    from keephive.commands.serve import _render_knowledge_panel

    data = {
        "guides": [],
        "prompts": [],
        "skills": [
            {"name": "chrome-devtools", "content": "# Chrome DevTools\nUse for testing."},
        ],
    }
    html = _render_knowledge_panel(data)
    assert "Chrome DevTools" in html
    assert "&#9654;" in html  # chevron toggle
    assert "acc-body" in html  # expandable body present


def test_knowledge_panel_skills_no_content_greyed(hive_env):
    """Skills without content render with greyed em-dash (non-expandable)."""
    from keephive.commands.serve import _render_knowledge_panel

    data = {
        "guides": [],
        "prompts": [],
        "skills": [
            {"name": "no-content-skill", "content": ""},
        ],
    }
    html = _render_knowledge_panel(data)
    assert "no-content-skill" in html
    assert "&#8212;" in html  # em-dash for non-expandable
    # No acc-body since content is empty
    assert "acc-body" not in html


def test_knowledge_panel_mixed_skills(hive_env):
    """Panel handles mix of skills with and without content."""
    from keephive.commands.serve import _render_knowledge_panel

    data = {
        "guides": [],
        "prompts": [],
        "skills": [
            {"name": "rich-skill", "content": "# Rich\nHas content."},
            {"name": "bare-skill", "content": ""},
        ],
    }
    html = _render_knowledge_panel(data)
    assert "rich-skill" in html
    assert "bare-skill" in html
    assert "&#9654;" in html   # expandable chevron for rich-skill
    assert "&#8212;" in html   # em-dash for bare-skill


# ---- UX fixes: Dev layout, status bars, note preview ----


def test_dev_view_uses_knowledge_limited():
    """Dev view uses knowledge-limited (not full knowledge) to avoid height imbalance."""
    from keephive.commands.serve import VIEWS

    dev_rows = VIEWS["dev"]["rows"]
    know_row = next((r for r in dev_rows if any("knowledge" in p for p in r)), None)
    assert know_row is not None, "No knowledge panel in Dev view"
    assert "knowledge-limited" in know_row, "Dev view should use knowledge-limited, not full knowledge"
    assert "knowledge" not in [p for p in know_row if p != "knowledge-limited"], \
        "Dev view must not use the full 'knowledge' panel"


def test_know_view_has_status_bar():
    """Know view includes status-brief row for context."""
    from keephive.commands.serve import VIEWS

    rows = VIEWS["know"]["rows"]
    assert any("status-brief" in r for r in rows), "/know should have status-brief row"


def test_mem_view_has_status_bar():
    """Mem view includes status-brief row for context."""
    from keephive.commands.serve import VIEWS

    rows = VIEWS["mem"]["rows"]
    assert any("status-brief" in r for r in rows), "/mem should have status-brief row"


def test_notes_view_has_status_bar():
    """Notes view includes status-brief row for context."""
    from keephive.commands.serve import VIEWS

    rows = VIEWS["notes"]["rows"]
    assert any("status-brief" in r for r in rows), "/notes should have status-brief row"


def test_notes_panel_preview_in_header(hive_env, tmp_path):
    """Collapsed note accordions show a text preview snippet in the header."""
    from keephive.commands.serve import _render_notes_panel

    data = {
        "slots": [
            {
                "slot": 1,
                "content": "Slot 1 ★\nThis is the important content line.",
                "lines": 2,
                "active": True,
            },
            {
                "slot": 2,
                "content": "Slot 2\nAnother note here.",
                "lines": 2,
                "active": False,
            },
        ]
    }
    html = _render_notes_panel(data)
    # Preview class is present
    assert "acc-preview" in html
    # First non-header line used as preview
    assert "This is the important content line" in html
    assert "Another note here" in html


def test_notes_panel_strips_slot_header_from_body(hive_env):
    """Note body strips 'Slot N ★' metadata line; badge still shows it."""
    from keephive.commands.serve import _render_notes_panel

    data = {
        "slots": [
            {
                "slot": 1,
                "content": "Slot 1 ★\nReal user content here.",
                "lines": 2,
                "active": True,
            }
        ]
    }
    html = _render_notes_panel(data)
    # The slot-badge shows "Slot 1 ★" (via badge span)
    assert "slot-badge" in html
    # "Slot 1 ★" should NOT appear as raw text in the markdown body
    # (It appears in badge only, not duplicated in acc-body)
    # Count occurrences — badge = 1, body = 0 → total exactly 1
    badge_count = html.count("Slot 1")
    # Once in the badge span, not duplicated in the rendered markdown body
    assert badge_count == 1, f"'Slot 1' appeared {badge_count} times, expected 1 (badge only)"


def test_css_has_acc_preview_styles():
    """CSS includes acc-preview rule for collapsed note slot previews."""
    from keephive.commands.serve import _CSS

    assert ".acc-preview{" in _CSS
    assert ".acc-header.open .acc-preview{display:none}" in _CSS


# ---- P5: Activity sparkline ----


def test_stats_data_has_daily_spark(hive_env):
    """_get_stats_data returns 14-day daily_spark list."""
    from keephive.commands.serve import _get_stats_data

    data = _get_stats_data()
    assert "daily_spark" in data
    spark = data["daily_spark"]
    assert len(spark) == 14
    # Each entry is (label, count)
    for label, count in spark:
        assert isinstance(label, str)
        assert isinstance(count, int)
        assert count >= 0
    # Last entry is today
    from datetime import date

    assert date.today().strftime("%b %d") in spark[-1][0]


def test_stats_panel_renders_sparkline_when_data(hive_env):
    """_render_stats_panel renders sparkline bars when daily_spark has nonzero counts."""
    from keephive.commands.serve import _render_stats_panel

    data = {
        "commands": [],
        "today": {},
        "week": {},
        "total_days": 5,
        "curr_streak": 3,
        "longest_streak": 5,
        "projects": [],
        "daily_spark": [("Feb 01", 0), ("Feb 02", 10), ("Feb 03", 5), ("Feb 04", 0), ("Feb 05", 20)],
    }
    html = _render_stats_panel(data)
    assert "spark-bar" in html
    assert "spark-bar today" in html
    # Today bar (last item) should be green
    assert "today" in html


def test_stats_panel_no_sparkline_when_all_zero(hive_env):
    """_render_stats_panel skips sparkline when all counts are 0."""
    from keephive.commands.serve import _render_stats_panel

    data = {
        "commands": [],
        "today": {},
        "week": {},
        "total_days": 0,
        "curr_streak": 0,
        "longest_streak": 0,
        "projects": [],
        "daily_spark": [("Feb 01", 0), ("Feb 02", 0)],
    }
    html = _render_stats_panel(data)
    assert "spark-bar" not in html


def test_css_has_sparkline_styles():
    """CSS includes sparkline and spark-bar rules."""
    from keephive.commands.serve import _CSS

    assert ".sparkline{" in _CSS
    assert ".spark-bar{" in _CSS
    assert ".spark-bar.today{" in _CSS


# ---- P6: Log type filter ----


def test_log_panel_entries_have_data_type_attribute(hive_env):
    """Log entries include data-type attribute for client-side filtering."""
    from keephive.commands.serve import _render_log_panel

    data = {
        "entries": [
            {"time": "10:00:00", "text": "FACT: something", "cat": "fact"},
            {"time": "10:01:00", "text": "TODO: do it", "cat": "todo"},
        ],
        "date": "2026-02-20",
    }
    html = _render_log_panel(data)
    assert 'data-type="fact"' in html
    assert 'data-type="todo"' in html


def test_log_panel_filter_bar_shown_when_diverse_and_large(hive_env):
    """Filter bar appears when > 10 entries with multiple types."""
    from keephive.commands.serve import _render_log_panel

    entries = []
    for i in range(6):
        entries.append({"time": f"10:0{i}:00", "text": f"FACT: f{i}", "cat": "fact"})
    for i in range(6):
        entries.append({"time": f"11:0{i}:00", "text": f"TODO: t{i}", "cat": "todo"})

    data = {"entries": entries, "date": "2026-02-20"}
    html = _render_log_panel(data)
    assert "log-filter" in html
    assert "log-filter-btn" in html
    # All button is active initially
    assert 'log-filter-btn active" data-type=""' in html


def test_log_panel_filter_bar_hidden_when_few_entries(hive_env):
    """Filter bar is absent when <= 10 entries."""
    from keephive.commands.serve import _render_log_panel

    data = {
        "entries": [
            {"time": "10:00:00", "text": "FACT: x", "cat": "fact"},
            {"time": "10:01:00", "text": "TODO: y", "cat": "todo"},
        ],
        "date": "2026-02-20",
    }
    html = _render_log_panel(data)
    assert "log-filter" not in html


def test_log_panel_filter_bar_hidden_when_single_type(hive_env):
    """Filter bar is absent when all entries are same type."""
    from keephive.commands.serve import _render_log_panel

    entries = [{"time": f"10:0{i}:00", "text": f"FACT: x{i}", "cat": "fact"} for i in range(12)]
    data = {"entries": entries, "date": "2026-02-20"}
    html = _render_log_panel(data)
    assert "log-filter" not in html


def test_css_has_log_filter_styles():
    """CSS includes log-filter and log-filter-btn rules."""
    from keephive.commands.serve import _CSS

    assert ".log-filter{" in _CSS
    assert ".log-filter-btn{" in _CSS
    assert ".log-entry.filtered{" in _CSS


def test_js_has_log_filter_handler():
    """JS includes log-filter-btn click handler."""
    from keephive.commands.serve import _JS

    assert "log-filter-btn" in _JS
    assert "filtered" in _JS


# ---- P7: Note slot switcher ----


def test_notes_panel_renders_slot_switcher(hive_env):
    """Notes panel includes slot switcher buttons."""
    from keephive.commands.serve import _render_notes_panel

    data = {
        "slots": [
            {"slot": 1, "content": "active note", "lines": 1, "active": True},
            {"slot": 2, "content": "second note", "lines": 1, "active": False},
        ]
    }
    html = _render_notes_panel(data)
    assert "slot-switcher" in html
    assert "slot-btn" in html
    assert "switchNote(1)" in html
    assert "switchNote(2)" in html


def test_notes_panel_active_slot_button_has_active_class(hive_env):
    """Active slot button has 'active' CSS class."""
    from keephive.commands.serve import _render_notes_panel

    data = {
        "slots": [
            {"slot": 1, "content": "content", "lines": 1, "active": False},
            {"slot": 2, "content": "active", "lines": 1, "active": True},
        ]
    }
    html = _render_notes_panel(data)
    # slot-btn active should appear for slot 2
    assert 'slot-btn active" onclick="switchNote(2)"' in html
    # slot 1 should not be active
    assert 'slot-btn" onclick="switchNote(1)"' in html


def test_api_note_switch_sets_active_slot(hive_env):
    """POST /api/note/switch calls set_active_slot and returns ok."""
    import os

    os.environ["HIVE_HOME"] = str(hive_env)

    from keephive.commands.serve import _HiveHandler, HTTPServer

    port = 13876
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)
    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    time.sleep(0.1)

    payload = json.dumps({"slot": 2})
    conn = HTTPConnection("localhost", port, timeout=3)
    conn.request("POST", "/api/note/switch", body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    httpd.server_close()

    assert resp.status == 200
    data = json.loads(body)
    assert data["ok"] is True

    # Verify the active slot was actually switched
    from keephive.storage import active_slot
    assert active_slot() == 2


def test_api_note_switch_rejects_invalid_slot(hive_env):
    """POST /api/note/switch rejects slot=0."""
    import os

    os.environ["HIVE_HOME"] = str(hive_env)

    from keephive.commands.serve import _HiveHandler, HTTPServer

    port = 13877
    _HiveHandler.server_port = port
    httpd = HTTPServer(("localhost", port), _HiveHandler)
    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    time.sleep(0.1)

    payload = json.dumps({"slot": 0})
    conn = HTTPConnection("localhost", port, timeout=3)
    conn.request("POST", "/api/note/switch", body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    httpd.server_close()

    assert resp.status == 200
    data = json.loads(body)
    assert data["ok"] is False


def test_css_has_slot_switcher_styles():
    """CSS includes slot-switcher and slot-btn rules."""
    from keephive.commands.serve import _CSS

    assert ".slot-switcher{" in _CSS
    assert ".slot-btn{" in _CSS
    assert ".slot-btn.active{" in _CSS


def test_js_has_switch_note_function():
    """JS includes switchNote function calling /api/note/switch."""
    from keephive.commands.serve import _JS

    assert "switchNote" in _JS
    assert "/api/note/switch" in _JS
