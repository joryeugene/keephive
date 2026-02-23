"""hive serve: live web dashboard for keephive data.

Serves a local web dashboard at localhost:3847 (default).
Views: / (home), /dev, /know (tabbed: guides/memory/notes), /stats

Usage: hive serve [port] [--hot]
       --hot   Watch source files, restart server on change
"""

from __future__ import annotations

import base64
import hashlib
import html as _html
import json
import os
import re
import sys
import webbrowser
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from keephive.llm import available_backends, get_backend_state
from keephive.llm.pending import list_pending, pending_count
from keephive.platforms import platform_specs, read_hook_manifest
from keephive.settings import get_setting
from keephive.skillpack import get_record as get_skill_record
from keephive.telemetry import read_events

DEFAULT_PORT = 3847

_FAVICON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><polygon points="16,2 28,9 28,23 16,30 4,23 4,9" fill="#f59e0b" stroke="#d97706" stroke-width="2"/><polygon points="16,8 22,12 22,20 16,24 10,20 10,12" fill="#fbbf24"/></svg>'
_FAVICON_SVG_URI = "data:image/svg+xml;base64," + base64.b64encode(_FAVICON_SVG.encode()).decode()


def _load_data_uri(filename: str, mime: str) -> str:
    """Load a file from keephive.data as a base64 data URI."""
    try:
        from importlib import resources

        ref = resources.files("keephive.data").joinpath(filename)
        data = ref.read_bytes()
        return f"data:{mime};base64," + base64.b64encode(data).decode()
    except Exception:
        return ""


def _keepbee_data_uri() -> str:
    """Load keepbee.gif as a base64 data URI for the nav brand logo."""
    return _load_data_uri("keepbee.gif", "image/gif")


def _mascot_data_uri() -> str:
    """Load mascot.png as a base64 data URI for the settings page."""
    return _load_data_uri("mascot.png", "image/png")


_FAVICON: str | None = None


def _get_favicon() -> str:
    """Return keepbee GIF favicon, falling back to SVG hexagon."""
    global _FAVICON  # noqa: PLW0603
    if _FAVICON is None:
        gif_uri = _keepbee_data_uri()
        _FAVICON = gif_uri if gif_uri else _FAVICON_SVG_URI
    return _FAVICON


# ---- Markdown renderer ----

_INLINE_RE = [
    (re.compile(r"\*\*\*(.+?)\*\*\*"), r"<strong><em>\1</em></strong>"),
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"\*([^\s*][^*]*?)\*"), r"<em>\1</em>"),
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), r'<a href="\2">\1</a>'),
]


def _inline_md(text: str) -> str:
    text = _html.escape(text)
    for pat, repl in _INLINE_RE:
        text = pat.sub(repl, text)
    return text


def render_md(text: str) -> str:
    """Lightweight markdown renderer covering keephive guide patterns."""
    lines = text.split("\n")
    result: list[str] = []
    in_code = False
    code_acc: list[str] = []
    in_list = False
    in_table = False
    table_header_done = False

    for _, line in enumerate(lines):
        # Code fences
        if line.startswith("```"):
            if in_code:
                escaped = "\n".join(code_acc)
                result.append(f'<pre class="code-block"><code>{escaped}</code></pre>')
                code_acc = []
                in_code = False
            else:
                if in_list:
                    result.append("</ul>")
                    in_list = False
                if in_table:
                    result.append("</tbody></table>")
                    in_table = False
                in_code = True
            continue

        if in_code:
            code_acc.append(_html.escape(line))
            continue

        # Table rows
        if line.startswith("|") and "|" in line[1:]:
            stripped = line.strip()
            # Separator row
            if re.match(r"^\|[\s\-\|:]+\|$", stripped):
                if in_table and not table_header_done:
                    result.append("</thead><tbody>")
                    table_header_done = True
                continue
            if in_list:
                result.append("</ul>")
                in_list = False
            if not in_table:
                result.append('<table class="md-table"><thead>')
                in_table = True
                table_header_done = False
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            tag = "th" if not table_header_done else "td"
            row_html = "".join(f"<{tag}>{_inline_md(c)}</{tag}>" for c in cells)
            result.append(f"<tr>{row_html}</tr>")
            continue

        if in_table:
            result.append("</tbody></table>")
            in_table = False

        # Horizontal rule
        if re.match(r"^[-*_]{3,}$", line.strip()):
            if in_list:
                result.append("</ul>")
                in_list = False
            result.append("<hr>")
            continue

        # Headers
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            if in_list:
                result.append("</ul>")
                in_list = False
            level = len(m.group(1))
            result.append(f"<h{level}>{_inline_md(m.group(2))}</h{level}>")
            continue

        # List items
        m = re.match(r"^\s*[-*]\s+(.*)", line)
        if m:
            if not in_list:
                result.append("<ul>")
                in_list = True
            result.append(f"<li>{_inline_md(m.group(1))}</li>")
            continue

        # Close list on non-list line
        if in_list:
            result.append("</ul>")
            in_list = False

        # Empty line
        if not line.strip():
            continue

        result.append(f"<p>{_inline_md(line)}</p>")

    if in_code:
        result.append(f'<pre class="code-block"><code>{chr(10).join(code_acc)}</code></pre>')
    if in_list:
        result.append("</ul>")
    if in_table:
        result.append("</tbody></table>")

    return "\n".join(result)


# ---- CSS + JS ----

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9;font-size:13px;line-height:1.6;letter-spacing:-0.006em;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
nav{background:#161b22;border-bottom:1px solid #30363d;padding:0 16px;display:flex;align-items:center;gap:2px;position:sticky;top:0;z-index:100}
.nav-brand{color:#f0f6fc;font-weight:700;font-size:14px;padding:10px 10px 10px 0;margin-right:10px;border-right:1px solid #30363d;text-decoration:none;display:flex;align-items:center;gap:6px}
.nav-logo{width:28px;height:28px;image-rendering:pixelated}
.profile-badge{color:#58a6ff;font-size:11px;font-weight:400;padding:2px 6px;border:1px solid #30363d;border-radius:3px;margin-left:4px;vertical-align:middle}
.nav-tab{color:#8b949e;text-decoration:none;padding:10px 10px;border-bottom:2px solid transparent;font-size:12px;white-space:nowrap;transition:color .1s}
.nav-tab:hover{color:#c9d1d9}.nav-tab.active{color:#f0f6fc;border-bottom-color:#58a6ff;font-weight:600}
.nav-right{margin-left:auto;display:flex;align-items:center;gap:8px;padding-left:12px}
.refresh-label{color:#8b949e;font-size:12px}
select.refresh-select{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:3px 8px;border-radius:4px;font-size:12px;cursor:pointer}
#refresh-ts{color:#6e7681;font-size:11px;min-width:88px;text-align:right}
#search-input{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:3px 8px;border-radius:4px;font-size:12px;width:140px;outline:none}
#search-input:focus{border-color:#58a6ff}
#search-input::placeholder{color:#6e7681}
main{max-width:1400px;margin:0 auto;padding:16px}
.grid-row{display:grid;gap:12px;margin-bottom:12px;align-items:start}
.grid-cols-1{grid-template-columns:1fr}
.grid-cols-2{grid-template-columns:1fr 1fr}
.grid-cols-3{grid-template-columns:1fr 1fr 1fr}
@media(max-width:900px){.grid-cols-2,.grid-cols-3{grid-template-columns:1fr}}
.layout-cols{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.layout-col{display:flex;flex-direction:column;gap:12px}
.layout-col>.grid-panel>.card{margin-bottom:0}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin-bottom:12px;transition:border-color .1s}
.card-header{padding:7px 14px;background:#1e252e;border-bottom:1px solid #30363d;display:flex;align-items:center;justify-content:space-between;gap:8px}
.card-title{font-weight:600;font-size:13px;color:#f0f6fc}
.card-meta{color:#6e7681;font-size:12px}
.card-body{padding:10px 14px}
.stat-row{display:flex;gap:16px 24px;flex-wrap:wrap;margin-bottom:8px;justify-content:center}
.stat-item{display:flex;flex-direction:column;gap:2px;align-items:center;text-align:center;min-width:64px}
.stat-value{font-size:22px;font-weight:700;color:#f0f6fc}
.stat-value.warn{color:#e3b341}.stat-value.err{color:#f85149}.stat-value.ok{color:#3fb950}
.stat-label{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.02em;white-space:nowrap}
.pulse-hero .card-body{padding:16px 24px}
.pulse-kpi{display:flex;flex-direction:column;align-items:center;gap:2px}
.pulse-kpi-val{font-size:16px;font-weight:700;color:#e6edf3}
.pulse-kpi-label{font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:.02em;white-space:nowrap}
.metric-hero{font-size:36px;font-weight:700;color:#f0f6fc;line-height:1}
.metric-hero-unit{font-size:16px;color:#484f58;font-weight:400}
.metric-hero-delta{font-size:14px;margin-left:4px}
.metric-hero-delta.up{color:#3fb950}
.metric-hero-delta.down{color:#f85149}
.metric-hero-delta.flat{color:#484f58}
.kpi-row{display:flex;justify-content:center;gap:20px;flex-wrap:wrap;margin:8px 0}
.kpi-item{text-align:center;min-width:48px}
.kpi-value{font-size:18px;font-weight:700;color:#e6edf3;line-height:1.2}
.kpi-label{font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-top:2px}
.gauge-row{display:flex;align-items:center;gap:8px;margin-bottom:5px}
.gauge-label{font-size:11px;color:#8b949e;width:110px;text-align:right;flex-shrink:0}
.gauge-track{flex:1;height:6px;border-radius:3px;background:#21262d}
.gauge-fill{height:100%;border-radius:3px;transition:width .3s ease}
.gauge-pct{font-size:11px;color:#8b949e;width:36px;text-align:right;flex-shrink:0}
.sparkline-unicode{font-family:monospace;font-size:14px;letter-spacing:1px;color:#58a6ff}
.sparkline-axis{display:flex;justify-content:space-between;font-size:9px;color:#484f58;margin-top:1px}
.health-dots{display:flex;gap:6px;align-items:center}
.health-dot{width:6px;height:6px;border-radius:50%;display:inline-block}
.health-dot.ok{background:#3fb950}
.health-dot.warn{background:#e3b341}
.health-dot.off{background:#484f58}
.action-timeline{display:flex;gap:12px;flex-wrap:wrap;margin-top:8px}
.action-item{font-size:11px;display:flex;align-items:center;gap:4px}
.action-dot{width:8px;height:8px;border-radius:50%}
.action-dot.recent{background:#3fb950}
.action-dot.overdue{background:#e3b341;border:1px solid #e3b34155}
.action-dot.never{background:none;border:1px solid #484f58}
.action-label{color:#8b949e}
.action-ago{color:#484f58;font-size:10px}
.health-row{display:flex;gap:12px;margin-top:6px;justify-content:center}
.dot-ok{color:#3fb950}.dot-off{color:#6e7681}
.health-label{font-size:12px;color:#8b949e}
.stale-accordion{margin-top:8px}
.stale-summary{cursor:pointer;color:#e3b341;font-size:12px;list-style:none;padding:2px 0}
.stale-summary::-webkit-details-marker{display:none}
.stale-summary::marker{display:none}
.status-divider{border-top:1px solid #21262d;margin:6px 0}
.status-brief{font-size:12px;color:#8b949e;padding:4px 0}
.status-brief span{color:#c9d1d9;font-weight:600}
.log-date-nav{display:flex;align-items:center;gap:4px}
.date-nav-btn{background:none;border:1px solid #30363d;color:#8b949e;padding:1px 6px;border-radius:3px;cursor:pointer;font-size:14px;line-height:1.4}
.date-nav-btn:hover:not([disabled]){color:#c9d1d9;border-color:#58a6ff}
.date-nav-btn:disabled{opacity:0.3;cursor:default}
.log-date-label{font-size:12px;color:#6e7681;min-width:82px;text-align:center}
.log-entry{display:flex;gap:8px;padding:5px 10px;border-bottom:1px solid #21262d;font-size:12px;border-radius:4px;transition:background .1s}
.log-entry:last-child{border-bottom:none}
.log-entry:hover{background:#1c2128}
.log-time{color:#6e7681;font-family:monospace;min-width:52px;flex-shrink:0}
.log-text{flex:1;color:#c9d1d9;word-break:break-word}
.log-tag{display:inline-block;padding:0 5px;border-radius:3px;font-size:10px;font-weight:600;letter-spacing:.03em;margin-right:5px;vertical-align:middle;line-height:1.6}
.log-tag-fact{background:#1c3552;color:#79c0ff}
.log-tag-decision{background:#2c1f52;color:#d2a8ff}
.log-tag-insight{background:#0d2e1a;color:#56d364}
.log-tag-todo{background:#3d2e00;color:#e3b341}
.log-tag-correction{background:#3d1a00;color:#ffa657}
.log-tag-done{background:#0d2e1a;color:#3fb950}
.log-tag-auto{background:#1c2128;color:#8b949e}
.fact{color:#79c0ff}.decision{color:#d2a8ff}.insight{color:#56d364}
.todo-color{color:#e3b341}.correction{color:#ffa657}.done-cat{color:#3fb950}.auto-cat{color:#8b949e}
.log-see-more{display:block;padding:6px 0;font-size:12px;color:#58a6ff;text-decoration:none;text-align:center}
.log-see-more:hover{color:#79c0ff}
.log-show-more{display:flex;gap:8px;justify-content:center;padding:8px 0 4px}
.log-show-more button{font-size:11px;padding:3px 10px;border-radius:10px;cursor:pointer;border:1px solid #30363d;background:#21262d;color:#8b949e;transition:border-color .1s,color .1s}
.log-show-more button:hover{border-color:#58a6ff;color:#c9d1d9}
.todo-item{padding:5px 10px;border-bottom:1px solid #21262d;display:flex;gap:8px;align-items:baseline;font-size:12px;border-radius:4px;transition:background .1s}
.todo-item:last-child{border-bottom:none}
.todo-item:hover{background:#1c2128}
.todo-age{color:#6e7681;font-size:11px;min-width:36px;text-align:right;flex-shrink:0}
.todo-age.old{color:#e3b341}.todo-age.vold{color:#f85149}
.todo-text{flex:1;color:#c9d1d9}
.recurring-item{display:flex;gap:8px;padding:6px 10px;border-bottom:1px solid #21262d;font-size:12px;border-radius:4px;transition:background .15s}
.recurring-item:last-child{border-bottom:none}
.recurring-item:hover{background:#1c2128}
.recurring-freq{color:#8b949e;font-family:monospace;min-width:54px;flex-shrink:0}
.recurring-text{flex:1;color:#c9d1d9}
.recurring-due{color:#e3b341;font-size:11px}.recurring-due.overdue{color:#f85149}
.accordion{border:1px solid #30363d;border-radius:6px;overflow:hidden;margin-bottom:8px}
.acc-header{padding:8px 12px;background:#1c2128;cursor:pointer;display:flex;align-items:center;gap:8px;font-size:13px;color:#c9d1d9;user-select:none}
.acc-header:hover{background:#262c36}
.acc-toggle{color:#6e7681;font-size:10px;width:10px;flex-shrink:0;display:inline-block;transition:transform .15s}
.acc-header.open .acc-toggle{transform:rotate(90deg)}
.acc-name{flex:0 0 auto}.acc-preview{flex:1;font-size:11px;color:#6e7681;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;padding:0 4px}.acc-header.open .acc-preview{display:none}
.acc-meta{font-size:11px;color:#6e7681}
.acc-type{font-size:10px;padding:1px 6px;border-radius:10px;background:#21262d;color:#8b949e}
.acc-body{padding:12px 14px;display:none;font-size:13px}
.acc-body.open{display:block}
.acc-body.md{max-height:480px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#30363d #1c2128}
.know-item{display:flex;align-items:center;gap:8px;padding:6px 10px;font-size:12px;border-bottom:1px solid #21262d;border-radius:4px;cursor:pointer;transition:background .15s}
.know-item:last-child{border-bottom:none}
.know-item:hover{background:#1c2128}
.know-name{color:#c9d1d9}
.know-divider{padding:5px 12px 3px;font-size:10px;color:#6e7681;text-transform:uppercase;letter-spacing:.05em;background:#0d1117;border-top:1px solid #21262d;margin-top:2px}
.know-divider:first-child{border-top:none;margin-top:0}
.note-tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));grid-auto-flow:dense;gap:10px}
.note-tile{padding:12px 16px;font-size:12px;background:#1c2128;border:1px solid #30363d;border-radius:6px;cursor:pointer;transition:border-color .15s,background .15s}
.note-tile:hover{border-color:#58a6ff;background:#1a2332}
.note-tile.active{border-color:#58a6ff;background:#1c2230}
.note-tile.expanded{grid-column:1/-1;order:-1;cursor:default}
.note-tile.expanded .note-tile-header{cursor:pointer}
.note-tile-header{display:flex;align-items:center;gap:8px}
.note-tile-slot{font-weight:700;color:#58a6ff;min-width:14px;font-size:13px}
.note-tile-preview{color:#c9d1d9;font-size:12px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.note-tile.expanded .note-tile-preview{display:none}
.note-tile-meta{color:#6e7681;font-size:11px;white-space:nowrap}
.note-tile-body{display:none;padding:8px 0 0;border-top:1px solid #30363d;margin-top:8px;max-height:480px;overflow-y:auto}
.note-tile.expanded .note-tile-body{display:block}
.md h1,.md h2,.md h3,.md h4{color:#f0f6fc;margin:10px 0 5px}
.md h1{font-size:16px}.md h2{font-size:14px;padding-bottom:4px;border-bottom:1px solid #30363d}
.md h3{font-size:13px;color:#c9d1d9}.md p{margin-bottom:7px;color:#c9d1d9}
.md ul{padding-left:18px;margin-bottom:7px}.md li{margin-bottom:2px;color:#c9d1d9}
.md code{background:#21262d;padding:1px 5px;border-radius:3px;font-family:monospace;font-size:12px;color:#ffa657}
.md pre.code-block{background:#21262d;padding:10px 12px;border-radius:5px;overflow-x:auto;margin-bottom:8px}
.md pre.code-block code{background:none;padding:0;color:#c9d1d9;font-size:12px}
.md table.md-table{border-collapse:collapse;width:100%;margin-bottom:8px;font-size:12px}
.md table.md-table th,.md table.md-table td{padding:5px 10px;border:1px solid #30363d;text-align:left}
.md table.md-table th{background:#1c2128;color:#f0f6fc;font-weight:600}
.md hr{border:none;border-top:1px solid #30363d;margin:10px 0}
.md a{color:#58a6ff;text-decoration:none}.md a:hover{text-decoration:underline}
.md strong{color:#f0f6fc}.md em{font-style:italic}
.ps-item{display:flex;gap:8px;align-items:center;padding:3px 0;font-size:12px}
.ps-dot{color:#3fb950;font-size:9px}
.ps-name{flex:1;color:#c9d1d9}.ps-name.current{color:#f0f6fc;font-weight:600}
.ps-meta{color:#6e7681}
.slot-badge{display:inline-flex;align-items:center;gap:4px;background:#1c2128;border:1px solid #30363d;border-radius:3px;padding:1px 6px;font-size:11px;color:#8b949e}
.slot-badge.active{color:#58a6ff;border-color:#58a6ff}
.stats-table{width:100%;border-collapse:collapse;font-size:12px}
.stats-table th{color:#8b949e;font-weight:500;text-align:left;padding:4px 8px;border-bottom:1px solid #30363d}
.stats-table td{padding:4px 8px;border-bottom:1px solid #21262d;color:#c9d1d9}
.stats-table td:last-child{text-align:right;color:#58a6ff;font-weight:600}
.mem-line{padding:3px 0;font-size:12px;border-bottom:1px solid #21262d;font-family:monospace;white-space:pre-wrap;word-break:break-word;color:#c9d1d9}
.stats-platform-table td.ok{color:#3fb950}
.stats-platform-table td.warn{color:#e3b341}

.mem-line:last-child{border-bottom:none}
.fact-item{padding:6px 10px;border-bottom:1px solid #21262d;font-size:12px;border-radius:4px;transition:background .15s}
.fact-item:last-child{border-bottom:none}
.fact-item:hover{background:#1c2128}
.fact-date{color:#6e7681;font-size:11px}
.fact-text{color:#c9d1d9}
.empty{color:#8b949e;font-size:12px;padding:16px 20px;font-style:italic;text-align:center;background:#0d1117;border:1px dashed #30363d;border-radius:6px;margin:4px 0}
.cmd-hints{display:flex;flex-wrap:wrap;gap:5px;padding:6px 12px;border-bottom:1px solid #21262d;background:#0a0e13}
.cmd-hint{font-family:monospace;font-size:11px;color:#8b949e;background:#161b22;border:1px solid #30363d;border-radius:3px;padding:2px 7px;cursor:default;user-select:all;transition:border-color .15s,background .15s}
.cmd-hint:hover{border-color:#58a6ff;color:#c9d1d9;background:#1c2128}
.standup-section{margin-bottom:8px}
.standup-label{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.04em;display:block;margin-bottom:3px}
.standup-item{font-size:12px;color:#c9d1d9;padding:2px 0}
.done-item{color:#3fb950}.pr-item{color:#79c0ff}
.panel-input{display:flex;gap:6px;padding:8px 12px 4px;border-top:1px solid #21262d}
.panel-input input{flex:1;background:#0d1117;border:1px solid #30363d;border-radius:4px;padding:5px 8px;color:#e6edf3;font-size:12px;outline:none}
.panel-input input:focus{border-color:#58a6ff}
.panel-input input.input-error{border-color:#f85149}
.panel-input button{background:#238636;border:none;border-radius:4px;color:#fff;padding:5px 10px;cursor:pointer;font-size:13px;font-weight:600}
.panel-input button:hover{background:#2ea043}
.todo-done-btn{background:#0d1117;border:1px solid #30363d;border-radius:3px;color:#7d8590;padding:2px 7px;cursor:pointer;font-size:11px;margin-left:auto;flex-shrink:0;transition:border-color .15s,color .15s,background .15s}
.todo-done-btn:hover{border-color:#238636;color:#3fb950;background:#0d2818}
#main-content{transition:opacity .12s}
#main-content.is-loading{opacity:.45;pointer-events:none}
#search-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;z-index:200;background:rgba(0,0,0,0.6);justify-content:center;align-items:flex-start;padding-top:80px}
.search-panel{background:#161b22;border:1px solid #30363d;border-radius:8px;width:600px;max-width:92vw;max-height:70vh;overflow-y:auto}
.search-header{padding:10px 14px;border-bottom:1px solid #30363d;display:flex;justify-content:space-between;align-items:center}
.search-title{color:#f0f6fc;font-weight:600;font-size:13px}
.search-close{background:none;border:none;color:#8b949e;font-size:16px;cursor:pointer;padding:0 4px;line-height:1}
.search-close:hover{color:#c9d1d9}
.search-body{padding:12px 14px}
.search-result{padding:8px 10px;border-bottom:1px solid #21262d;font-size:12px;border-radius:4px;transition:background .15s}
.search-result:last-child{border-bottom:none}
.search-result:hover{background:#1c2128}
.search-date{color:#6e7681;font-size:11px;margin-right:8px;font-family:monospace}
.search-line{color:#c9d1d9;word-break:break-word}
.sparkline-wrap{padding:4px 12px 0;border-bottom:1px solid #21262d}
.sparkline{display:flex;align-items:flex-end;gap:2px;height:56px;padding:4px 0 0}
.spark-bar{flex:1;border-radius:2px 2px 0 0;min-height:2px;cursor:default;transition:opacity .15s}
.spark-bar:hover{opacity:.65}
.spark-bar.today{background:#3fb950}
.spark-bar.weekend{background:hsl(265,30%,42%)}
.spark-labels{display:flex;gap:2px;padding:2px 0 4px}
.spark-labels span{flex:1;text-align:center;font-size:9px;color:#6e7681}
.spark-labels span.weekend{color:#8b7bb5}
.heatmap-wrap{padding:4px 12px 0}
.heatmap{display:flex;align-items:flex-end;gap:1px;height:32px}
.heat-bar{flex:1;border-radius:1px 1px 0 0;min-height:2px;cursor:default;transition:opacity .15s}
.heat-bar:hover{opacity:.65}
.heat-bar.current{box-shadow:0 0 0 1px #58a6ff}
.heat-labels{display:flex;gap:1px;padding:1px 0 4px}
.heat-labels span{flex:1;text-align:center;font-size:8px;color:#6e7681}
.summary-stats{display:flex;gap:12px 20px;padding:6px 12px;justify-content:center;flex-wrap:wrap}
.summary-stat{text-align:center;min-width:56px}
.summary-stat .stat-value{font-size:18px;font-weight:700;color:#e6edf3;display:block}
.summary-stat .stat-label{font-size:10px;color:#8b949e;white-space:nowrap}
.summary-link{display:block;text-align:center;padding:8px 12px;font-size:12px;color:#58a6ff;text-decoration:none;border-top:1px solid #21262d;background:#111820;font-weight:500;letter-spacing:.02em;transition:background .15s,color .15s}
.summary-link:hover{color:#79c0ff;background:#122131}
.log-filter{display:flex;gap:4px;padding:5px 12px;border-bottom:1px solid #21262d;flex-wrap:wrap}
.log-filter-btn{font-size:11px;padding:1px 7px;border-radius:10px;cursor:pointer;border:1px solid #30363d;background:#21262d;color:#8b949e}
.log-filter-btn.active{border-color:#58a6ff;color:#fff;background:#1a3a5c;font-weight:600}
.log-filter-btn:hover:not(.active){border-color:#6e7681;color:#c9d1d9}
.log-entry.filtered{display:none}
.slot-switcher{display:flex;gap:4px;padding:5px 12px;border-bottom:1px solid #21262d}
.slot-btn{background:#21262d;border:1px solid #30363d;border-radius:3px;color:#8b949e;padding:1px 7px;cursor:pointer;font-size:11px}
.slot-btn.active{border-color:#58a6ff;color:#58a6ff}
.slot-btn:hover:not(.active){border-color:#6e7681;color:#c9d1d9}
a.know-item{text-decoration:none;color:inherit;display:flex}
mark{background:#3d2e00;color:#e3b341;padding:0 2px;border-radius:2px}
.hive-focus{box-shadow:inset 0 0 0 1px #58a6ff,0 0 0 1px rgba(88,166,255,0.25)}
.todo-item.hive-focus,.log-entry.hive-focus{background:#1c2230;box-shadow:inset 2px 0 0 #58a6ff}
.accordion.hive-focus>.acc-header{background:#1a2332}
.note-tile.hive-focus{border-color:#58a6ff;background:#1a2332}
.setting-row.hive-focus{background:#1c2230;box-shadow:inset 2px 0 0 #58a6ff}
.session-item.hive-focus{background:#1c2230;box-shadow:inset 2px 0 0 #58a6ff}
.card.hive-focus{border-color:#58a6ff}
#g-prefix{position:fixed;bottom:16px;right:16px;background:#161b22;border:1px solid #58a6ff;border-radius:6px;padding:4px 10px;font-family:monospace;font-size:14px;color:#58a6ff;z-index:300;display:none;pointer-events:none}
#help-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;z-index:400;background:rgba(0,0,0,0.7);justify-content:center;align-items:center}
#help-overlay.open{display:flex}
.help-panel{background:#161b22;border:1px solid #30363d;border-radius:8px;width:640px;max-width:92vw;max-height:80vh;overflow-y:auto;padding:20px 24px}
.help-panel h2{color:#f0f6fc;font-size:15px;margin-bottom:12px;padding-bottom:6px;border-bottom:1px solid #30363d}
.help-panel h3{color:#c9d1d9;font-size:12px;margin:10px 0 4px;text-transform:uppercase;letter-spacing:.04em}
.help-keys{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;font-size:12px}
.help-key{color:#58a6ff;font-family:monospace;font-weight:600;text-align:right;padding:1px 0}
.help-desc{color:#8b949e;padding:1px 0}
.search-tier{display:inline-block;padding:0 5px;border-radius:3px;font-size:10px;font-weight:600;margin-right:6px}
.search-tier-working{background:#1c3552;color:#79c0ff}
.search-tier-knowledge{background:#0d2e1a;color:#56d364}
.search-tier-daily{background:#3d2e00;color:#e3b341}
.search-tier-archive{background:#21262d;color:#8b949e}
.search-context{font-size:11px;color:#6e7681;padding:2px 0 0 24px}
.search-actions{display:flex;gap:4px;margin-top:4px;padding-left:24px}
.search-action-btn{font-size:10px;padding:1px 6px;border:1px solid #30363d;border-radius:3px;background:#21262d;color:#8b949e;cursor:pointer}
.search-action-btn:hover{border-color:#58a6ff;color:#c9d1d9}
#edit-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;z-index:350;background:rgba(0,0,0,0.8)}
#edit-overlay.open{display:flex}
.edit-modal{width:100%;height:100%;display:flex;flex-direction:column;background:#0d1117}
.edit-toolbar{display:flex;align-items:center;gap:8px;padding:8px 16px;background:#161b22;border-bottom:1px solid #30363d}
.edit-toolbar-title{color:#f0f6fc;font-weight:600;font-size:13px;flex:1}
.edit-btn{padding:4px 12px;border-radius:4px;font-size:12px;cursor:pointer;border:1px solid #30363d;background:#21262d;color:#c9d1d9}
.edit-btn:hover{border-color:#58a6ff;color:#f0f6fc}
.edit-btn-save{background:#238636;border-color:#238636;color:#fff}
.edit-btn-save:hover{background:#2ea043}
.edit-panes{display:flex;flex:1;overflow:hidden}
.edit-panes textarea{flex:1;background:#0d1117;color:#e6edf3;border:none;border-right:1px solid #30363d;padding:12px 16px;font-family:'SF Mono',Monaco,Consolas,monospace;font-size:13px;line-height:1.6;resize:none;outline:none;tab-size:2}
.edit-preview{flex:1;padding:12px 16px;overflow-y:auto;font-size:13px}
.know-cmd{color:#6e7681;font-family:monospace;font-size:11px;margin-left:auto}
.tab-bar{display:flex;gap:0;border-bottom:1px solid #30363d;background:#161b22}
.tab-btn{padding:8px 16px;font-size:12px;color:#8b949e;background:none;border:none;border-bottom:2px solid transparent;cursor:pointer;transition:color .1s}
.tab-btn:hover{color:#c9d1d9}
.tab-btn.active{color:#f0f6fc;border-bottom-color:#58a6ff;font-weight:600}
.tab-content{display:none}
.tab-content.active{display:block}
.setting-row{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid #21262d}
.setting-row:last-child{border-bottom:none}
.setting-label{font-weight:500;min-width:80px}
.setting-desc{color:#8b949e;font-size:12px;flex:1}
.setting-toggle{position:relative;width:36px;height:20px;display:inline-block}
.setting-toggle input{opacity:0;width:0;height:0}
.setting-toggle .slider{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background:#30363d;border-radius:10px;transition:.2s}
.setting-toggle .slider:before{content:'';position:absolute;height:14px;width:14px;left:3px;bottom:3px;background:#c9d1d9;border-radius:50%;transition:.2s}
.setting-toggle input:checked+.slider{background:#238636}
.setting-toggle input:checked+.slider:before{transform:translateX(16px)}
.setting-select{background:#161b22;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:4px 8px;font-size:12px;cursor:pointer}
.setting-select:focus{border-color:#58a6ff;outline:none}
.sound-test-btn{background:none;border:1px solid #30363d;border-radius:6px;color:#8b949e;cursor:pointer;padding:2px 8px;font-size:14px;line-height:1;transition:.2s}
.sound-test-btn:hover{border-color:#58a6ff;color:#58a6ff}
.sound-test-btn.playing{color:#3fb950;border-color:#3fb950}
.trend-up{color:#3fb950}.trend-down{color:#f85149}.trend-flat{color:#6e7681}
.trend-row{display:flex;align-items:center;gap:12px;padding:4px 0;border-bottom:1px solid #21262d;font-size:12px}
.trend-row:last-child{border-bottom:none}
.trend-label{width:90px;flex:none;color:#8b949e;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.trend-val{min-width:40px;text-align:right;color:#c9d1d9;font-weight:600}
.trend-delta{font-size:10px;min-width:44px;text-align:right;color:#6e7681}
.cmd-bar{display:inline-block;height:12px;border-radius:2px;background:#1a3a5c;vertical-align:middle}
.prompt-hist{display:flex;align-items:flex-end;gap:3px;height:80px;padding:8px 12px 0}
.prompt-hist-bar{flex:1;border-radius:2px 2px 0 0;background:#238636;min-width:18px;position:relative;cursor:default}
.prompt-hist-bar span{position:absolute;top:-16px;left:50%;transform:translateX(-50%);font-size:10px;color:#8b949e}
.prompt-hist-labels{display:flex;gap:3px;padding:2px 12px 6px}
.prompt-hist-labels span{flex:1;text-align:center;font-size:10px;color:#6e7681;min-width:18px}
.session-list{max-height:180px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#30363d #161b22}
.session-item{display:flex;gap:8px;align-items:center;padding:4px 10px;border-bottom:1px solid #21262d;font-size:12px}
.session-item:last-child{border-bottom:none}
.session-time{color:#6e7681;font-family:monospace;min-width:44px;flex-shrink:0}
.session-prompts{color:#58a6ff;font-weight:600;min-width:20px;text-align:right}
.session-proj{color:#8b949e;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.session-tools{color:#6e7681;font-size:11px;flex-shrink:0}
.session-live{display:inline-block;font-size:10px;font-weight:700;color:#3fb950;background:#0d2e1a;padding:1px 5px;border-radius:3px;margin-right:4px;letter-spacing:.5px;animation:pulse-live 2s ease-in-out infinite}
@keyframes pulse-live{0%,100%{opacity:1}50%{opacity:.6}}
.session-live-item{border-left:2px solid #3fb950}
.session-duration{color:#3fb950;font-size:11px;font-family:monospace;flex-shrink:0}
.session-item.session-filtered{display:none}
.session-mode{display:inline-block;font-size:9px;font-weight:700;padding:1px 4px;border-radius:2px;margin-right:3px;letter-spacing:.4px;flex-shrink:0}
.session-mode-plan{color:#f0a500;background:#2d1f00}
.session-mode-auto{color:#20b2aa;background:#0d2e2e}
.session-mode-bypass{color:#f85149;background:#2d0f0f}
.source-row{display:flex;align-items:center;gap:8px;padding:3px 0}
.source-label{min-width:100px;text-align:right;color:#8b949e;font-size:12px}
.source-bar-track{flex:1;background:#161b22;border-radius:2px;height:14px;overflow:hidden}
.source-bar-fill{height:100%;border-radius:2px;background:#1a3a5c}
.source-pct{min-width:35px;text-align:right;font-size:12px;color:#8b949e}

.card.brain-card{border-color:#262b33;min-height:140px}
.card.brain-card.brain-card-emphasis{border-color:#3fb950}
.card.brain-card .card-header{display:flex;align-items:center;justify-content:space-between;gap:8px}
.card.brain-card .card-body{display:flex;flex-direction:column;gap:6px;font-size:12px;color:#c9d1d9}
.brain-line{display:flex;align-items:center;gap:8px;justify-content:space-between}
.brain-line .brain-text{flex:1;color:#c9d1d9}
.brain-line .brain-detail{color:#6e7681;font-size:11px;white-space:nowrap}
.brain-chip{display:inline-flex;align-items:center;justify-content:center;padding:2px 7px;border-radius:999px;font-size:10px;text-transform:uppercase;background:#21262d;color:#8b949e;border:1px solid #30363d;letter-spacing:.04em}
.brain-chip-ok{background:#1f6feb;color:#fff;border-color:#388bfd}
.brain-chip-warn{background:#b62324;color:#fff;border-color:#f85149}
.brain-chip-dim{background:#1c2128;color:#6e7681;border-color:#2d333b}
.brain-meta{font-size:11px;color:#8b949e}
.brain-meta-warn{color:#fdaeb7}
.brain-empty{font-size:12px;color:#8b949e;margin:6px 0}
.brain-hint{font-size:11px;color:#58a6ff;margin-top:4px}
.platform-row{display:flex;align-items:center;gap:8px;font-size:12px;margin-bottom:4px;color:#c9d1d9}
.platform-title{font-weight:600;flex:1}
.platform-meta{color:#8b949e;font-size:11px}
"""
_JS = """
(function(){
  var view=document.body.dataset.view||'home';
  var iv=null;
  var tsIv=null;
  var lastSuccess=Date.now();
  var lastInterval=10;
  var logDate=null;
  var _focusIdx=-1;
  var _innerMode=false;
  var _innerIdx=-1;
  var _gPending=false;
  var _gTimer=null;
  var _searchIdx=-1;
  var _searchDebounce=null;

  // --- State persistence ---
  function saveState(key, val) {
    try { localStorage.setItem(key, JSON.stringify(val)); } catch(e) {}
  }
  function loadState(key, fallback) {
    try { var v = localStorage.getItem(key); return v !== null ? JSON.parse(v) : fallback; } catch(e) { return fallback; }
  }
  function saveAccState() {
    var names = [];
    document.querySelectorAll('.acc-header.open').forEach(function(hdr) {
      var nm = hdr.querySelector('.acc-name');
      if (nm) names.push(nm.textContent);
    });
    saveState('hive-acc-' + view, names);
  }
  function restoreState() {
    // Log type filter
    var sf = loadState('hive-log-filter', '');
    var logFilter = document.querySelector('.log-filter:not([data-filter-group])');
    if (logFilter && sf) {
      logFilter.querySelectorAll('.log-filter-btn').forEach(function(b) {
        b.classList.toggle('active', b.dataset.type === sf);
        b.setAttribute('aria-pressed', String(b.dataset.type === sf));
      });
      var card = logFilter.closest('.card');
      if (card) {
        card.querySelectorAll('.log-entry').forEach(function(e) {
          e.classList.toggle('filtered', e.dataset.type !== sf);
        });
      }
    }
    // Session filter
    var ssf = loadState('hive-session-filter', '');
    var sessFilter = document.querySelector('.log-filter[data-filter-group="session"]');
    if (sessFilter && ssf) {
      sessFilter.querySelectorAll('.log-filter-btn').forEach(function(b) {
        b.classList.toggle('active', b.dataset.type === ssf);
        b.setAttribute('aria-pressed', String(b.dataset.type === ssf));
      });
      var scard = sessFilter.closest('.card');
      if (scard) {
        scard.querySelectorAll('.session-item').forEach(function(item) {
          if(!ssf){item.classList.remove('session-filtered');}
          else if(ssf==='live'){item.classList.toggle('session-filtered',item.dataset.live!=='true');}
          else if(ssf==='deep'){item.classList.toggle('session-filtered',item.dataset.depth!=='deep');}
          else{item.classList.toggle('session-filtered',item.dataset.depth!==ssf);}
        });
      }
    }
    // Accordions (per-view)
    var accNames = loadState('hive-acc-' + view, null);
    if (accNames !== null && Array.isArray(accNames)) {
      document.querySelectorAll('.acc-header').forEach(function(hdr) {
        var nm = hdr.querySelector('.acc-name');
        if (!nm) return;
        var open = accNames.indexOf(nm.textContent) !== -1;
        var body = hdr.nextElementSibling;
        if (body && body.classList.contains('acc-body')) {
          hdr.classList.toggle('open', open);
          body.classList.toggle('open', open);
        }
      });
    } else if (view === 'know') {
      var fb = document.querySelector('.tab-content.active .acc-body');
      var fh = document.querySelector('.tab-content.active .acc-header');
      if (fb) fb.classList.add('open');
      if (fh) fh.classList.add('open');
    }
    // Note tile
    var slot = loadState('hive-note-tile', null);
    if (slot !== null) {
      document.querySelectorAll('.note-tile').forEach(function(t) {
        var s = t.querySelector('.note-tile-slot');
        if (s && s.textContent.trim() === String(slot))
          t.classList.add('expanded');
      });
    }
    // Tab selection (know view)
    var tab = loadState('hive-tab-know', null);
    if (tab && view === 'know') {
      var card = document.querySelector('.card');
      if (card) {
        card.querySelectorAll('.tab-btn').forEach(function(b) {
          b.classList.toggle('active', b.dataset.tab === tab);
        });
        card.querySelectorAll('.tab-content').forEach(function(tc) {
          tc.classList.toggle('active', tc.dataset.tab === tab);
        });
      }
    }
  }

  // --- Helpers ---
  function escHtml(s){
    return String(s)
      .replace(/&/g,"&amp;")
      .replace(/</g,"&lt;")
      .replace(/>/g,"&gt;")
      .replace(/"/g,"&quot;");
  }
  // All cards (flat list in DOM order)
  function _cards(){
    return Array.from(document.querySelectorAll('#main-content .card[tabindex]'));
  }
  // Build row structure: array of arrays of card indices.
  // Each row = cards sharing the same grid-row container.
  function _rows(){
    var mc=document.getElementById('main-content');
    if(!mc)return [];
    var cards=_cards();
    if(!cards.length)return [];
    var rows=[];var seen=new Set();
    var children=mc.querySelectorAll('.grid-row');
    for(var i=0;i<children.length;i++){
      var child=children[i];
      var rowCards=[];
      var inner=child.querySelectorAll('.card[tabindex]');
      for(var j=0;j<inner.length;j++){
        var ci=cards.indexOf(inner[j]);
        if(ci>=0&&!seen.has(ci)){rowCards.push(ci);seen.add(ci);}
      }
      if(rowCards.length)rows.push(rowCards);
    }
    return rows;
  }
  // Find which row a card index belongs to: {rowIdx, colIdx}
  function _findCell(cardIdx){
    var rs=_rows();
    for(var r=0;r<rs.length;r++){
      var c=rs[r].indexOf(cardIdx);
      if(c>=0)return {rowIdx:r,colIdx:c};
    }
    return null;
  }
  // Inner items within a specific card
  function _innerItems(card){
    return Array.from(card.querySelectorAll('.todo-item[tabindex], .log-entry[tabindex], .accordion[tabindex], .note-tile[tabindex], .setting-row[tabindex], .session-item[tabindex]'));
  }
  // All focusables (flat, for search-result nav)
  function _focusables(){
    return Array.from(document.querySelectorAll('#main-content .card[tabindex], #main-content .todo-item[tabindex], #main-content .log-entry[tabindex], #main-content .accordion[tabindex], #main-content .note-tile[tabindex], #main-content .setting-row[tabindex], #main-content .session-item[tabindex]'));
  }
  function _clearAllFocus(){
    _focusables().forEach(function(el){el.classList.remove('hive-focus');});
  }
  function _setFocus(idx){
    var items=_cards();
    _clearAllFocus();
    _innerMode=false;_innerIdx=-1;
    if(idx<0)idx=0;
    if(idx>=items.length)idx=items.length-1;
    _focusIdx=idx;
    if(items[idx]){
      items[idx].classList.add('hive-focus');
      items[idx].scrollIntoView({block:'nearest',behavior:'smooth'});
    }
  }
  function _setInnerFocus(card,idx){
    var items=_innerItems(card);
    _clearAllFocus();
    if(idx<0)idx=0;
    if(idx>=items.length)idx=items.length-1;
    _innerIdx=idx;
    card.classList.add('hive-focus');
    if(items[idx]){
      items[idx].classList.add('hive-focus');
      items[idx].scrollIntoView({block:'nearest',behavior:'smooth'});
    }
  }

  // --- Refresh timestamp ---
  function updateTs(){
    var el=document.getElementById('refresh-ts');
    if(!el)return;
    var age=Math.floor((Date.now()-lastSuccess)/1000);
    if(lastInterval>0&&age>lastInterval*2){el.style.color='#e3b341';}
    else{el.style.color='#6e7681';}
    el.textContent=age<60?'updated '+age+'s ago':'updated '+Math.floor(age/60)+'m ago';
  }

  // --- Main refresh ---
  function refresh(){
    var mc=document.getElementById('main-content');
    var url='/api/fragment?view='+view;
    if(logDate)url+='&log_date='+logDate;
    var savedIdx=_focusIdx;
    var savedInner=_innerMode;
    var savedInnerIdx=_innerIdx;
    // Skip refresh if user is typing in an input (avoid destroying their text)
    var ae=document.activeElement;
    if(ae&&mc&&mc.contains(ae)&&(ae.tagName==='INPUT'||ae.tagName==='TEXTAREA')){
      mc.classList.remove('is-loading');
      return;
    }
    if(mc)mc.classList.add('is-loading');
    // Save ephemeral UI state before refresh
    var detailsState={};
    var scrollState={};
    var inputState={};
    if(mc){
      mc.querySelectorAll('details').forEach(function(d){
        var key=d.closest('[data-panel-id]');
        var pid=key?key.getAttribute('data-panel-id'):'';
        var sum=d.querySelector('summary');
        var label=sum?sum.textContent.trim():'';
        if(pid&&label)detailsState[pid+'::'+label]=d.open;
      });
      mc.querySelectorAll('.session-list,.log-list,.todo-list').forEach(function(el){
        if(el.scrollTop>0){
          var key=el.closest('[data-panel-id]');
          var pid=key?key.getAttribute('data-panel-id'):'';
          var cls=el.className.split(' ')[0];
          if(pid)scrollState[pid+'::'+cls]=el.scrollTop;
        }
      });
      // Save input/textarea values
      mc.querySelectorAll('input[type="text"],textarea').forEach(function(inp){
        if(!inp.value)return;
        var panel=inp.closest('[data-panel-id]');
        var pid=panel?panel.getAttribute('data-panel-id'):'';
        var id=inp.id||inp.name||inp.placeholder;
        if(pid&&id)inputState[pid+'::'+id]=inp.value;
      });
    }
    fetch(url)
      .then(function(r){return r.text();})
      .then(function(h){
        if(mc){
          var tmp=document.createElement('div');
          tmp.innerHTML=h;
          var newPanels=tmp.querySelectorAll('[data-panel-id]');
          if(newPanels.length){
            newPanels.forEach(function(np){
              var id=np.getAttribute('data-panel-id');
              var ep=mc.querySelector('[data-panel-id="'+id+'"]');
              if(ep){
                // Update inner content, preserving gridstack wrapper
                var innerTarget=ep.querySelector('.grid-panel')||ep;
                var innerSource=np.querySelector('.grid-panel')||np;
                innerTarget.innerHTML=innerSource.innerHTML;
              }
            });
          } else {
            mc.innerHTML=h;
          }
          // Restore details open state
          Object.keys(detailsState).forEach(function(k){
            var parts=k.split('::');
            var pid=parts[0],label=parts[1];
            var panel=mc.querySelector('[data-panel-id="'+pid+'"]');
            if(!panel)return;
            panel.querySelectorAll('details').forEach(function(d){
              var sum=d.querySelector('summary');
              if(sum&&sum.textContent.trim()===label)d.open=detailsState[k];
            });
          });
          // Restore scroll positions
          Object.keys(scrollState).forEach(function(k){
            var parts=k.split('::');
            var pid=parts[0],cls=parts[1];
            var panel=mc.querySelector('[data-panel-id="'+pid+'"]');
            if(!panel)return;
            var el=panel.querySelector('.'+cls);
            if(el)el.scrollTop=scrollState[k];
          });
          // Restore input values
          Object.keys(inputState).forEach(function(k){
            var parts=k.split('::');
            var pid=parts[0],id=parts[1];
            var panel=mc.querySelector('[data-panel-id="'+pid+'"]');
            if(!panel)return;
            var el=panel.querySelector('#'+CSS.escape(id))
                 ||panel.querySelector('[name="'+id+'"]')
                 ||panel.querySelector('[placeholder="'+id+'"]');
            if(el)el.value=inputState[k];
          });
          mc.classList.remove('is-loading');
        }
        lastSuccess=Date.now();
        updateTs();
        restoreState();
        if(savedIdx>=0){
          if(savedInner){
            // Restore card focus, then re-enter inner mode
            var cards=_cards();
            _clearAllFocus();
            if(savedIdx>=cards.length)savedIdx=cards.length-1;
            _focusIdx=savedIdx;
            _innerMode=true;
            if(cards[savedIdx]){
              _setInnerFocus(cards[savedIdx],savedInnerIdx);
            }
          }else{
            _setFocus(savedIdx);
          }
        }
      }).catch(function(){
        if(mc)mc.classList.remove('is-loading');
        var el=document.getElementById('refresh-ts');
        if(el){el.style.color='#f85149';el.textContent='\u25cf offline';}
      });
  }
  function setIv(s){
    lastInterval=s;
    if(iv)clearInterval(iv);
    if(s>0)iv=setInterval(refresh,s*1000);
  }
  var sel=document.getElementById('refresh-select');
  var saved=parseInt(localStorage.getItem('hive-refresh')||'10',10);
  if(sel){
    sel.value=String(saved);
    sel.addEventListener('change',function(){
      var s=parseInt(this.value,10);
      localStorage.setItem('hive-refresh',String(s));
      setIv(s);
    });
  }
  setIv(saved);
  tsIv=setInterval(updateTs,1000);

  // --- Log date navigation ---
  window.loadLog=function(dateStr){
    logDate=dateStr;
    saveState('hive-log-date', dateStr);
    fetch('/api/fragment?view=log&date='+dateStr)
      .then(function(r){return r.text();})
      .then(function(h){
        var mc=document.getElementById('main-content');
        if(!mc)return;
        var logPanel=mc.querySelector('[data-panel="log"]');
        if(logPanel){
          var tmp=document.createElement('div');
          tmp.innerHTML=h;
          var np=tmp.firstElementChild;
          if(np)logPanel.replaceWith(np);
        }
      });
  };
  window.loadLogMore=function(limit){
    var url='/api/fragment?view=log&limit='+limit;
    if(logDate)url+='&date='+logDate;
    fetch(url)
      .then(function(r){return r.text();})
      .then(function(h){
        var mc=document.getElementById('main-content');
        if(!mc)return;
        var logPanel=mc.querySelector('[data-panel="log"]');
        if(logPanel){
          var tmp=document.createElement('div');
          tmp.innerHTML=h;
          var np=tmp.firstElementChild;
          if(np)logPanel.replaceWith(np);
        }
      });
  };

  // --- Accordion toggle ---
  document.addEventListener('click',function(e){
    var h=e.target.closest('.acc-header');
    if(!h)return;
    var acc=h.closest('.accordion');
    var b=h.nextElementSibling;
    if(b&&b.classList.contains('acc-body')){
      var isOpen=b.classList.contains('open');
      b.classList.toggle('open',!isOpen);
      h.classList.toggle('open',!isOpen);
      if(acc)acc.setAttribute('aria-expanded',String(!isOpen));
      saveAccState();
    }
  });

  // --- Note tile expand (single-open accordion) ---
  document.addEventListener('click',function(e){
    if(e.target.closest('.note-tile-body')) return;
    var tile=e.target.closest('.note-tile');
    if(!tile)return;
    var wasExpanded=tile.classList.contains('expanded');
    document.querySelectorAll('.note-tile.expanded').forEach(function(t){
      t.classList.remove('expanded');
      t.setAttribute('aria-expanded','false');
    });
    if(!wasExpanded){tile.classList.add('expanded');tile.setAttribute('aria-expanded','true');}
    var es=document.querySelector('.note-tile.expanded .note-tile-slot');
    saveState('hive-note-tile', es ? es.textContent.trim() : null);
  });

  // --- Restore persisted state ---
  logDate = loadState('hive-log-date', null);
  if (logDate) { refresh(); }
  restoreState();

  // --- Search overlay ---
  function doSearch(rawQ){
    if(!rawQ)return;
    var q=encodeURIComponent(rawQ);
    fetch('/api/search?q='+q)
      .then(function(r){return r.json();})
      .then(function(data){
        var results=data.results||[];
        var html='';
        _searchIdx=-1;
        if(!results.length){
          html='<div class="empty">No results for \\u201c'+escHtml(rawQ)+'\\u201d</div>';
        } else {
          var safeQ=escHtml(rawQ).replace(/[.*+?^${}()|\\[\\]\\\\]/g,'\\\\$&');
          var hlRe=new RegExp('('+safeQ+')','gi');
          results.forEach(function(r,i){
            var safeLine=escHtml(r.line||'');
            var hlLine=safeLine.replace(hlRe,'<mark>$1</mark>');
            var tier=r.tier||'daily';
            var tierBadge='<span class="search-tier search-tier-'+tier+'">'+tier+'</span>';
            var ctx='';
            if(r.prev_line||r.next_line){
              ctx='<div class="search-context">';
              if(r.prev_line)ctx+=escHtml(r.prev_line)+'<br>';
              if(r.next_line)ctx+=escHtml(r.next_line);
              ctx+='</div>';
            }
            var actions='<div class="search-actions">';
            if(tier==='daily')actions+='<button class="search-action-btn" onclick="promoteToMemory(this)" data-line="'+escHtml(r.line||'')+'">Promote</button>';
            actions+='<button class="search-action-btn" onclick="copyLine(this)" data-line="'+escHtml(r.line||'')+'">Copy</button>';
            if(tier==='knowledge'&&r.file)actions+='<a class="search-action-btn" href="/know">View Guide</a>';
            actions+='</div>';
            html+='<div class="search-result" data-idx="'+i+'" tabindex="0">'
              +tierBadge
              +'<span class="search-date">'+escHtml(r.date||'')+'</span>'
              +'<span class="search-line">'+hlLine+'</span>'
              +ctx+actions
              +'</div>';
          });
        }
        var sb=document.getElementById('search-body');
        if(sb)sb.innerHTML=html;
        var ov=document.getElementById('search-overlay');
        if(ov)ov.style.display='flex';
      });
  }
  window.promoteToMemory=function(btn){
    var line=btn.getAttribute('data-line');
    if(!line)return;
    btn.disabled=true;btn.textContent='...';
    fetch('/api/mem/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:line})})
      .then(function(r){return r.json();})
      .then(function(d){btn.textContent=d.ok?'Done':'Err';})
      .catch(function(){btn.textContent='Err';});
  };
  window.copyLine=function(btn){
    var line=btn.getAttribute('data-line');
    if(line&&navigator.clipboard){navigator.clipboard.writeText(line);btn.textContent='Copied';}
  };
  function closeSearch(){
    var ov=document.getElementById('search-overlay');
    if(ov)ov.style.display='none';
    _searchIdx=-1;
    // Restore card focus after closing search
    if(_focusIdx>=0){
      var cards=_cards();
      if(cards[_focusIdx])cards[_focusIdx].classList.add('hive-focus');
    }
  }
  var si=document.getElementById('search-input');
  if(si){
    si.addEventListener('input',function(){
      var val=this.value.trim();
      if(_searchDebounce)clearTimeout(_searchDebounce);
      if(val.length>=2){
        _searchDebounce=setTimeout(function(){doSearch(val);},300);
      }
    });
    si.addEventListener('keydown',function(e){
      if(e.key==='Enter'&&this.value.trim()){
        if(_searchDebounce)clearTimeout(_searchDebounce);
        doSearch(this.value.trim());
      } else if(e.key==='Escape'){
        closeSearch();
        this.blur();
      }
    });
  }
  var sc=document.getElementById('search-close');
  if(sc)sc.addEventListener('click',closeSearch);

  // --- CRUD panel inputs ---
  document.addEventListener('submit',function(e){
    var f=e.target.closest('.panel-input');
    if(!f)return;
    e.preventDefault();
    var inp=f.querySelector('input');
    var btn=f.querySelector('button');
    var val=inp.value.trim();
    if(!val)return;
    btn.disabled=true;btn.textContent='\\u2026';
    var action=f.dataset.action;
    var field=f.dataset.field;
    var body={};body[field]=val;
    fetch(action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
      .then(function(r){return r.json();})
      .then(function(d){
        btn.disabled=false;btn.textContent='+';
        if(d.ok){inp.value='';refresh();}
        else{inp.classList.add('input-error');setTimeout(function(){inp.classList.remove('input-error');},2000);}
      })
      .catch(function(){btn.disabled=false;btn.textContent='+';});
  });
  document.addEventListener('click',function(e){
    var btn=e.target.closest('.todo-done-btn');
    if(!btn)return;
    var pattern=btn.dataset.pattern;
    var orig=btn.textContent;
    btn.disabled=true;btn.textContent='\\u2026';
    fetch('/api/todo/done',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pattern:pattern})})
      .then(function(r){return r.json();})
      .then(function(d){
        if(d.ok){refresh();}
        else{btn.disabled=false;btn.textContent=orig;}
      })
      .catch(function(){btn.disabled=false;btn.textContent=orig;});
  });

  // --- Log type filter ---
  document.addEventListener('click',function(e){
    var btn=e.target.closest('.log-filter-btn');
    if(!btn)return;
    var type=btn.dataset.type;
    var container=btn.closest('.card');
    if(!container)return;
    var filterGroup=btn.closest('.log-filter');
    var isSession=filterGroup&&filterGroup.dataset.filterGroup==='session';
    container.querySelectorAll('.log-filter-btn').forEach(function(b){
      // Only toggle buttons in the same filter group
      if(b.closest('.log-filter')===filterGroup){
        b.classList.toggle('active',b===btn);
        b.setAttribute('aria-pressed',String(b===btn));
      }
    });
    if(isSession){
      // Session filter: match on data-depth or data-live attributes
      container.querySelectorAll('.session-item').forEach(function(item){
        if(!type){item.classList.remove('session-filtered');}
        else if(type==='live'){item.classList.toggle('session-filtered',item.dataset.live!=='true');}
        else if(type==='deep'){item.classList.toggle('session-filtered',item.dataset.depth!=='deep');}
        else{item.classList.toggle('session-filtered',item.dataset.depth!==type);}
      });
      saveState('hive-session-filter',type||'');
    }else{
      container.querySelectorAll('.log-entry').forEach(function(entry){
        if(!type){entry.classList.remove('filtered');}
        else{entry.classList.toggle('filtered',entry.dataset.type!==type);}
      });
      saveState('hive-log-filter', type || '');
    }
  });

  // --- Note slot switcher ---
  window.switchNote=function(n){
    document.querySelectorAll('.slot-btn').forEach(function(b){b.disabled=true;});
    fetch('/api/note/switch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slot:n})})
      .then(function(r){return r.json();})
      .then(function(d){if(d.ok)refresh();})
      .catch(function(){document.querySelectorAll('.slot-btn').forEach(function(b){b.disabled=false;});});
  };

  // --- Profile management ---
  window.profileSwitch=function(name){
    fetch('/api/profiles/use',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name})})
      .then(function(r){return r.json();})
      .then(function(d){if(d.ok)location.reload();});
  };
  window.profileCreate=function(){
    var inp=document.getElementById('profile-name');
    var seed=document.getElementById('profile-seed');
    var name=(inp?inp.value:'').trim();
    if(!name)return;
    fetch('/api/profiles/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name,seed:seed?seed.checked:false})})
      .then(function(r){return r.json();})
      .then(function(d){if(d.ok){if(inp)inp.value='';refresh();}});
  };
  window.profileDelete=function(name){
    if(!confirm('Delete profile "'+name+'" and ALL its data?'))return;
    fetch('/api/profiles/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name})})
      .then(function(r){return r.json();})
      .then(function(d){if(d.ok)refresh();});
  };

  // --- Data transfer ---
  window.transferExport=function(){
    var a=document.createElement('a');
    a.href='/api/transfer/export';
    a.download='';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };
  window.transferImport=function(){
    var inp=document.getElementById('import-file');
    if(!inp||!inp.files||!inp.files.length)return;
    var file=inp.files[0];
    var reader=new FileReader();
    reader.onload=function(){
      var b64=btoa(String.fromCharCode.apply(null,new Uint8Array(reader.result)));
      fetch('/api/transfer/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data:b64})})
        .then(function(r){return r.json();})
        .then(function(d){if(d.ok){inp.value='';refresh();}});
    };
    reader.readAsArrayBuffer(file);
  };

  // --- Edit modal ---
  var _editType='';var _editName='';var _editSlot=0;
  var _previewDebounce=null;
  window.openEdit=function(type,name,slot){
    _editType=type;_editName=name||'';_editSlot=slot||0;
    var ov=document.getElementById('edit-overlay');
    if(!ov)return;
    var title=document.getElementById('edit-title');
    if(title)title.textContent='Edit: '+(name||type);
    var params='type='+encodeURIComponent(type);
    if(name)params+='&name='+encodeURIComponent(name);
    if(slot)params+='&slot='+slot;
    fetch('/api/content?'+params)
      .then(function(r){return r.json();})
      .then(function(d){
        var ta=document.getElementById('edit-textarea');
        var pv=document.getElementById('edit-preview-body');
        if(ta)ta.value=d.content||'';
        if(pv)pv.innerHTML=d.html||'';
        ov.classList.add('open');
        if(ta)ta.focus();
      });
  };
  window.closeEdit=function(){
    var ov=document.getElementById('edit-overlay');
    if(ov)ov.classList.remove('open');
    _editType='';_editName='';_editSlot=0;
  };
  window.saveEdit=function(){
    var ta=document.getElementById('edit-textarea');
    if(!ta)return;
    var body={type:_editType,content:ta.value};
    if(_editName)body.name=_editName;
    if(_editSlot)body.slot=_editSlot;
    fetch('/api/edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
      .then(function(r){return r.json();})
      .then(function(d){if(d.ok){closeEdit();refresh();}});
  };
  document.addEventListener('input',function(e){
    if(e.target.id!=='edit-textarea')return;
    if(_previewDebounce)clearTimeout(_previewDebounce);
    _previewDebounce=setTimeout(function(){
      fetch('/api/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:e.target.value})})
        .then(function(r){return r.json();})
        .then(function(d){
          var pv=document.getElementById('edit-preview-body');
          if(pv)pv.innerHTML=d.html||'';
        });
    },500);
  });

  // --- Tab switching (knowledge view) ---
  document.addEventListener('click',function(e){
    var btn=e.target.closest('.tab-btn');
    if(!btn)return;
    var target=btn.dataset.tab;
    var container=btn.closest('.card');
    if(!container)return;
    container.querySelectorAll('.tab-btn').forEach(function(b){b.classList.toggle('active',b===btn);});
    container.querySelectorAll('.tab-content').forEach(function(tc){
      tc.classList.toggle('active',tc.dataset.tab===target);
    });
    saveState('hive-tab-know', target);
  });

  // --- Click-to-focus: sync keyboard nav state on click ---
  document.addEventListener('click', function(e) {
    var card = e.target.closest('#main-content .card[tabindex]');
    if (!card) return;
    var cards = _cards();
    var cardIdx = cards.indexOf(card);
    if (cardIdx < 0) return;
    var innerItem = e.target.closest(
      '.todo-item[tabindex], .log-entry[tabindex], .accordion[tabindex], ' +
      '.note-tile[tabindex], .setting-row[tabindex], .session-item[tabindex]'
    );
    if (innerItem) {
      var items = _innerItems(card);
      var itemIdx = items.indexOf(innerItem);
      if (itemIdx >= 0) {
        _focusIdx = cardIdx;
        _innerMode = true;
        _setInnerFocus(card, itemIdx);
        return;
      }
    }
    _setFocus(cardIdx);
  });

  // --- Keyboard navigation ---
  var _VIEW_KEYS={h:'/',d:'/dev',b:'/brain',k:'/know',s:'/stats',c:'/settings'};

  function _clearG(){
    _gPending=false;
    if(_gTimer){clearTimeout(_gTimer);_gTimer=null;}
    var gi=document.getElementById('g-prefix');
    if(gi)gi.style.display='none';
  }

  function _isInput(el){
    if(!el)return false;
    var tag=el.tagName;
    return tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT'||el.isContentEditable;
  }

  document.addEventListener('keydown',function(e){
    // Skip when focus is in an input/textarea/select
    if(_isInput(document.activeElement))return;
    // Skip when edit overlay is open
    var editOv=document.getElementById('edit-overlay');
    if(editOv&&editOv.classList.contains('open')){
      if(e.key==='Escape'){closeEdit();e.preventDefault();}
      if(e.ctrlKey&&e.key==='Enter'){saveEdit();e.preventDefault();}
      return;
    }
    // Help overlay
    var helpOv=document.getElementById('help-overlay');
    if(helpOv&&helpOv.classList.contains('open')){
      if(e.key==='Escape'||e.key==='?'){helpOv.classList.remove('open');e.preventDefault();}
      return;
    }
    // Search overlay
    var searchOv=document.getElementById('search-overlay');
    if(searchOv&&searchOv.style.display==='flex'){
      if(e.key==='Escape'){closeSearch();e.preventDefault();return;}
      if(e.key==='n'||e.key==='N'){
        var results=searchOv.querySelectorAll('.search-result');
        if(results.length){
          _searchIdx=e.key==='n'?(_searchIdx+1)%results.length:(_searchIdx-1+results.length)%results.length;
          results.forEach(function(r,i){r.classList.toggle('hive-focus',i===_searchIdx);});
          results[_searchIdx].scrollIntoView({block:'nearest'});
        }
        e.preventDefault();return;
      }
      return;
    }

    var k=e.key;

    // g-prefix handler
    if(_gPending){
      _clearG();
      if(_VIEW_KEYS[k]){window.location.href=_VIEW_KEYS[k];e.preventDefault();}
      else if(k==='g'){_setFocus(0);window.scrollTo(0,0);e.preventDefault();}
      return;
    }
    if(k==='g'&&!e.ctrlKey&&!e.metaKey&&!e.altKey){
      _gPending=true;
      var gi=document.getElementById('g-prefix');
      if(gi)gi.style.display='block';
      _gTimer=setTimeout(_clearG,800);
      e.preventDefault();return;
    }

    // j/k: move between rows (card level) or items (inner mode)
    if(k==='j'){
      if(_innerMode){
        var cardJ=_cards()[_focusIdx];
        if(cardJ){var items=_innerItems(cardJ);if(_innerIdx<items.length-1)_setInnerFocus(cardJ,_innerIdx+1);}
      }else{
        var cell=_findCell(_focusIdx);var rs=_rows();
        if(cell&&cell.rowIdx<rs.length-1){
          var nextRow=rs[cell.rowIdx+1];
          var col=Math.min(cell.colIdx,nextRow.length-1);
          _setFocus(nextRow[col]);
        }else if(_focusIdx<0){_setFocus(0);}
      }
      e.preventDefault();
    }
    else if(k==='k'){
      if(_innerMode){
        var cardK=_cards()[_focusIdx];
        if(cardK){if(_innerIdx>0)_setInnerFocus(cardK,_innerIdx-1);}
      }else{
        var cell2=_findCell(_focusIdx);var rs2=_rows();
        if(cell2&&cell2.rowIdx>0){
          var prevRow=rs2[cell2.rowIdx-1];
          var col2=Math.min(cell2.colIdx,prevRow.length-1);
          _setFocus(prevRow[col2]);
        }
      }
      e.preventDefault();
    }
    // h/l: move left/right within row, or prev/next row if solo card
    // In inner mode, exit inner and move to adjacent card
    else if(k==='h'){
      if(_innerMode){_innerMode=false;_innerIdx=-1;}
      var cellH=_findCell(_focusIdx);var rsH=_rows();
      if(cellH){
        var rowH=rsH[cellH.rowIdx];
        if(cellH.colIdx>0){_setFocus(rowH[cellH.colIdx-1]);}
        else if(cellH.rowIdx>0){var pr=rsH[cellH.rowIdx-1];_setFocus(pr[pr.length-1]);}
      }
      e.preventDefault();
    }
    else if(k==='l'){
      if(_innerMode){_innerMode=false;_innerIdx=-1;}
      var cellL=_findCell(_focusIdx);var rsL=_rows();
      if(cellL){
        var rowL=rsL[cellL.rowIdx];
        if(cellL.colIdx<rowL.length-1){_setFocus(rowL[cellL.colIdx+1]);}
        else if(cellL.rowIdx<rsL.length-1){_setFocus(rsL[cellL.rowIdx+1][0]);}
      }
      e.preventDefault();
    }
    else if(k==='J'){window.scrollBy(0,window.innerHeight/2);e.preventDefault();}
    else if(k==='K'){window.scrollBy(0,-window.innerHeight/2);e.preventDefault();}
    else if(k==='G'){var rsG=_rows();if(rsG.length){_setFocus(rsG[rsG.length-1][0]);}window.scrollTo(0,document.body.scrollHeight);e.preventDefault();}
    // Enter/o: dive into card's inner items, or toggle inner item
    else if(k==='Enter'||k==='o'){
      if(!_innerMode){
        var cardE=_cards()[_focusIdx];
        if(cardE){
          var inner2=_innerItems(cardE);
          if(inner2.length>0){_innerMode=true;_setInnerFocus(cardE,0);}
          else{
            var acc=cardE.querySelector('.acc-header');
            if(acc)acc.click();
          }
        }
      }else{
        // Toggle the focused inner item (accordion/note-tile)
        var cardI=_cards()[_focusIdx];
        if(cardI){
          var innerI=_innerItems(cardI);var curI=innerI[_innerIdx];
          if(curI){
            var accI=curI.querySelector('.acc-header');
            if(accI)accI.click();
            else if(curI.classList.contains('note-tile'))curI.click();
            else if(curI.classList.contains('todo-item')){var db=curI.querySelector('.todo-done-btn');if(db)db.click();}
            else if(curI.classList.contains('setting-row')){var scb=curI.querySelector('.setting-cb');if(scb){scb.checked=!scb.checked;scb.dispatchEvent(new Event('change',{bubbles:true}));}else{var ssel=curI.querySelector('.setting-select');if(ssel)ssel.focus();}}
          }
        }
      }
      e.preventDefault();
    }
    // Collapse focused accordion
    else if(k==='x'){
      var curX=_innerMode?(_innerItems(_cards()[_focusIdx])||[])[_innerIdx]:_cards()[_focusIdx];
      if(curX){
        var bx=curX.querySelector('.acc-body.open');
        if(bx){bx.classList.remove('open');var hdr=curX.querySelector('.acc-header');if(hdr)hdr.classList.remove('open');}
        if(curX.classList.contains('note-tile')&&curX.classList.contains('expanded'))curX.classList.remove('expanded');
      }
      e.preventDefault();
    }
    // Mark focused TODO done
    else if(k==='d'){
      var curD=_innerMode?(_innerItems(_cards()[_focusIdx])||[])[_innerIdx]:null;
      if(curD&&curD.classList.contains('todo-item')){
        var doneBtn=curD.querySelector('.todo-done-btn');
        if(doneBtn)doneBtn.click();
      }
      e.preventDefault();
    }
    // Focus search
    else if(k==='/'){
      var sinp=document.getElementById('search-input');
      if(sinp){sinp.focus();sinp.select();}
      e.preventDefault();
    }
    // Focus first input
    else if(k==='i'){
      var fi=document.querySelector('#main-content input[type="text"]');
      if(fi)fi.focus();
      e.preventDefault();
    }
    // Refresh
    else if(k==='r'){refresh();e.preventDefault();}
    // Date navigation
    else if(k==='['){
      var prevBtn=document.querySelector('.date-nav-btn:first-child');
      if(prevBtn&&!prevBtn.disabled)prevBtn.click();
      e.preventDefault();
    }
    else if(k===']'){
      var nextBtn=document.querySelector('.date-nav-btn:last-child');
      if(nextBtn&&!nextBtn.disabled)nextBtn.click();
      e.preventDefault();
    }
    // Number keys: know view tab switching or note slot switching
    else if(k>='1'&&k<='9'){
      if(view==='know'){
        // Check active tab: if Notes tab, switch slots; otherwise 1/2/3 switch tabs
        var activeTab=document.querySelector('.tab-btn.active');
        var tabName=activeTab?activeTab.dataset.tab:'';
        if(tabName==='notes'||tabName===''){
          window.switchNote(parseInt(k,10));
        } else if(k>='1'&&k<='3'){
          var tabBtns=document.querySelectorAll('.tab-btn');
          var idx=parseInt(k,10)-1;
          if(tabBtns[idx])tabBtns[idx].click();
        }
      }
      e.preventDefault();
    }
    // Edit focused item
    else if(k==='e'){
      var curE=_innerMode?(_innerItems(_cards()[_focusIdx])||[])[_innerIdx]:_cards()[_focusIdx];
      if(curE){
        var accName=curE.querySelector('.acc-name');
        var accType=curE.querySelector('.acc-type');
        if(accName&&accType){
          var t=accType.textContent.trim();
          if(t==='guide')openEdit('guide',accName.textContent.trim());
          else if(t==='prompt')openEdit('guide',accName.textContent.trim());
        }
        if(accName&&accName.textContent.trim()==='Working Memory')openEdit('memory');
        if(accName&&accName.textContent.trim()==='Rules')openEdit('rules');
      }
      e.preventDefault();
    }
    // Help overlay
    else if(k==='?'){
      var ho=document.getElementById('help-overlay');
      if(ho)ho.classList.toggle('open');
      e.preventDefault();
    }
    // Tab/Shift+Tab: card-level navigation
    else if(k==='Tab'&&!_innerMode){
      var cardsT=_cards();
      if(cardsT.length){
        if(e.shiftKey){
          _setFocus(_focusIdx>0?_focusIdx-1:cardsT.length-1);
        }else{
          _setFocus(_focusIdx<cardsT.length-1?_focusIdx+1:0);
        }
      }
      e.preventDefault();
    }
    // Escape: exit inner mode first, then clear focus
    else if(k==='Escape'){
      closeSearch();
      if(_innerMode){
        _innerMode=false;_innerIdx=-1;
        _clearAllFocus();
        var cards=_cards();if(cards[_focusIdx])cards[_focusIdx].classList.add('hive-focus');
      }else{
        _clearAllFocus();
        _focusIdx=-1;
        if(document.activeElement)document.activeElement.blur();
      }
    }
  });

  // Tab key in edit textarea inserts tab
  document.addEventListener('keydown',function(e){
    if(e.key==='Tab'&&e.target.id==='edit-textarea'){
      e.preventDefault();
      var ta=e.target;
      var start=ta.selectionStart;
      var end=ta.selectionEnd;
      ta.value=ta.value.substring(0,start)+'  '+ta.value.substring(end);
      ta.selectionStart=ta.selectionEnd=start+2;
    }
  });

  // --- Settings toggle ---
  document.addEventListener('change',function(e){
    var cb=e.target.closest('.setting-cb');
    if(cb){
      var key=cb.dataset.key;
      var val=cb.checked;
      fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:key,value:val})})
        .then(function(r){return r.json();})
        .then(function(d){if(!d.ok){cb.checked=!val;}})
        .catch(function(){cb.checked=!val;});
      return;
    }
    var sel=e.target.closest('.setting-select');
    if(sel){
      var key=sel.dataset.key;
      var val=sel.value;
      fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:key,value:val})})
        .then(function(r){return r.json();})
        .catch(function(){});
    }
  });

  // --- Sound test button ---
  document.addEventListener('click',function(e){
    var btn=e.target.closest('.sound-test-btn');
    if(!btn)return;
    var type=btn.dataset.soundType||'';
    btn.classList.add('playing');
    fetch('/api/sound-test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:type})})
      .then(function(r){return r.json();})
      .then(function(d){
        if(!d.ok){btn.title=d.error||'Error';}
        setTimeout(function(){btn.classList.remove('playing');},1500);
      })
      .catch(function(){
        setTimeout(function(){btn.classList.remove('playing');},1500);
      });
  });
})();
"""


# ---- Data providers ----


def _safe_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def _compute_tool_trends(
    sessions: list[dict], week_start: str, prev_week_start: str
) -> dict[str, str]:
    """Compute week-over-week tool usage trend arrows from session data.

    Returns {tool_name: "▲N%" or "▼N%"} for tools with >= 2% change.
    """
    tools_this_wk: dict[str, int] = {}
    tools_prev_wk: dict[str, int] = {}
    for s in sessions:
        day = s.get("day", "")
        for tool, count in (s.get("tool_counts") or s.get("tools") or {}).items():
            if day >= week_start:
                tools_this_wk[tool] = tools_this_wk.get(tool, 0) + count
            elif day >= prev_week_start:
                tools_prev_wk[tool] = tools_prev_wk.get(tool, 0) + count
    total_this = sum(tools_this_wk.values()) or 1
    total_prev = sum(tools_prev_wk.values()) or 1
    trends: dict[str, str] = {}
    for tool in tools_this_wk:
        t_pct = int(tools_this_wk[tool] / total_this * 100)
        p_pct = int(tools_prev_wk.get(tool, 0) / total_prev * 100)
        delta = t_pct - p_pct
        if abs(delta) >= 2:
            trends[tool] = f"\u25b2{delta}%" if delta > 0 else f"\u25bc{abs(delta)}%"
    return trends


def _get_status_data() -> dict:
    import re as re_mod

    from keephive.storage import (
        count_daily_entries,
        count_stale_facts,
        get_stale_facts,
        guides_dir,
        memory_file,
    )

    mem = memory_file()
    total_verified = 0
    stale = 0
    if mem.exists():
        text = mem.read_text()
        total_verified = len(re_mod.findall(r"\[verified:", text))
        stale = count_stale_facts()
    guide_count = sum(1 for _ in guides_dir().glob("*.md")) if guides_dir().exists() else 0
    try:
        from keephive.health import health_summary

        hooks_ok, mcp_ok, data_ok = health_summary()
    except Exception:
        hooks_ok, mcp_ok, data_ok = False, False, True

    # Stale fact details for accordion
    stale_facts: list[str] = []
    if stale > 0:
        raw_facts = _safe_call(get_stale_facts) or []
        for _, fact, raw_line in raw_facts:
            m = re_mod.search(r"\[verified:(\d{4}-\d{2}-\d{2})\]", raw_line)
            date_part = f" [{m.group(1)}]" if m else ""
            stale_facts.append(f"{fact[:80]}{date_part}")

    try:
        from keephive.storage import open_todos as _open_todos

        todo_count = len(_open_todos())
    except Exception:
        todo_count = 0

    from datetime import timedelta as _timedelta

    from keephive.clock import get_today as _get_today

    yesterday = (_get_today() - _timedelta(days=1)).isoformat()

    # Activity metrics from stats (for merged activity section)
    activity_today = 0
    activity_week = 0
    activity_streak = 0
    activity_hours: dict[str, int] = {}
    try:
        from keephive.storage import read_stats

        _stats = read_stats()
        _days = _stats.get("days", {})
        _today_str = _get_today().isoformat()
        _week_start = (_get_today() - _timedelta(days=7)).isoformat()
        _today_day = _days.get(_today_str, {})
        activity_today = sum(_today_day.get("commands", {}).values())
        activity_hours = _today_day.get("hours", {})
        for _ds, _dd in _days.items():
            if _ds >= _week_start:
                activity_week += sum(_dd.get("commands", {}).values())
        from keephive.commands.stats import _calculate_streak

        activity_streak, _ = _calculate_streak(_days)
    except Exception:
        pass

    # Pipeline health for pulse score in home view
    pulse_score = 0
    pulse_delta = 0
    fresh_pct = 0.0
    capture_recall = 0.0
    try:
        from keephive.commands.stats import (
            _capture_mix,
            _knowledge_health,
            _productivity_pulse,
            _session_productivity,
        )

        kh = _knowledge_health()
        fresh_pct = kh.get("fresh_pct", 0)
        capture_recall = kh.get("capture_recall_ratio", 0)
        cm = _capture_mix()
        sp = _session_productivity()
        from keephive.storage import read_stats as _rs

        pulse = _productivity_pulse(kh, cm, sp, _rs())
        pulse_score = pulse.get("score", 0)
        pulse_delta = pulse.get("delta", 0)
    except Exception:
        pass

    # Pending rules count
    pending_rules_count = 0
    try:
        from keephive.storage import hive_dir

        pr_path = hive_dir() / ".pending-rules.md"
        if pr_path.exists():
            pr_text = pr_path.read_text().strip()
            if pr_text:
                pending_rules_count = sum(
                    1 for ln in pr_text.splitlines() if ln.strip().startswith("- ")
                )
    except Exception:
        pass

    return {
        "stale": stale,
        "total_verified": total_verified,
        "guide_count": guide_count,
        "today_entries": count_daily_entries(),
        "yesterday_entries": count_daily_entries(yesterday),
        "hooks_ok": hooks_ok,
        "mcp_ok": mcp_ok,
        "data_ok": data_ok,
        "stale_facts": stale_facts,
        "todo_count": todo_count,
        "activity_today": activity_today,
        "activity_week": activity_week,
        "activity_streak": activity_streak,
        "activity_hours": activity_hours,
        "pulse_score": pulse_score,
        "pulse_delta": pulse_delta,
        "fresh_pct": fresh_pct,
        "capture_recall": capture_recall,
        "pending_rules_count": pending_rules_count,
    }


def _get_log_data(date_str: str | None = None) -> dict:
    import re as re_mod

    from keephive.storage import daily_file, safe_read_text

    path = daily_file(date_str)
    if not path.exists():
        from keephive.clock import get_today

        used_date = date_str if date_str else get_today().isoformat()
        return {"entries": [], "date": used_date}
    entries = []
    for line in safe_read_text(path).splitlines():
        m = re_mod.match(r"^- \[(\d{2}:\d{2}:\d{2})\]\s*(.*)", line)
        if m:
            ts, rest = m.group(1), m.group(2)
            upper = rest.upper()
            if "SESSION" in upper or "COMPACTED" in upper or "COMPACTION" in upper:
                continue
            cat = ""
            for c in ("FACT", "DECISION", "INSIGHT", "TODO", "CORRECTION"):
                if rest.upper().startswith(c + ":"):
                    cat = c.lower()
                    break
            if not cat:
                if rest.upper().startswith("DONE:"):
                    cat = "done"
                elif rest.upper().startswith(("AUTO-PROMOTED:", "AUTO-CAPTURED:")):
                    cat = "auto"
            entries.append({"time": ts[:5], "text": rest, "cat": cat})
    from keephive.clock import get_today

    used_date = date_str if date_str else get_today().isoformat()
    return {"entries": entries, "date": used_date}


def _get_todo_data() -> dict:
    from keephive.storage import due_recurring, open_todos

    return {"todos": open_todos(), "due": due_recurring()}


def _get_knowledge_data() -> dict:
    from pathlib import Path

    from keephive.storage import guides_dir, prompts_dir, safe_read_text

    guides = []
    if guides_dir().exists():
        for f in sorted(guides_dir().glob("*.md")):
            guides.append({"name": f.stem, "content": safe_read_text(f)})
    prompts = []
    if prompts_dir().exists():
        for f in sorted(prompts_dir().glob("*.md")):
            prompts.append({"name": f.stem, "content": safe_read_text(f)})
    skills = []
    skills_base = Path.home() / ".claude" / "skills"
    if skills_base.exists():
        for d in sorted(p for p in skills_base.iterdir() if p.is_dir()):
            skill_md = d / "SKILL.md"
            content = safe_read_text(skill_md) if skill_md.exists() else ""
            skills.append({"name": d.name, "content": content})
    return {"guides": guides, "prompts": prompts, "skills": skills}


def _get_soul_data() -> dict:
    import datetime
    import re as _re

    from keephive.storage import read_pending_improvements, read_soul, soul_file

    content = read_soul()
    last_modified = ""
    sf = soul_file()
    if sf.exists():
        mtime = sf.stat().st_mtime
        last_modified = datetime.datetime.fromtimestamp(mtime).strftime("%b %d")
    pending_count = len(read_pending_improvements())
    # Strip HTML template comments before storing (they're author hints, not content)
    if content:
        content = _re.sub(r"<!--.*?-->", "", content, flags=_re.DOTALL).strip()
    return {"soul": content, "last_modified": last_modified, "pending_count": pending_count}


def _render_soul_panel(data: dict) -> str:
    soul = data.get("soul", "").strip()
    last_mod = data.get("last_modified", "")
    pending_count = data.get("pending_count", 0)
    meta = f"updated {_e(last_mod)}" if last_mod else ""
    if soul:
        body = f'<div class="md">{render_md(soul)}</div>'
    else:
        body = (
            '<div class="empty">No SOUL.md yet. Run <code>hive setup</code> to meet KingBee.</div>'
        )
    pending_hint = ""
    if pending_count:
        pending_hint = (
            f'<div class="kpi-row" style="margin-top:6px">'
            f'<span style="color:#f0883e">🐝 {pending_count} '
            f"improvement{'s' if pending_count != 1 else ''} pending</span>"
            f'<code style="margin-left:8px">hive improve review</code>'
            f"</div>"
        )
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Agent Identity">'
        f'<div class="card-header">'
        f'<span class="card-title">🐝 Soul</span>'
        f'<span class="card-meta">{meta}</span>'
        f"</div>"
        f"{_cmd_hints(['hive edit soul', 'hive daemon status'])}"
        f'<div class="card-body">{body}{pending_hint}</div>'
        f"</div>"
    )


def _get_memory_data() -> dict:
    from keephive.storage import read_memory, read_rules

    return {"memory": read_memory(), "rules": read_rules()}


def _get_notes_data() -> dict:
    from keephive.storage import NOTE_SLOT_COUNT, active_slot, safe_read_text, slot_file

    active = active_slot()
    slots = []
    for n in range(1, NOTE_SLOT_COUNT + 1):
        f = slot_file(n)
        if f.exists():
            content = safe_read_text(f).strip()
            if content:
                lines = sum(1 for ln in content.splitlines() if ln.strip())
                slots.append({"slot": n, "content": content, "lines": lines, "active": n == active})
    return {"slots": slots}


_stats_data_cache: dict = {}
_STATS_CACHE_TTL: float = 30.0  # seconds; stats data changes rarely


def _get_stats_data() -> dict:
    import time

    now = time.monotonic()
    cached_ts = _stats_data_cache.get("ts", 0.0)
    if now - cached_ts < _STATS_CACHE_TTL:
        return _stats_data_cache["data"]
    data = _get_stats_data_fresh()
    _stats_data_cache["ts"] = now
    _stats_data_cache["data"] = data
    return data


def _get_stats_data_fresh() -> dict:
    from datetime import timedelta

    from keephive.clock import get_today
    from keephive.storage import read_stats

    stats = read_stats()
    days = stats.get("days", {})
    today = get_today().isoformat()
    week_start = (get_today() - timedelta(days=7)).isoformat()

    # Aggregate command counts
    cmd_totals: dict[str, int] = {}
    cmd_today: dict[str, int] = {}
    cmd_week: dict[str, int] = {}

    for day_str, day_data in days.items():
        cmds = day_data.get("commands", {})
        for name, count in cmds.items():
            cmd_totals[name] = cmd_totals.get(name, 0) + count
            if day_str == today:
                cmd_today[name] = cmd_today.get(name, 0) + count
            if day_str >= week_start:
                cmd_week[name] = cmd_week.get(name, 0) + count

    top = sorted(cmd_totals.items(), key=lambda x: -x[1])[:12]
    total_days = len(days)

    # Per-project stats
    projects: dict[str, int] = {}
    for day_data in days.values():
        for proj, pdata in day_data.get("projects", {}).items():
            projects[proj] = projects.get(proj, 0) + pdata.get("commands", 0)
    top_projects = sorted(projects.items(), key=lambda x: -x[1])[:5]

    # Streaks
    curr_streak, longest_streak = _safe_call(
        lambda: __import__(
            "keephive.commands.stats", fromlist=["_calculate_streak"]
        )._calculate_streak(days)
    ) or (0, 0)

    # 14-day per-day command totals for sparkline (label, count, iso_date)
    daily_spark: list[tuple[str, int, str]] = []
    for i in range(13, -1, -1):
        d = get_today() - timedelta(days=i)
        day_s = d.isoformat()
        total_cmds = sum(days.get(day_s, {}).get("commands", {}).values())
        daily_spark.append((d.strftime("%b %d"), total_cmds, day_s))

    # Today's hourly data
    today_str = get_today().isoformat()
    today_day = days.get(today_str, {})
    today_hours: dict[str, int] = today_day.get("hours", {})

    # Tool breakdown from session metrics (for What You Use panel)
    from keephive.storage import (
        count_log_entries_with_prefix,
        read_cc_sessions,
        read_sessions,
        session_metrics,
    )

    sm = _safe_call(session_metrics, days_back=30) or {}
    tool_totals = sm.get("tool_totals", {}) if isinstance(sm, dict) else {}
    tool_pct = sm.get("tool_pct", {}) if isinstance(sm, dict) else {}

    # Tool trend arrows (week-over-week, prefer CC data)
    cc_sess = _safe_call(read_cc_sessions, days_back=14) or []
    sessions = cc_sess if cc_sess else (_safe_call(read_sessions, days_back=14) or [])
    prev_week_start = (get_today() - timedelta(days=13)).isoformat()
    tool_trends = _compute_tool_trends(sessions, week_start, prev_week_start)

    # Per-day top commands for sparkline tooltips
    daily_spark_cmds: dict[str, list[tuple[str, int]]] = {}
    for spark_item in daily_spark:
        iso_d = spark_item[2] if len(spark_item) >= 3 else ""
        if iso_d:
            day_cmds = days.get(iso_d, {}).get("commands", {})
            if day_cmds:
                top_3 = sorted(day_cmds.items(), key=lambda x: -x[1])[:3]
                daily_spark_cmds[iso_d] = top_3

    # Quality KPIs (output metrics for Activity panel)
    insights = _safe_call(count_log_entries_with_prefix, "INSIGHT:", 90) or 0
    decisions = _safe_call(count_log_entries_with_prefix, "DECISION:", 90) or 0
    corrections = _safe_call(count_log_entries_with_prefix, "CORRECTION:", 90) or 0
    todos_done = _safe_call(count_log_entries_with_prefix, "DONE:", 90) or 0
    standups = _safe_call(count_log_entries_with_prefix, "STANDUP:", 90) or 0

    return {
        "commands": top,
        "today": cmd_today,
        "week": cmd_week,
        "total_days": total_days,
        "projects": top_projects,
        "curr_streak": curr_streak,
        "longest_streak": longest_streak,
        "daily_spark": daily_spark,
        "daily_spark_cmds": daily_spark_cmds,
        "today_hours": today_hours,
        "tool_totals": tool_totals,
        "tool_pct": tool_pct,
        "tool_trends": tool_trends,
        "insights": insights,
        "decisions": decisions,
        "corrections": corrections,
        "todos_done": todos_done,
        "standups": standups,
    }


def _get_ps_data() -> dict:
    import os

    from keephive.commands.ps import (
        _count_claude_processes,
        _recent_projects,
    )
    from keephive.storage import read_stats

    cwd = os.getcwd()
    stats = read_stats()
    projects = _safe_call(_recent_projects, stats, cwd) or []
    active = _safe_call(_count_claude_processes) or 0
    return {"projects": projects[:8], "active_sessions": active}


def _get_recent_facts_data() -> dict:
    from keephive.storage import get_key_entries_past_days

    entries = get_key_entries_past_days(days=7, limit=15)
    return {"entries": entries}


def _get_standup_data() -> dict:
    try:
        from keephive.commands.standup import _gather_raw_data

        return _gather_raw_data()
    except Exception:
        return {
            "recent_done": [],
            "open_todos": [],
            "insights": [],
            "open_prs": [],
            "merged_prs": [],
            "closed_prs": [],
            "daily_text": "",
        }


# ---- Panel renderers ----


def _e(text: str) -> str:
    """HTML-escape a string."""
    return _html.escape(str(text))


def _cmd_hints(cmds: list[str]) -> str:
    """Render a row of copyable command hint chips above card body."""
    chips = "".join(f'<span class="cmd-hint">{_e(c)}</span>' for c in cmds)
    return f'<div class="cmd-hints">{chips}</div>'


def _render_status_panel(data: dict) -> str:
    stale = data.get("stale", 0)
    total = data.get("total_verified", 0)
    today_entries = data.get("today_entries", 0)
    hooks_ok = data.get("hooks_ok", False)
    mcp_ok = data.get("mcp_ok", False)
    data_ok = data.get("data_ok", True)
    stale_facts = data.get("stale_facts", [])
    todo_count = data.get("todo_count", 0)

    # Health dots in header (moved from body)
    def _header_dot(ok: bool, label: str) -> str:
        cls = "ok" if ok else "off"
        return f'<span class="health-dot {cls}" title="{label}"></span>'

    health_dots = (
        '<div class="health-dots">'
        + _header_dot(hooks_ok, "hooks")
        + _header_dot(mcp_ok, "mcp")
        + _header_dot(data_ok, "data")
        + "</div>"
    )

    # Data for metrics
    fresh_pct = data.get("fresh_pct", 0)
    capture_recall = data.get("capture_recall", 0)
    activity_today = data.get("activity_today", 0)
    activity_week = data.get("activity_week", 0)
    activity_streak = data.get("activity_streak", 0)
    activity_hours = data.get("activity_hours", {})
    pending_rules_count = data.get("pending_rules_count", 0)

    # Full-width 2-column layout: left = KPIs + gauge, right = activity + heatmap
    stale_cls = "warn" if stale > 0 else "ok"

    # Left column: core health KPIs + gauge
    kpi_html = (
        f'<div class="kpi-row" style="justify-content:flex-start;gap:16px">'
        f'<div class="kpi-item"><div class="kpi-value">{total}</div><div class="kpi-label">verified facts</div></div>'
        f'<div class="kpi-item"><div class="kpi-value {stale_cls}">{stale}</div><div class="kpi-label">stale</div></div>'
        f'<div class="kpi-item"><div class="kpi-value">{todo_count}</div><div class="kpi-label">open todos</div></div>'
        f'<div class="kpi-item"><div class="kpi-value">{today_entries}</div><div class="kpi-label">logged today</div></div>'
        f"</div>"
    )
    gauge_html = ""
    if fresh_pct > 0 or capture_recall > 0:
        fp_clamped = max(0, min(100, fresh_pct))
        gauge_html = (
            f'<div class="gauge-row" style="margin-top:6px">'
            f'<span class="gauge-label" style="width:60px">freshness</span>'
            f'<div class="gauge-track">'
            f'<div class="gauge-fill" style="width:{fp_clamped:.0f}%;background:#3fb950"></div></div>'
            f'<span class="gauge-pct">{fresh_pct:.0f}%</span>'
            f"</div>"
            f'<div style="font-size:11px;color:#8b949e;margin-top:2px">'
            f'<span style="color:#58a6ff">{capture_recall:.0f}%</span> recall'
            f' &middot; <a href="/stats" style="color:#484f58;text-decoration:none">details &rarr;</a></div>'
        )

    # Stale facts (inline)
    stale_html = ""
    if stale > 0 and stale_facts:
        items_html = "".join(
            f'<div class="fact-item"><span class="fact-text">{_e(f)}</span></div>'
            for f in stale_facts
        )
        label = f"{stale} stale fact{'s' if stale != 1 else ''} &#9658;"
        stale_html = (
            f'<details class="stale-accordion" style="margin-top:4px">'
            f'<summary class="stale-summary">{label}</summary>'
            f'<div style="margin-top:4px">{items_html}</div>'
            f"</details>"
        )

    # Pending rules
    pending_html = ""
    if pending_rules_count > 0:
        s = "s" if pending_rules_count != 1 else ""
        pending_html = (
            f'<div style="margin-top:4px;padding:3px 8px;border-radius:4px;background:#e3b34122;border:1px solid #e3b34144;font-size:11px;color:#e3b341">'
            f"{pending_rules_count} pending rule{s} &rarr; <code>hive rule review</code></div>"
        )

    left_col = f"{kpi_html}{gauge_html}{stale_html}{pending_html}"

    # Right column: activity KPIs + heatmap
    right_col = ""
    if activity_today > 0 or activity_week > 0 or activity_streak > 0:
        right_col = (
            f'<div class="kpi-row" style="justify-content:flex-start;gap:16px">'
            f'<div class="kpi-item"><div class="kpi-value">{activity_today}</div><div class="kpi-label">commands today</div></div>'
            f'<div class="kpi-item"><div class="kpi-value">{activity_week}</div><div class="kpi-label">this week</div></div>'
            f'<div class="kpi-item"><div class="kpi-value">{activity_streak}d</div><div class="kpi-label">streak</div></div>'
            f"</div>"
        )
        hourly = _render_hourly_heatmap(activity_hours)
        if hourly:
            right_col += hourly

    # Assemble: 2-column grid when full-width, stacks on narrow
    body_html = (
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start">'
        f"<div>{left_col}</div>"
        f"<div>{right_col}</div>"
        f"</div>"
    )

    hints = ["hive status", "hive verify", "hive doctor"]
    if stale > 0:
        hints = ["hive verify  \u2190 stale facts!", "hive status", "hive doctor"]
    elif not hooks_ok or not mcp_ok:
        hints = ["hive setup", "hive status", "hive doctor"]
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Status">'
        f'<div class="card-header"><span class="card-title">Status</span>{health_dots}</div>'
        f"{_cmd_hints(hints)}"
        f'<div class="card-body">{body_html}</div>'
        f"</div>"
    )


def _render_status_brief_panel(data: dict) -> str:
    stale = data.get("stale", 0)
    total = data.get("total_verified", 0)
    today_entries = data.get("today_entries", 0)
    todo_count = data.get("todo_count", 0)
    activity_today = data.get("activity_today", 0)
    fresh_pct = data.get("fresh_pct", 0)
    capture_recall = data.get("capture_recall", 0)
    stale_str = f' <span style="color:#e3b341">({stale} stale)</span>' if stale > 0 else ""
    activity_str = (
        f" &nbsp;|&nbsp; <span>{activity_today}</span> commands today" if activity_today > 0 else ""
    )
    pipeline_str = ""
    if fresh_pct > 0 or capture_recall > 0:
        pipeline_str = (
            f" &nbsp;|&nbsp; <span>{fresh_pct:.0f}%</span> fresh"
            f" &middot; <span>{capture_recall:.0f}%</span> recall"
        )
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Status">'
        f'<div class="card-body">'
        f'<div class="status-brief">'
        f"<span>{total}</span> verified facts{stale_str} &nbsp;|&nbsp; "
        f"<span>{today_entries}</span> logged today &nbsp;|&nbsp; "
        f"<span>{todo_count}</span> open todos"
        f"{activity_str}{pipeline_str}"
        f"</div></div></div>"
    )


def _render_log_panel(
    data: dict, limit: int = 0, show_nav: bool = True, see_more_url: str = ""
) -> str:
    from datetime import date as _date
    from datetime import timedelta

    from keephive.clock import get_today as _get_today

    entries = data.get("entries", [])
    date_str = data.get("date", "")
    total = len(entries)
    truncated = False
    if limit > 0 and total > limit:
        entries = entries[-limit:]
        truncated = True
    meta = f"{total} entries" if total else ""

    # Date navigation (only when show_nav=True)
    nav_html = ""
    if show_nav:
        today_str = _get_today().isoformat()
        try:
            cur = _date.fromisoformat(date_str)
            prev_str = (cur - timedelta(days=1)).isoformat()
            next_str = (cur + timedelta(days=1)).isoformat()
            is_today = date_str >= today_str
        except ValueError:
            prev_str = next_str = date_str
            is_today = True
        next_disabled = " disabled" if is_today else ""
        nav_html = (
            f'<div class="log-date-nav">'
            f'<button class="date-nav-btn" onclick="loadLog(\'{_e(prev_str)}\')" title="Previous day">&#8249;</button>'
            f'<span class="log-date-label">{_e(date_str)}</span>'
            f'<button class="date-nav-btn"{next_disabled} onclick="loadLog(\'{_e(next_str)}\')" title="Next day">&#8250;</button>'
            f"</div>"
        )

    _CAT_LABELS = {
        "fact": "FACT",
        "decision": "DEC",
        "insight": "INS",
        "todo": "TODO",
        "correction": "COR",
        "done": "DONE",
        "auto": "AUTO",
    }
    # done and auto use separate CSS class names to avoid collisions
    _CAT_CLS = {"done": "done-cat", "auto": "auto-cat", "todo": "todo-color"}
    # Prefixes to strip from display text when the badge already shows the category
    _CAT_PREFIX = {
        "fact": "FACT:",
        "decision": "DECISION:",
        "insight": "INSIGHT:",
        "todo": "TODO:",
        "correction": "CORRECTION:",
        "done": "DONE:",
        "auto": "AUTO-CAPTURED:",
    }
    rows = ""
    for e in reversed(entries):
        cat = e.get("cat", "")
        badge = ""
        if cat and cat in _CAT_LABELS:
            badge = f'<span class="log-tag log-tag-{cat}">{_CAT_LABELS[cat]}</span>'
        cat_cls = f" {_CAT_CLS.get(cat, cat)}" if cat else ""
        text = e["text"]
        if cat and cat in _CAT_PREFIX:
            pfx = _CAT_PREFIX[cat]
            if text.upper().startswith(pfx):
                text = text[len(pfx) :].lstrip()
            elif cat == "auto" and text.upper().startswith("AUTO-PROMOTED:"):
                text = text[len("AUTO-PROMOTED:") :].lstrip()
        rows += (
            f'<div class="log-entry" data-type="{_e(cat)}" tabindex="0" role="listitem">'
            f'<span class="log-time">{_e(e["time"])}</span>'
            f'<span class="log-text{cat_cls}">{badge}{_e(text)}</span>'
            f"</div>"
        )
    if not rows:
        rows = '<div class="empty">No entries for this date</div>'

    see_more_html = ""
    if truncated and see_more_url:
        see_more_html = (
            f'<a class="log-see-more" href="{_e(see_more_url)}">See all {total} entries \u2192</a>'
        )
    elif truncated:
        remaining = total - limit
        next_limit = limit + 25
        see_more_html = (
            f'<div class="log-show-more">'
            f'<button onclick="loadLogMore({next_limit})">{remaining} more &mdash; show next 25</button>'
            f'<button onclick="loadLogMore(0)">show all {total}</button>'
            f"</div>"
        )

    # Filter bar: only when enough entries with type diversity
    filter_html = ""
    all_entries = data.get("entries", [])
    cats_present = {e.get("cat", "") for e in all_entries} - {""}
    if total > 10 and len(cats_present) > 1:
        _FILTER_LABELS = [
            ("", "All"),
            ("fact", "FACT"),
            ("todo", "TODO"),
            ("done", "DONE"),
            ("insight", "INS"),
            ("decision", "DEC"),
            ("correction", "COR"),
            ("auto", "AUTO"),
        ]
        btns = ""
        for cat_key, label in _FILTER_LABELS:
            if cat_key == "" or cat_key in cats_present:
                active_cls = " active" if cat_key == "" else ""
                pressed = "true" if cat_key == "" else "false"
                btns += f'<button class="log-filter-btn{active_cls}" data-type="{_e(cat_key)}" aria-pressed="{pressed}">{label}</button>'
        filter_html = f'<div class="log-filter">{btns}</div>'

    data_panel_attr = ' data-panel="log"' if show_nav else ""
    aria_log = ' tabindex="0" role="region" aria-label="Daily log"'
    title = "Today's Log" if not date_str or date_str == _get_today().isoformat() else "Log"
    log_hints = _cmd_hints(['hive remember "FACT: ..."', "hive log", "hive log summarize"])
    log_input = (
        '<form class="panel-input" data-action="/api/remember" data-field="text">'
        '<input type="text" placeholder="hive remember \u2014 fact or note..." autocomplete="off">'
        '<button type="submit">+</button>'
        "</form>"
    )
    return (
        f'<div class="card"{data_panel_attr}{aria_log}>'
        f'<div class="card-header">'
        f'<span class="card-title">{title}</span>'
        f"{nav_html}"
        f'<span class="card-meta">{meta}</span>'
        f"</div>"
        f"{log_hints}"
        f"{filter_html}"
        f"{log_input}"
        f'<div class="card-body">{rows}{see_more_html}</div>'
        f"</div>"
    )


def _render_log_brief_panel(data: dict) -> str:
    return _render_log_panel(data, limit=5, show_nav=False)


def _render_log_home_panel(data: dict) -> str:
    """Log panel for home view: 25 recent entries with date nav."""
    return _render_log_panel(data, limit=25, show_nav=True)


def _render_todo_panel(data: dict, limit: int = 0) -> str:
    from datetime import date

    from keephive.clock import get_today

    todos = data.get("todos", [])
    total = len(todos)
    if limit > 0:
        todos = list(reversed(todos))[:limit]
    else:
        todos = list(reversed(todos))
    today_str = get_today().isoformat()
    rows = ""
    for d, _, text in todos:
        try:
            age = (date.fromisoformat(today_str) - date.fromisoformat(d)).days
        except ValueError:
            age = 0
        age_cls = "vold" if age > 7 else ("old" if age > 2 else "")
        age_label = f"{age}d" if age > 0 else "now"
        rows += (
            f'<div class="todo-item" tabindex="0" role="listitem" aria-label="TODO: {_e(text)}">'
            f'<span class="todo-age {age_cls}">{age_label}</span>'
            f'<span class="todo-text">{_e(text)}</span>'
            f'<button class="todo-done-btn" data-pattern="{_e(text)}" title="Mark done" aria-label="Mark done: {_e(text[:40])}">&#10003;</button>'
            f"</div>"
        )
    if not rows:
        rows = '<div class="empty">No open TODOs</div>'
    meta = f"{total}" if total else ""
    todo_input = (
        '<form class="panel-input" data-action="/api/todo/add" data-field="text">'
        '<input type="text" placeholder="Add a TODO..." autocomplete="off">'
        '<button type="submit">+</button>'
        "</form>"
    )
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Open TODOs">'
        f'<div class="card-header"><span class="card-title">Open TODOs</span><span class="card-meta">{meta}</span></div>'
        f"{_cmd_hints(['hive todo <text>', 'hive todo done <pat>', 'hive todo'])}"
        f"{todo_input}"
        f'<div class="card-body">{rows}</div>'
        f"</div>"
    )


def _render_todo_brief_panel(data: dict) -> str:
    return _render_todo_panel(data, limit=5)


def _render_recurring_panel(data: dict) -> str:
    due = data.get("due", [])
    rows = ""
    for freq, text, overdue in due:
        due_str = f"+{overdue}d" if overdue > 0 else "due"
        due_cls = "overdue" if overdue > 0 else ""
        rows += (
            f'<div class="recurring-item">'
            f'<span class="recurring-freq">[{_e(freq)}]</span>'
            f'<span class="recurring-text">{_e(text)}</span>'
            f'<span class="recurring-due {due_cls}">{due_str}</span>'
            f"</div>"
        )
    if not rows:
        rows = '<div class="empty">No due recurring tasks</div>'
    meta = f"{len(due)} due" if due else ""
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Recurring tasks">'
        f'<div class="card-header"><span class="card-title">Recurring</span><span class="card-meta">{meta}</span></div>'
        f"{_cmd_hints(['hive todo repeat daily <task>', 'hive todo done <pat>'])}"
        f'<div class="card-body">{rows}</div>'
        f"</div>"
    )


def _render_knowledge_panel(data: dict) -> str:
    guides = data.get("guides", [])
    prompts = data.get("prompts", [])
    skills = data.get("skills", [])
    rows = ""

    # Build command mapping for smart deduplication (Phase 9)
    from keephive.cli import _CANONICAL

    _cmd_aliases: dict[str, str] = {}  # guide_stem -> "hive <cmd>"
    _alias_to_canon = {v: v for v in set(_CANONICAL.values())}
    for g in guides:
        stem = g["name"].lower().replace("-", " ").replace("_", " ")
        content_lower = g["content"].lower()
        for canon in _alias_to_canon:
            # Check if guide stem contains command name or content references it 3+ times
            if canon in stem or content_lower.count(f"hive {canon}") >= 3:
                _cmd_aliases[g["name"]] = f"hive {canon}"
                break

    if guides:
        rows += '<div class="know-divider">Guides</div>'
    for g in guides:
        body = f'<div class="acc-body md">{render_md(g["content"])}</div>'
        cmd_badge = ""
        if g["name"] in _cmd_aliases:
            cmd_badge = f'<span class="know-cmd">{_e(_cmd_aliases[g["name"]])}</span>'
        rows += (
            f'<div class="accordion" tabindex="0" role="button" aria-expanded="false" aria-label="{_e(g["name"])} guide">'
            f'<div class="acc-header">'
            f'<span class="acc-toggle">&#9654;</span>'
            f'<span class="acc-name">{_e(g["name"])}</span>'
            f"{cmd_badge}"
            f'<span class="acc-type">guide</span>'
            f"</div>{body}</div>"
        )

    if prompts:
        rows += '<div class="know-divider">Prompts</div>'
    for p in prompts:
        body = f'<div class="acc-body md">{render_md(p["content"])}</div>'
        rows += (
            f'<div class="accordion" tabindex="0" role="button" aria-expanded="false" aria-label="{_e(p["name"])} prompt">'
            f'<div class="acc-header">'
            f'<span class="acc-toggle">&#9654;</span>'
            f'<span class="acc-name">{_e(p["name"])}</span>'
            f'<span class="acc-type">prompt</span>'
            f"</div>{body}</div>"
        )

    if skills:
        rows += '<div class="know-divider">Skills</div>'
    for s in skills:
        name = s["name"]
        content = s.get("content", "")
        if content:
            body = f'<div class="acc-body md">{render_md(content)}</div>'
            toggle = "&#9654;"
            toggle_style = ""
        else:
            body = ""
            toggle = "&#8212;"
            toggle_style = ' style="color:#30363d"'
        rows += (
            f'<div class="accordion" tabindex="0" role="button" aria-expanded="false" aria-label="{_e(name)} skill">'
            f'<div class="acc-header">'
            f'<span class="acc-toggle"{toggle_style}>{toggle}</span>'
            f'<span class="acc-name">{_e(name)}</span>'
            f'<span class="acc-type">skill</span>'
            f"</div>{body}</div>"
        )
    if not rows:
        rows = '<div class="empty">No knowledge guides yet — hive knowledge edit &lt;name&gt;</div>'
    total = len(guides) + len(prompts) + len(skills)
    meta = f"{len(guides)}g / {len(prompts)}p / {len(skills)}sk" if total else ""
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Knowledge">'
        f'<div class="card-header"><span class="card-title">Knowledge</span><span class="card-meta">{meta}</span></div>'
        f"{_cmd_hints(['hive knowledge edit <name>', 'hive prompt edit <name>', 'hive knowledge <name>', 'hive reflect draft <topic>'])}"
        f'<div class="card-body">{rows}</div>'
        f"</div>"
    )


def _render_knowledge_limited_panel(data: dict) -> str:
    """Knowledge panel - shows all guides/prompts as collapsed accordions."""
    return _render_knowledge_panel(data)


def _render_knowledge_compact_panel(data: dict) -> str:
    """Flat scannable list of all knowledge items (no accordions). Links to /know."""
    guides = data.get("guides", [])
    prompts = data.get("prompts", [])
    skills = data.get("skills", [])
    total = len(guides) + len(prompts) + len(skills)
    rows = ""
    for g in guides:
        rows += f'<a class="know-item" href="/know"><span class="acc-type">guide</span> <span class="know-name">{_e(g["name"])}</span></a>'
    for p in prompts:
        rows += f'<a class="know-item" href="/know"><span class="acc-type">prompt</span> <span class="know-name">{_e(p["name"])}</span></a>'
    for s in skills:
        rows += f'<a class="know-item" href="/know"><span class="acc-type">skill</span> <span class="know-name">{_e(s["name"])}</span></a>'
    if not rows:
        rows = '<div class="empty">No knowledge items</div>'
    meta = f"{total} items" if total else ""
    link = '<a class="summary-link" href="/know">Expand all &rarr;</a>'
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Knowledge">'
        f'<div class="card-header"><span class="card-title">Knowledge</span><span class="card-meta">{meta}</span></div>'
        f'<div class="card-body">{rows}</div>'
        f"{link}"
        f"</div>"
    )


def _render_notes_compact_panel(data: dict) -> str:
    """Compact tile grid: one tile per populated slot. Click to expand inline."""
    slots = data.get("slots", [])
    tiles = ""
    for s in slots:
        active_cls = " active" if s["active"] else ""
        # Preview: first non-slot-header line, truncated
        content_lines = s["content"].splitlines()
        body_lines = [ln for ln in content_lines if not re.match(r"^Slot \d+", ln)]
        content_body = "\n".join(body_lines).strip()
        preview_lines = [
            ln.strip() for ln in content_lines if ln.strip() and not re.match(r"^Slot \d+", ln)
        ]
        preview = (
            (preview_lines[0][:60] + "\u2026")
            if (preview_lines and len(preview_lines[0]) > 60)
            else (preview_lines[0] if preview_lines else "")
        )
        tiles += (
            f'<div class="note-tile{active_cls}" tabindex="0" role="button" aria-expanded="false" aria-label="Note slot {s["slot"]}">'
            f'<div class="note-tile-header">'
            f'<span class="note-tile-slot">{s["slot"]}</span>'
            f'<span class="note-tile-meta">{s["lines"]}L</span>'
            f"</div>"
            f'<div class="note-tile-preview">{_e(preview)}</div>'
            f'<div class="note-tile-body md">{render_md(content_body)}</div>'
            f"</div>"
        )
    if not tiles:
        tiles = '<div class="empty">No notes \u2014 hive note</div>'
    meta = f"{len(slots)} slots" if slots else ""
    link = '<a class="summary-link" href="/know">All notes &rarr;</a>'
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Notes">'
        f'<div class="card-header"><span class="card-title">Notes</span><span class="card-meta">{meta}</span></div>'
        f'<div class="card-body note-tiles">{tiles}</div>'
        f"{link}"
        f"</div>"
    )


def _render_memory_panel(data: dict) -> str:
    memory = data.get("memory", "")
    rules = data.get("rules", "")
    mem_html = f'<div class="md">{render_md(memory)}</div>' if memory.strip() else ""
    rules_html = f'<div class="md">{render_md(rules)}</div>' if rules.strip() else ""
    _empty_div = '<div class="empty">Empty</div>'
    mem_section = (
        f'<div class="accordion" tabindex="0" role="button" aria-expanded="false" aria-label="Working Memory">'
        f'<div class="acc-header"><span class="acc-toggle">&#9654;</span>'
        f'<span class="acc-name">Working Memory</span></div>'
        f'<div class="acc-body">{mem_html or _empty_div}</div>'
        f"</div>"
    )
    rules_section = (
        f'<div class="accordion" tabindex="0" role="button" aria-expanded="false" aria-label="Rules">'
        f'<div class="acc-header"><span class="acc-toggle">&#9654;</span>'
        f'<span class="acc-name">Rules</span></div>'
        f'<div class="acc-body">{rules_html or _empty_div}</div>'
        f"</div>"
    )
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Memory">'
        f'<div class="card-header"><span class="card-title">Memory</span></div>'
        f"{_cmd_hints(['hive mem <fact>', 'hive mem rm <pat>', 'hive rule <text>', 'hive edit memory'])}"
        f'<div class="card-body">{mem_section}{rules_section}</div>'
        f"</div>"
    )


def _render_notes_panel(data: dict) -> str:
    slots = data.get("slots", [])
    rows = ""
    for s in slots:
        active_cls = " active" if s["active"] else ""
        badge = f'<span class="slot-badge{active_cls}">Slot {s["slot"]}{" \u2605" if s["active"] else ""}</span>'
        # Strip the "Slot N ★" storage marker from rendered body — it's shown via badge
        content_lines = s["content"].splitlines()
        body_lines = [ln for ln in content_lines if not re.match(r"^Slot \d+", ln)]
        content_body = "\n".join(body_lines).strip()
        body = f'<div class="acc-body md">{badge}<br>{render_md(content_body)}</div>'
        meta = f"{s['lines']}L"
        # Build 1-line preview for collapsed state (first non-empty, non-slot-header line)
        preview_lines = [
            ln.strip() for ln in content_lines if ln.strip() and not re.match(r"^Slot \d+", ln)
        ]
        preview_text = (
            preview_lines[0][:68] + "\u2026"
            if preview_lines and len(preview_lines[0]) > 68
            else (preview_lines[0] if preview_lines else "")
        )
        preview_html = (
            f'<span class="acc-preview">{_e(preview_text)}</span>' if preview_text else ""
        )
        rows += (
            f'<div class="accordion">'
            f'<div class="acc-header">'
            f'<span class="acc-toggle">&#9654;</span>'
            f'<span class="acc-name">Note {s["slot"]}{" (active)" if s["active"] else ""}</span>'
            f"{preview_html}"
            f'<span class="acc-meta">{meta}</span>'
            f"</div>{body}</div>"
        )
    if not rows:
        rows = '<div class="empty">No notes — hive note</div>'
    meta = f"{len(slots)} slots" if slots else ""

    # Slot switcher buttons
    max_slot = max((s["slot"] for s in slots), default=4)
    slot_btns = ""
    for n in range(1, max_slot + 1):
        is_active = any(s["slot"] == n and s["active"] for s in slots)
        active_cls = " active" if is_active else ""
        slot_btns += f'<button class="slot-btn{active_cls}" onclick="switchNote({n})">{n}</button>'
    slot_switcher = f'<div class="slot-switcher">{slot_btns}</div>' if slot_btns else ""

    note_input = (
        '<form class="panel-input" data-action="/api/note/append" data-field="text">'
        '<input type="text" placeholder="Append to active note..." autocomplete="off">'
        '<button type="submit">+</button>'
        "</form>"
    )
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Notes">'
        f'<div class="card-header"><span class="card-title">Notes</span><span class="card-meta">{meta}</span></div>'
        f"{_cmd_hints(['hive note', 'hive note show', 'hive note clear', 'hive note.3'])}"
        f"{slot_switcher}"
        f"{note_input}"
        f'<div class="card-body">{rows}</div>'
        f"</div>"
    )


def _render_hourly_heatmap(hours: dict[str, int]) -> str:
    """Render an hourly heatmap HTML fragment. Returns empty string if no data."""
    if not hours or all(v == 0 for v in hours.values()):
        return ""
    from datetime import datetime as _dt

    current_hour = _dt.now().strftime("%H")
    vals = [hours.get(f"{h:02d}", 0) for h in range(24)]
    mx = max(vals) or 1
    bars = ""
    for h in range(24):
        v = vals[h]
        hk = f"{h:02d}"
        ratio = v / mx if v > 0 else 0
        sat = 30 + round(ratio * 50)
        lum = 20 + round(ratio * 25)
        bg = f"hsl(185,{sat}%,{lum}%)" if v > 0 else "#161b22"
        ht = max(2, round(ratio * 28))
        cur_cls = " current" if hk == current_hour else ""
        bars += f'<div class="heat-bar{cur_cls}" style="height:{ht}px;background:{bg}" title="{hk}:00 — {v} events"></div>'
    labels = ""
    for h in range(24):
        label_text = str(h) if h % 3 == 0 else ""
        labels += f"<span>{label_text}</span>"
    return (
        f'<div class="heatmap-wrap">'
        f'<div class="heatmap">{bars}</div>'
        f'<div class="heat-labels">{labels}</div>'
        f"</div>"
    )


def _render_stats_panel(data: dict) -> str:
    """Activity panel: sparkline, hourly heatmap, streaks."""
    total_days = data.get("total_days", 0)
    curr_streak = data.get("curr_streak", 0)
    longest_streak = data.get("longest_streak", 0)
    daily_spark = data.get("daily_spark", [])
    daily_spark_cmds = data.get("daily_spark_cmds", {})
    today_hours = data.get("today_hours", {})

    sparkline_html = ""
    # Support both 2-tuple (label, count) and 3-tuple (label, count, iso_date)
    has_data = False
    for item in daily_spark:
        count = item[1] if len(item) >= 2 else 0
        if count > 0:
            has_data = True
            break

    if daily_spark and has_data:
        max_c = max(item[1] for item in daily_spark) or 1
        bars = ""
        labels = ""
        for i, item in enumerate(daily_spark):
            label = item[0]
            count = item[1]
            iso_date = item[2] if len(item) >= 3 else ""
            is_today = i == len(daily_spark) - 1

            h = max(2, round(count / max_c * 48)) if count > 0 else 2

            # Determine day-of-week for coloring
            is_weekend = False
            dow_letter = ""
            if iso_date:
                try:
                    from datetime import date as _date

                    d = _date.fromisoformat(iso_date)
                    dow_letter = "MTWTFSS"[d.weekday()]
                    is_weekend = d.weekday() >= 5
                except (ValueError, IndexError):
                    pass

            if is_today:
                cls = " today"
                extra_bg = ""
            elif is_weekend:
                cls = " weekend"
                extra_bg = ""
            else:
                cls = ""
                if count > 0:
                    ratio = count / max_c
                    sat = 50 + round(ratio * 30)
                    lum = 25 + round(ratio * 20)
                    extra_bg = f";background:hsl(215,{sat}%,{lum}%)"
                else:
                    extra_bg = ";background:#161b22"

            # Enrich tooltip with top commands for that day
            spark_title = f"{_e(label)}: {count} cmds"
            if iso_date and iso_date in daily_spark_cmds:
                top_cmds = daily_spark_cmds[iso_date]
                cmd_summary = ", ".join(f"{_e(c)}:{n}" for c, n in top_cmds)
                spark_title += f" ({cmd_summary})"
            bars += f'<div class="spark-bar{cls}" style="height:{h}px{extra_bg}" title="{spark_title}"></div>'

            wk_cls = ' class="weekend"' if is_weekend else ""
            labels += f"<span{wk_cls}>{dow_letter}</span>"

        sparkline_html = (
            f'<div class="sparkline-wrap">'
            f'<div class="sparkline">{bars}</div>'
            f'<div class="spark-labels">{labels}</div>'
            f"</div>"
        )

    # Hourly heatmap (embedded in stats panel after sparkline)
    hourly_html = _render_hourly_heatmap(today_hours)

    streak_html = ""
    if total_days > 0:
        streak_html = (
            f'<div class="stat-row" style="margin-bottom:12px">'
            f'<div class="stat-item"><span class="stat-value">{curr_streak}</span><span class="stat-label">current streak</span></div>'
            f'<div class="stat-item"><span class="stat-value">{longest_streak}</span><span class="stat-label">longest streak</span></div>'
            f'<div class="stat-item"><span class="stat-value">{total_days}</span><span class="stat-label">days active</span></div>'
            f"</div>"
        )

    # Quality KPIs (output metrics: what you produced)
    insights = data.get("insights", 0)
    decisions = data.get("decisions", 0)
    corrections = data.get("corrections", 0)
    todos_done = data.get("todos_done", 0)
    standups = data.get("standups", 0)
    quality_html = ""
    if insights or decisions or corrections or todos_done or standups:
        quality_html = (
            f'<div style="border-top:1px solid #21262d;margin-top:10px;padding-top:8px">'
            f'<div class="stat-row">'
            f'<div class="stat-item"><span class="stat-value">{insights}</span><span class="stat-label">insights</span></div>'
            f'<div class="stat-item"><span class="stat-value">{decisions}</span><span class="stat-label">decisions</span></div>'
            f'<div class="stat-item"><span class="stat-value">{corrections}</span><span class="stat-label">corrections</span></div>'
            f'<div class="stat-item"><span class="stat-value">{todos_done}</span><span class="stat-label">completed</span></div>'
            f'<div class="stat-item"><span class="stat-value">{standups}</span><span class="stat-label">standups</span></div>'
            f"</div>"
            f"</div>"
        )

    meta = f"{total_days} days tracked" if total_days else ""
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Activity">'
        f'<div class="card-header"><span class="card-title">Activity</span><span class="card-meta">{meta}</span></div>'
        f"{_cmd_hints(['hive stats', 'hive stats -p <project>', 'hive stats yesterday'])}"
        f"{sparkline_html}"
        f"{hourly_html}"
        f'<div class="card-body">{streak_html}{quality_html}</div>'
        f"</div>"
    )


def _render_stats_platforms_panel(data: dict) -> str:
    platforms = data.get("platforms", {}).get("platforms", {})
    if not platforms:
        body = '<div class="empty">No platform telemetry yet</div>' + _cmd_hints(
            ["keephive setup --yes", "hive doctor"]
        )
    else:
        header = (
            "<tr>"
            "<th>Platform</th>"
            "<th>Status</th>"
            "<th>Telemetry events</th>"
            "<th>Top event types</th>"
            "</tr>"
        )
        rows = ""
        for slug, info in platforms.items():
            telemetry = info.get("telemetry", {})
            total = telemetry.get("total", 0)
            by_event = telemetry.get("by_event", {})
            top = ", ".join(
                f"{_e(evt)}:{cnt}"
                for evt, cnt in sorted(by_event.items(), key=lambda x: (-x[1], x[0]))[:4]
            )
            status = "online" if info.get("detected") else "offline"
            status_cls = "ok" if info.get("detected") else "warn"
            rows += (
                "<tr>"
                f"<td>{_e(info.get('title', slug.title()))}</td>"
                f'<td class="{status_cls}">{status}</td>'
                f"<td>{total}</td>"
                f"<td>{top or '—'}</td>"
                "</tr>"
            )
        body = f'<table class="stats-table stats-platform-table">{header}{rows}</table>'
    return (
        '<div class="card" tabindex="0" role="region" aria-label="Platform telemetry">'
        '<div class="card-header"><span class="card-title">Platforms</span></div>'
        f'<div class="card-body">{body}</div>'
        "</div>"
    )


def _render_stats_commands_panel(data: dict) -> str:
    """Command + tool breakdown as a standalone card."""
    commands = data.get("commands", [])
    today_map = data.get("today", {})
    week_map = data.get("week", {})
    tool_totals = data.get("tool_totals", {})
    tool_pct = data.get("tool_pct", {})
    tool_trends = data.get("tool_trends", {})

    cmd_rows = ""
    if commands:
        max_count = commands[0][1] or 1
        for name, total in commands:
            t = today_map.get(name, 0)
            w = week_map.get(name, 0)
            bar_w = max(2, round(total / max_count * 100))
            today_badge = (
                f'<span style="font-size:10px;color:#8b949e;white-space:nowrap"> · {t} today</span>'
                if t
                else ""
            )
            week_badge = (
                f'<span style="font-size:10px;color:#484f58;white-space:nowrap"> · {w} wk</span>'
                if w and not t
                else ""
            )
            cmd_rows += (
                f'<div style="display:flex;align-items:center;gap:8px;margin:2px 0"'
                f' title="{_e(name)}: {total} all-time, {t} today, {w} this week">'
                f'<span style="min-width:80px;max-width:80px;overflow:hidden;text-overflow:ellipsis'
                f';white-space:nowrap;color:#c9d1d9;font-size:12px">{_e(name)}</span>'
                f'<div style="flex:1;background:#161b22;border-radius:2px;height:12px">'
                f'<div style="width:{bar_w}%;background:#1f6feb;border-radius:2px;height:100%"></div>'
                f"</div>"
                f'<span style="min-width:34px;text-align:right;font-size:12px;color:#58a6ff'
                f';white-space:nowrap">{total}</span>'
                f"{today_badge}{week_badge}"
                f"</div>"
            )
        rows = cmd_rows
    else:
        rows = '<div class="empty">No usage data yet</div>'

    # Tool breakdown (moved from Sessions panel)
    tools_html = ""
    if tool_totals:
        sorted_tools = sorted(tool_totals.items(), key=lambda x: -x[1])[:6]
        max_tool = sorted_tools[0][1] if sorted_tools else 1
        tool_rows = ""
        for tool, count in sorted_tools:
            pct = int(tool_pct.get(tool, 0) * 100)
            bar_w = max(2, round(count / max_tool * 100))
            trend_str = tool_trends.get(tool, "")
            if trend_str:
                trend_color = "#3fb950" if "\u25b2" in trend_str else "#f85149"
                trend_inner = (
                    f'<span style="font-size:11px;color:{trend_color}">{_e(trend_str)}</span>'
                )
            else:
                trend_inner = '<span style="font-size:11px;color:#484f58">\u25ac</span>'
            trend_html = f'<span style="min-width:42px;display:inline-block;text-align:right">{trend_inner}</span>'
            tool_title = f"{_e(tool)}: {count} uses ({pct}% of total)"
            if trend_str:
                tool_title += f", {_e(trend_str)} vs last week"
            tool_rows += (
                f'<div style="display:flex;align-items:center;gap:8px;margin:2px 0" title="{tool_title}">'
                f'<span style="min-width:160px;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:right;color:#8b949e;font-size:12px">{_e(tool)}</span>'
                f"{trend_html}"
                f'<div style="flex:1;background:#161b22;border-radius:2px;height:14px">'
                f'<div style="width:{bar_w}%;background:#238636;border-radius:2px;height:100%"></div>'
                f"</div>"
                f'<span style="min-width:40px;text-align:right;font-size:12px;color:#8b949e;white-space:nowrap">{pct}%</span>'
                f"</div>"
            )
        tools_html = f'<div style="margin-top:12px"><div style="font-size:11px;color:#484f58;margin-bottom:4px">Tool usage (from sessions)</div>{tool_rows}</div>'

    return (
        f'<div class="card" tabindex="0" role="region" aria-label="What You Use">'
        f'<div class="card-header"><span class="card-title">What You Use</span></div>'
        f'<div class="card-body">{rows}{tools_html}</div>'
        f"</div>"
    )


def _render_ps_panel(data: dict) -> str:
    projects = data.get("projects", [])
    active = data.get("active_sessions", 0)
    rows = ""
    for p in projects:
        is_cur = p.get("is_current", False)
        name_cls = "current" if is_cur else ""
        dot = '<span class="ps-dot">●</span> ' if is_cur else "  "
        age = p.get("age", "")
        cmds = p.get("today_cmds", 0)
        meta = f"{cmds} cmd today" if cmds else age
        rows += (
            f'<div class="ps-item">'
            f"{dot}"
            f'<span class="ps-name {name_cls}">{_e(p.get("name", "?"))}</span>'
            f'<span class="ps-meta">{_e(meta)}</span>'
            f"</div>"
        )
    if not rows:
        rows = '<div class="empty">No recent projects</div>'
    proc_str = f"{active} active session{'s' if active != 1 else ''}"
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Projects">'
        f'<div class="card-header"><span class="card-title">Projects</span><span class="card-meta">{_e(proc_str)}</span></div>'
        f"{_cmd_hints(['hive ps', 'hive go', 'hive standup'])}"
        f'<div class="card-body">{rows}</div>'
        f"</div>"
    )


def _render_recent_facts_panel(data: dict) -> str:
    entries = data.get("entries", [])
    rows = ""
    for day_str, entry in entries:
        text = entry.lstrip("~ ").strip()
        rows += (
            f'<div class="fact-item">'
            f'<span class="fact-date">{_e(day_str)}</span> '
            f'<span class="fact-text">{_e(text)}</span>'
            f"</div>"
        )
    if not rows:
        rows = '<div class="empty">No recent insights</div>'
    facts_hints = _cmd_hints(
        ['hive remember "FACT: ..."', "hive recall <query>", "hive reflect scan"]
    )
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Recent insights">'
        f'<div class="card-header"><span class="card-title">Recent Insights</span><span class="card-meta">past 7d</span></div>'
        f"{facts_hints}"
        f'<div class="card-body">{rows}</div>'
        f"</div>"
    )


def _render_standup_panel(data: dict) -> str:
    recent_done = data.get("recent_done", [])
    open_todos = data.get("open_todos", [])
    open_prs = data.get("open_prs", [])

    rows = ""

    if recent_done:
        rows += '<div class="standup-section"><span class="standup-label">Done</span>'
        for _, text in recent_done[:5]:
            rows += f'<div class="standup-item done-item">&#10003; {_e(text)}</div>'
        rows += "</div>"

    if open_todos:
        rows += '<div class="standup-section"><span class="standup-label">Focus</span>'
        for _, _, text in open_todos[:5]:
            rows += f'<div class="standup-item">{_e(text)}</div>'
        rows += "</div>"

    if open_prs:
        rows += '<div class="standup-section"><span class="standup-label">Open PRs</span>'
        for pr in open_prs[:3]:
            title = pr.get("title", "?")
            rows += f'<div class="standup-item pr-item">{_e(title)}</div>'
        rows += "</div>"

    if not rows:
        rows = '<div class="empty">No standup data yet</div>'

    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Standup">'
        f'<div class="card-header"><span class="card-title">Today\'s Focus</span></div>'
        f"{_cmd_hints(['hive standup', 'hive todo', 'hive todo done <pat>'])}"
        f'<div class="card-body">{rows}</div>'
        f"</div>"
    )


def _get_stats_summary_data() -> dict:
    """Activity stats: sparkline, heatmap, streaks, lifetime capture counts."""
    from datetime import timedelta

    from keephive.clock import get_today
    from keephive.storage import read_stats

    data = read_stats()
    days = data.get("days", {})
    today_str = get_today().isoformat()
    week_start = (get_today() - timedelta(days=7)).isoformat()

    today_data = days.get(today_str, {})
    today_total = sum(today_data.get("commands", {}).values())
    today_hours: dict[str, int] = today_data.get("hours", {})

    week_total = 0
    for day_str, day_data in days.items():
        if day_str >= week_start:
            week_total += sum(day_data.get("commands", {}).values())

    curr_streak = 0
    longest_streak = 0
    try:
        from keephive.commands.stats import _calculate_streak

        curr_streak, longest_streak = _calculate_streak(days)
    except Exception:
        pass

    # 14-day sparkline data
    sparkline_values: list[int] = []
    sparkline_labels: list[str] = []
    for i in range(13, -1, -1):
        d = (get_today() - timedelta(days=i)).isoformat()
        day_cmds = sum(days.get(d, {}).get("commands", {}).values())
        sparkline_values.append(day_cmds)
        # Short label: "Mon DD"
        dt = get_today() - timedelta(days=i)
        sparkline_labels.append(dt.strftime("%b %d"))

    days_active = len([d for d, dd in days.items() if sum(dd.get("commands", {}).values()) > 0])

    # Lifetime capture counts from daily logs
    capture_counts: dict[str, int] = {}
    try:
        from keephive.storage import daily_dir

        dd = daily_dir()
        if dd.exists():
            for log_file in dd.glob("*.md"):
                for line in log_file.read_text().splitlines():
                    for cat in ("INSIGHT", "DECISION", "CORRECTION", "DONE"):
                        if f"] {cat}:" in line or f"] {cat} " in line:
                            capture_counts[cat] = capture_counts.get(cat, 0) + 1
                            break
    except Exception:
        pass

    # Standup count from commands
    standups = 0
    for _, day_data in days.items():
        cmds = day_data.get("commands", {})
        standups += cmds.get("standup", 0) + cmds.get("su", 0)

    capture_counts["standups"] = standups

    return {
        "today_total": today_total,
        "week_total": week_total,
        "curr_streak": curr_streak,
        "longest_streak": longest_streak,
        "days_active": days_active,
        "today_hours": today_hours,
        "daily_sparkline": sparkline_values,
        "daily_sparkline_labels": sparkline_labels,
        "capture_counts": capture_counts,
    }


def _render_stats_summary_panel(data: dict) -> str:
    curr_streak = data.get("curr_streak", 0)
    longest_streak = data.get("longest_streak", 0)
    days_active = data.get("days_active", 0)
    today_hours = data.get("today_hours", {})

    # Stats from daily data
    capture_counts = data.get("capture_counts", {})
    insights = capture_counts.get("INSIGHT", 0)
    decisions = capture_counts.get("DECISION", 0)
    corrections = capture_counts.get("CORRECTION", 0)
    completed = capture_counts.get("DONE", 0)
    standups = capture_counts.get("standups", 0)

    hourly = _render_hourly_heatmap(today_hours)

    # 14-day sparkline
    daily_sparkline = data.get("daily_sparkline", [])
    spark_html = ""
    if daily_sparkline:
        spark_blocks = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
        mx = max(daily_sparkline) or 1
        chars = "".join(spark_blocks[min(8, round(v / mx * 8))] for v in daily_sparkline)
        labels = data.get("daily_sparkline_labels", [])
        first_label = labels[0] if labels else ""
        last_label = labels[-1] if labels else ""
        spark_html = (
            f'<div style="margin:8px 0 4px">'
            f'<div style="font-family:monospace;font-size:14px;letter-spacing:1px;color:#58a6ff">{chars}</div>'
            f'<div style="display:flex;justify-content:space-between;font-size:9px;color:#484f58">'
            f"<span>{first_label}</span><span>{last_label}</span></div></div>"
        )

    # Streak + lifetime KPIs
    streak_kpis = (
        f'<div class="kpi-row">'
        f'<div class="kpi-item"><div class="kpi-value">{curr_streak}</div><div class="kpi-label">current streak</div></div>'
        f'<div class="kpi-item"><div class="kpi-value">{longest_streak}</div><div class="kpi-label">longest streak</div></div>'
        f'<div class="kpi-item"><div class="kpi-value">{days_active}</div><div class="kpi-label">days active</div></div>'
        f"</div>"
    )

    # Capture lifetime KPIs
    capture_kpis = ""
    if insights or decisions or corrections or completed:
        capture_kpis = (
            f'<div class="kpi-row" style="margin-top:4px">'
            f'<div class="kpi-item"><div class="kpi-value">{insights}</div><div class="kpi-label">insights</div></div>'
            f'<div class="kpi-item"><div class="kpi-value">{decisions}</div><div class="kpi-label">decisions</div></div>'
            f'<div class="kpi-item"><div class="kpi-value">{corrections}</div><div class="kpi-label">corrections</div></div>'
            f'<div class="kpi-item"><div class="kpi-value">{completed}</div><div class="kpi-label">completed</div></div>'
            f'<div class="kpi-item"><div class="kpi-value">{standups}</div><div class="kpi-label">standups</div></div>'
            f"</div>"
        )

    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Activity">'
        f'<div class="card-header"><span class="card-title">Activity</span>'
        f'<span class="card-meta">{days_active} days tracked</span></div>'
        f"{_cmd_hints(['hive stats', 'hive stats -p <project>', 'hive stats yesterday'])}"
        f'<div class="card-body">{spark_html}{hourly}{streak_kpis}{capture_kpis}</div>'
        f"</div>"
    )


def _get_knowledge_all_data() -> dict:
    """Composite data for the tabbed knowledge view: guides + memory + notes + health."""
    health: dict = {}
    try:
        from keephive.commands.stats import _knowledge_health

        health = _knowledge_health()
    except Exception:
        pass
    return {
        "knowledge": _get_knowledge_data(),
        "memory": _get_memory_data(),
        "notes": _get_notes_data(),
        "health": health,
    }


def _render_knowledge_tabbed_panel(data: dict) -> str:
    """Knowledge view with client-side tabs: Guides / Memory / Notes."""
    know_data = data.get("knowledge", {})
    mem_data = data.get("memory", {})
    notes_data = data.get("notes", {})
    health = data.get("health", {})

    guides_html = _render_knowledge_panel(know_data)
    memory_html = _render_memory_panel(mem_data)
    notes_html = _render_notes_panel(notes_data)

    # Memory tab badge: freshness gauge
    mem_badge = ""
    total_facts = health.get("total_facts", 0)
    if total_facts > 0:
        fp = health.get("fresh_pct", 0)
        ap = health.get("aging_pct", 0)
        sp = health.get("stale_pct", 0)
        fresh_count = health.get("fresh", 0)
        aging_count = health.get("aging", 0)
        stale_count = health.get("stale", 0)
        mem_badge = f" ({total_facts})"
        # Freshness header for memory tab
        memory_health_header = (
            f'<div style="padding:8px 12px;border-bottom:1px solid #21262d">'
            f'<div style="display:flex;height:6px;border-radius:3px;overflow:hidden;background:#21262d;margin-bottom:4px">'
            f'<div style="width:{fp:.0f}%;background:#3fb950"></div>'
            f'<div style="width:{ap:.0f}%;background:#e3b341"></div>'
            f'<div style="width:{sp:.0f}%;background:#f85149"></div>'
            f"</div>"
            f'<div style="font-size:11px;color:#8b949e">'
            f"{fresh_count} fresh &middot; {aging_count} aging &middot; {stale_count} stale"
            f"</div></div>"
        )
        memory_html = memory_health_header + memory_html

    tab_bar = (
        '<div class="tab-bar">'
        '<button class="tab-btn active" data-tab="guides">Guides</button>'
        f'<button class="tab-btn" data-tab="memory">Memory{mem_badge}</button>'
        '<button class="tab-btn" data-tab="notes">Notes</button>'
        "</div>"
    )

    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Knowledge">'
        f"{tab_bar}"
        f'<div class="tab-content active" data-tab="guides">{guides_html}</div>'
        f'<div class="tab-content" data-tab="memory">{memory_html}</div>'
        f'<div class="tab-content" data-tab="notes">{notes_html}</div>'
        f"</div>"
    )


def _get_session_data() -> dict:
    """Session metrics for the sessions panel: histogram + session list.

    Prefers Claude Code session-meta for accurate user message counts
    and full tool breakdown. Falls back to keephive hook data.
    """
    from datetime import timedelta

    from keephive.clock import get_today
    from keephive.storage import (
        read_cc_sessions,
        read_live_sessions,
        read_sessions,
        session_metrics,
    )

    sm = session_metrics(days_back=30)
    use_cc = sm.get("source") == "claude_code"

    # Use same data source for histogram and session list
    if use_cc:
        sessions = read_cc_sessions(days_back=30)
        msg_key = "user_messages"
        tool_key = "tool_counts"
    else:
        sessions = read_sessions(7)
        msg_key = "prompts"
        tool_key = "tools"

    # Merge in live sessions (currently running, not yet in session-meta)
    live: list[dict] = []
    try:
        live = read_live_sessions()
        if live:
            # Deduplicate: live sessions override any matching session_id in CC data
            live_ids = {s["session_id"] for s in live}
            sessions = [s for s in sessions if s["session_id"] not in live_ids]
            sessions = live + sessions
    except Exception:
        pass

    # Message histogram buckets
    buckets = {"0": 0, "1-5": 0, "6-10": 0, "11-20": 0, "21-50": 0, "51+": 0}
    for s in sessions:
        p = s.get(msg_key, 0)
        if p == 0:
            buckets["0"] += 1
        elif p <= 5:
            buckets["1-5"] += 1
        elif p <= 10:
            buckets["6-10"] += 1
        elif p <= 20:
            buckets["11-20"] += 1
        elif p <= 50:
            buckets["21-50"] += 1
        else:
            buckets["51+"] += 1

    # Messages today and this week
    today_str = get_today().isoformat()
    week_ago = (get_today() - timedelta(days=7)).isoformat()
    prompts_today = sum(s.get(msg_key, 0) for s in sessions if s.get("day") == today_str)
    prompts_week = sum(s.get(msg_key, 0) for s in sessions if s.get("day", "") >= week_ago)

    # Recent sessions (most recent first, limit 20)
    recent = sorted(sessions, key=lambda x: x.get("started", ""), reverse=True)[:20]

    # Session depth buckets: shallow / medium / deep
    # Calibrated for real user_message_count from CC session-meta (avg ~4 msgs)
    shallow = 0
    medium = 0
    deep = 0
    for s in sessions:
        user_msgs = s.get(msg_key, 0)
        unique_tools = len(s.get(tool_key, {}))
        if user_msgs >= 15 and unique_tools >= 4:
            deep += 1
        elif user_msgs >= 5:
            medium += 1
        else:
            shallow += 1

    # Tool distribution with week-over-week trends
    prev_week_start = (get_today() - timedelta(days=13)).isoformat()
    tool_trends = _compute_tool_trends(sessions, week_ago, prev_week_start)

    return {
        **sm,
        "buckets": buckets,
        "recent": recent,
        "depth_shallow": shallow,
        "depth_medium": medium,
        "depth_deep": deep,
        "prompts_today": prompts_today,
        "prompts_week": prompts_week,
        "tool_trends": tool_trends,
        "live_count": len(live),
    }


def _render_sessions_panel(data: dict) -> str:
    """Sessions panel: compact header stats, histogram, session list."""
    total = data.get("total_sessions", 0)
    avg_prompts = data.get("avg_prompts_per_session", 0)
    avg_dur = data.get("avg_duration_minutes", 0)
    compaction_rate = data.get("compaction_rate", 0)
    prompts_today = data.get("prompts_today", 0)
    prompts_week = data.get("prompts_week", 0)
    buckets = data.get("buckets", {})
    recent = data.get("recent", [])

    # Source-aware field keys and labels
    use_cc = data.get("source") == "claude_code"
    msg_key = "user_messages" if use_cc else "prompts"
    tool_key = "tool_counts" if use_cc else "tools"
    msg_label = "msgs" if use_cc else "prompts"
    msg_unit = "\u21b5" if use_cc else "p"

    if total == 0 and not recent:
        return (
            '<div class="card" tabindex="0" role="region" aria-label="Sessions">'
            '<div class="card-header"><span class="card-title">Sessions</span></div>'
            '<div class="card-body"><div class="empty">No session data yet</div></div>'
            "</div>"
        )

    # Session depth buckets
    d_shallow = data.get("depth_shallow", 0)
    d_medium = data.get("depth_medium", 0)
    d_deep = data.get("depth_deep", 0)

    # Compact header stats
    compact_pct = f" | {int(compaction_rate * 100)}% compacted" if compaction_rate > 0 else ""
    depth_html = ""
    if d_shallow + d_medium + d_deep > 0:
        depth_html = (
            f'<div class="stat-item" title="Sessions with 15+ {msg_label}, 4+ unique tools"><span class="stat-value">{d_deep}</span><span class="stat-label">deep sessions</span></div>'
            f'<div class="stat-item" title="Sessions with 5-14 {msg_label}"><span class="stat-value">{d_medium}</span><span class="stat-label">medium sessions</span></div>'
            f'<div class="stat-item" title="Sessions with fewer than 5 {msg_label}"><span class="stat-value">{d_shallow}</span><span class="stat-label">brief sessions</span></div>'
        )

    # Tier 1: Hero section with code velocity
    hero_section = ""
    if use_cc:
        lines_add = data.get("lines_added_week", 0)
        lines_rm = data.get("lines_removed_week", 0)
        git_commits_wk = data.get("git_commits_week", 0)
        if lines_add or lines_rm or git_commits_wk:
            hero_section = (
                f'<div style="text-align:center;padding:6px 0 8px;border-bottom:1px solid #21262d;margin-bottom:8px">'
                f'<div class="kpi-label" style="letter-spacing:1px;margin-bottom:2px">CODE VELOCITY</div>'
                f'<span class="metric-hero" style="font-size:28px;color:#3fb950">+{lines_add:,}</span>'
                f'<span class="metric-hero" style="font-size:28px;color:#6e7681">/</span>'
                f'<span class="metric-hero" style="font-size:28px;color:#f85149">-{lines_rm:,}</span>'
                f'<span class="metric-hero-unit"> lines</span>'
                f'<div class="kpi-row" style="margin-top:6px">'
                f'<div class="kpi-item"><div class="kpi-value">{git_commits_wk}</div><div class="kpi-label">commits</div></div>'
                f'<div class="kpi-item"><div class="kpi-value">{data.get("files_modified_week", 0)}</div><div class="kpi-label">files</div></div>'
                f'<div class="kpi-item"><div class="kpi-value">{total}</div><div class="kpi-label">sessions</div></div>'
                f"</div></div>"
            )

    # Tier 2: KPI stats
    header_stats = (
        f"{hero_section}"
        f'<div class="kpi-row">'
        f'<div class="kpi-item"><div class="kpi-value">{avg_prompts:.0f}</div><div class="kpi-label">avg {msg_label}</div></div>'
        f'<div class="kpi-item"><div class="kpi-value">{avg_dur:.0f}m</div><div class="kpi-label">avg length</div></div>'
        f'<div class="kpi-item"><div class="kpi-value">{prompts_today}</div><div class="kpi-label">{msg_label} today</div></div>'
        f'<div class="kpi-item"><div class="kpi-value">{prompts_week}</div><div class="kpi-label">{msg_label} week</div></div>'
        f"</div>"
        f'<div class="kpi-row" style="margin-top:4px">'
        f"{depth_html}"
        f"</div>"
    )

    # Prompt histogram (absorbed from sessions-detail)
    hist_html = ""
    bucket_values = list(buckets.values())
    max_b = max(bucket_values) if bucket_values else 1
    max_b = max_b or 1
    if any(v > 0 for v in bucket_values):
        bars = ""
        labels = ""
        for label, count in buckets.items():
            h = max(2, round(count / max_b * 70)) if count > 0 else 2
            bars += f'<div class="prompt-hist-bar" style="height:{h}px"><span>{count}</span></div>'
            labels += f"<span>{_e(label)}</span>"
        hist_label = "Messages per session" if use_cc else "Prompts per session"
        hist_html = (
            f'<div style="font-size:11px;color:#484f58;padding:6px 12px 0">{hist_label}</div>'
            f'<div class="prompt-hist">{bars}</div>'
            f'<div class="prompt-hist-labels">{labels}</div>'
        )

    # Filter buttons (All / Deep / Active)
    live_count = data.get("live_count", 0)
    filter_html = (
        '<div class="log-filter" data-filter-group="session" style="padding:4px 12px">'
        '<button class="log-filter-btn active" data-type="">All</button>'
        '<button class="log-filter-btn" data-type="deep">Deep</button>'
        '<button class="log-filter-btn" data-type="live">Active</button>'
        "</div>"
    )

    # Session list (absorbed from sessions-detail)
    list_html = ""
    if recent:
        items = ""
        for s in recent:
            is_live = s.get("is_live", False)
            started = s.get("started", "")
            # Convert UTC (Z suffix) to local time for display
            time_str = ""
            if started:
                try:
                    from datetime import datetime as _dt

                    utc = _dt.fromisoformat(started.replace("Z", "+00:00"))
                    time_str = utc.astimezone().strftime("%H:%M")
                except (ValueError, OSError):
                    time_str = started[11:16] if len(started) > 16 else started[:5]
            msgs = s.get(msg_key, 0)
            full_proj = s.get("project", "")
            slug = s.get("slug", "")
            # Use slug (human-readable) if available, else last path component
            proj = slug or (full_proj.rsplit("/", 1)[-1] if "/" in full_proj else full_proj)
            proj_title = full_proj  # full path shown on hover
            tools = s.get(tool_key, s.get("tools", {}))
            tool_str = ", ".join(
                f"{t}:{c}" for t, c in sorted(tools.items(), key=lambda x: -x[1])[:5]
            )
            # Depth classification for tooltip (aligned with stats.py thresholds)
            unique_tools = len(tools)
            if msgs >= 15 and unique_tools >= 4:
                depth_label = "deep"
            elif msgs >= 5:
                depth_label = "medium"
            else:
                depth_label = "shallow"

            # Live session badge and duration
            live_badge = ""
            duration_str = ""
            dur_title = "running" if is_live else "duration"
            if is_live:
                live_badge = '<span class="session-live">LIVE</span>'
            dur = s.get("duration_minutes", 0)
            if dur >= 60:
                duration_str = f"{dur // 60}h {dur % 60}m"
            elif dur > 0:
                duration_str = f"{dur}m"
            elif is_live:
                duration_str = "<1m"  # only show <1m for live; 0 on completed = unknown

            # Permission mode badge (only for non-default modes, only on live sessions)
            mode_badge = ""
            pm = s.get("permission_mode", "")
            if pm == "plan":
                mode_badge = '<span class="session-mode session-mode-plan">PLAN</span>'
            elif pm in ("acceptEdits", "auto-edit"):
                mode_badge = '<span class="session-mode session-mode-auto">AUTO</span>'
            elif pm == "bypassPermissions":
                mode_badge = '<span class="session-mode session-mode-bypass">BYPASS</span>'

            depth_title = f"{depth_label} session: {msgs} {msg_label}, {unique_tools} tools"
            live_cls = " session-live-item" if is_live else ""
            items += (
                f'<div class="session-item{live_cls}" tabindex="0" title="{_e(depth_title)}" '
                f'data-live="{str(is_live).lower()}" data-depth="{depth_label}">'
                f"{live_badge}"
                f"{mode_badge}"
                f'<span class="session-time">{_e(time_str)}</span>'
                f'<span class="session-prompts" title="{msg_label}">{msgs}{msg_unit}</span>'
            )
            if duration_str:
                items += (
                    f'<span class="session-duration" title="{dur_title}">{_e(duration_str)}</span>'
                )
            items += (
                f'<span class="session-proj" title="{_e(proj_title)}">{_e(proj)}</span>'
                f'<span class="session-tools">{_e(tool_str)}</span>'
                f"</div>"
            )
        list_html = f'<div class="session-list">{items}</div>'

    # Card header with live count badge
    live_badge_header = ""
    if live_count > 0:
        live_badge_header = f' <span class="session-live" style="font-size:9px;vertical-align:middle">{live_count} LIVE</span>'

    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Sessions">'
        f'<div class="card-header"><span class="card-title">Sessions{live_badge_header}</span><span class="card-meta">{total} tracked{compact_pct}</span></div>'
        f'<div class="card-body">{header_stats}</div>'
        f"{hist_html}"
        f"{filter_html}"
        f"{list_html}"
        f"</div>"
    )


def _get_trend_data() -> dict:
    """Week-over-week comparison for 8 KPIs + event source breakdown."""
    from datetime import timedelta

    from keephive.clock import get_today
    from keephive.storage import read_cc_sessions, read_sessions, read_stats

    stats = read_stats()
    days = stats.get("days", {})
    today = get_today()

    # Week boundaries
    this_week_start = (today - timedelta(days=6)).isoformat()
    prev_week_start = (today - timedelta(days=13)).isoformat()
    prev_week_end = (today - timedelta(days=7)).isoformat()

    # Commands this/prev week
    cmds_this = 0
    cmds_prev = 0
    for day_str, day_data in days.items():
        total = sum(day_data.get("commands", {}).values())
        if day_str >= this_week_start:
            cmds_this += total
        elif day_str >= prev_week_start and day_str < prev_week_end:
            cmds_prev += total

    # Session metrics this/prev week (prefer CC data)
    cc_sessions = read_cc_sessions(days_back=14)
    if cc_sessions:
        sessions = cc_sessions
        _mk = "user_messages"
    else:
        sessions = read_sessions(14)
        _mk = "prompts"
    sess_this = [s for s in sessions if s.get("day", "") >= this_week_start]
    sess_prev = [s for s in sessions if prev_week_start <= s.get("day", "") < prev_week_end]

    sess_count_this = len(sess_this)
    sess_count_prev = len(sess_prev)

    def _avg_msgs(slist: list) -> float:
        if not slist:
            return 0.0
        return sum(s.get(_mk, 0) for s in slist) / len(slist)

    def _avg_duration(slist: list) -> float:
        durs: list[float] = []
        for s in slist:
            # CC data has duration_minutes directly; cap at 24h to filter unclosed sessions
            dur_min = s.get("duration_minutes", 0)
            if dur_min and 0 < dur_min <= 1440:
                durs.append(dur_min)
                continue
            started = s.get("started", "")
            last_seen = s.get("last_seen", "")
            if started and last_seen:
                try:
                    from datetime import datetime

                    t0 = datetime.fromisoformat(started)
                    t1 = datetime.fromisoformat(last_seen)
                    mins = (t1 - t0).total_seconds() / 60.0
                    if 0 <= mins <= 1440:  # cap at 24h; filters unclosed/corrupted sessions
                        durs.append(mins)
                except ValueError:
                    pass
        return sum(durs) / len(durs) if durs else 0.0

    prompts_this = _avg_msgs(sess_this)
    prompts_prev = _avg_msgs(sess_prev)
    dur_this = _avg_duration(sess_this)
    dur_prev = _avg_duration(sess_prev)

    def _trend(curr: float, prev: float) -> str:
        if prev == 0:
            return "up" if curr > 0 else "flat"
        ratio = curr / prev
        if ratio > 1.1:
            return "up"
        elif ratio < 0.9:
            return "down"
        return "flat"

    # Aggregate event sources across all days (absorbed from Quality panel)
    source_totals: dict[str, int] = {}
    for day_data in days.values():
        for src, count in day_data.get("sources", {}).items():
            source_totals[src] = source_totals.get(src, 0) + count

    # Pipeline health KPIs for extended trends
    insights_this = 0
    insights_prev = 0
    todos_done_this = 0
    todos_done_prev = 0
    try:
        from keephive.storage import count_log_entries_by_prefix

        counts_7d = count_log_entries_by_prefix(days_back=7)
        counts_14d = count_log_entries_by_prefix(days_back=14)
        insights_this = counts_7d.get("INSIGHT", 0)
        insights_prev = max(0, counts_14d.get("INSIGHT", 0) - insights_this)
        todos_done_this = counts_7d.get("DONE", 0)
        todos_done_prev = max(0, counts_14d.get("DONE", 0) - todos_done_this)
    except Exception:
        pass

    fresh_pct_this = 0.0
    recall_pct_this = 0.0
    try:
        from keephive.commands.stats import _knowledge_health

        kh = _knowledge_health()
        fresh_pct_this = kh.get("fresh_pct", 0)
        recall_pct_this = kh.get("capture_recall_ratio", 0)
    except Exception:
        pass

    return {
        "kpis": [
            {
                "label": "Commands",
                "this": cmds_this,
                "prev": cmds_prev,
                "trend": _trend(cmds_this, cmds_prev),
                "fmt": "d",
            },
            {
                "label": "Sessions",
                "this": sess_count_this,
                "prev": sess_count_prev,
                "trend": _trend(sess_count_this, sess_count_prev),
                "fmt": "d",
            },
            {
                "label": "Msgs/session" if cc_sessions else "Prompts/session",
                "this": prompts_this,
                "prev": prompts_prev,
                "trend": _trend(prompts_this, prompts_prev),
                "fmt": ".1f",
            },
            {
                "label": "Avg duration",
                "this": dur_this,
                "prev": dur_prev,
                "trend": _trend(dur_this, dur_prev),
                "fmt": ".0f",
                "suffix": "m",
            },
            {
                "label": "Insights",
                "this": insights_this,
                "prev": insights_prev,
                "trend": _trend(insights_this, insights_prev),
                "fmt": "d",
            },
            {
                "label": "TODOs done",
                "this": todos_done_this,
                "prev": todos_done_prev,
                "trend": _trend(todos_done_this, todos_done_prev),
                "fmt": "d",
            },
            {
                "label": "Freshness",
                "this": fresh_pct_this,
                "prev": 0,
                "trend": "up"
                if fresh_pct_this >= 80
                else ("flat" if fresh_pct_this >= 50 else "down"),
                "fmt": ".0f",
                "suffix": "%",
            },
            {
                "label": "Recall rate",
                "this": recall_pct_this,
                "prev": 0,
                "trend": "up"
                if recall_pct_this >= 80
                else ("flat" if recall_pct_this >= 50 else "down"),
                "fmt": ".0f",
                "suffix": "%",
            },
        ],
        "sources": source_totals,
    }


def _render_trends_panel(data: dict) -> str:
    """Render week-over-week trend arrows for KPIs + event source bars."""
    kpis = data.get("kpis", [])
    sources = data.get("sources", {})
    arrows = {"up": "\u25b2", "down": "\u25bc", "flat": "\u2014"}
    rows = ""
    for kpi in kpis:
        fmt = kpi.get("fmt", "d")
        suffix = kpi.get("suffix", "")
        if fmt == "d":
            this_str = str(int(kpi["this"]))
            prev_str = str(int(kpi["prev"]))
        else:
            this_str = f"{kpi['this']:{fmt}}"
            prev_str = f"{kpi['prev']:{fmt}}"
        # Gauge fill %
        if suffix == "%":
            gauge_pct = min(100, int(kpi["this"]))
        else:
            # Scale relative to max(this, prev); when prev=0, bar fills to 100%
            mx = max(kpi["this"], kpi["prev"]) or 1
            gauge_pct = round(kpi["this"] / mx * 100)
        gauge_pct = max(0, min(100, gauge_pct))
        # Gauge color: % KPIs use health-based color; others use trend direction
        if suffix == "%":
            gauge_color = (
                "#3fb950" if kpi["this"] >= 80 else "#e3b341" if kpi["this"] >= 50 else "#f85149"
            )
        else:
            gauge_color = {"up": "#3fb950", "down": "#f85149", "flat": "#484f58"}.get(
                kpi["trend"], "#484f58"
            )
        gauge_html = (
            f'<div class="gauge-track" style="flex:1;margin:0 6px">'
            f'<div class="gauge-fill" style="width:{gauge_pct}%;background:{gauge_color}"></div>'
            f"</div>"
        )
        # Delta: only show comparison when prev week has real data
        has_prev = kpi.get("prev", 0) > 0
        if has_prev:
            arrow = arrows.get(kpi["trend"], "\u2014")
            delta_html = (
                f'<span class="trend-delta trend-{kpi["trend"]}">{arrow} {prev_str}{suffix}</span>'
            )
        else:
            delta_html = ""
        rows += (
            f'<div class="trend-row">'
            f'<span class="trend-label">{_e(kpi["label"])}</span>'
            f"{gauge_html}"
            f'<span class="trend-val">{this_str}{suffix}</span>'
            f"{delta_html}"
            f"</div>"
        )
    if not rows:
        rows = '<div class="empty">Not enough data for trends</div>'

    # Source breakdown bars using gauge classes
    sources_html = ""
    if sources:
        total = sum(sources.values()) or 1
        sorted_sources = sorted(sources.items(), key=lambda x: -x[1])
        src_rows = ""
        for src, count in sorted_sources:
            pct = round(count / total * 100)
            bar_w = max(2, pct)
            src_rows += (
                f'<div class="gauge-row">'
                f'<span class="gauge-label">{_e(src)}</span>'
                f'<div class="gauge-track"><div class="gauge-fill" style="width:{bar_w}%;background:#58a6ff"></div></div>'
                f'<span class="gauge-pct">{pct}%</span>'
                f"</div>"
            )
        sources_html = f'<div style="margin-top:10px"><div style="font-size:11px;color:#484f58;margin-bottom:4px">Sources</div>{src_rows}</div>'

    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Trends">'
        f'<div class="card-header"><span class="card-title">Trends</span><span class="card-meta">week over week</span></div>'
        f'<div class="card-body">{rows}{sources_html}</div>'
        f"</div>"
    )


def _get_settings_data() -> dict:
    """Read all settings for dashboard display."""
    from keephive.settings import BUILTIN_SOUNDS, DEFAULTS, DESCRIPTIONS, read_settings

    return {
        "settings": read_settings(),
        "defaults": DEFAULTS,
        "descriptions": DESCRIPTIONS,
        "builtin_sounds": BUILTIN_SOUNDS,
        "backend": _get_backend_overview(),
        "platforms": _get_platform_overview(),
    }


def _get_backend_overview() -> dict:
    """Summarize backend selection, availability, and pending work."""
    preferred = get_setting("llm_backend") or "auto"
    env_override = os.environ.get("HIVE_LLM_BACKEND", "")
    state = get_backend_state()
    items: list[dict] = []
    for backend in available_backends():
        available, reason = backend.detect()
        items.append(
            {
                "name": backend.name,
                "available": available,
                "reason": reason,
                "supports_tools": backend.supports_tools,
                "supports_structured": backend.supports_structured,
                "supports_streaming": backend.supports_streaming,
            }
        )
    return {
        "preferred": preferred,
        "env_override": env_override,
        "state": state,
        "backends": items,
        "pending": pending_count(),
    }


def _collect_claude_hook_meta(settings_path: Path) -> list[dict]:
    if not settings_path.exists():
        return []
    try:
        data = json.loads(settings_path.read_text())
    except json.JSONDecodeError:
        return []

    hooks = data.get("hooks", {})
    meta: list[dict] = []

    def _iter_entries(entries: list) -> list[dict]:
        for entry in entries:
            if isinstance(entry, dict) and "hooks" in entry:
                yield from entry.get("hooks", [])
            elif isinstance(entry, dict):
                yield entry

    for event, entries in hooks.items():
        for item in _iter_entries(entries):
            command = item.get("command", "")
            if isinstance(command, str) and "keephive" in command:
                meta.append(
                    {
                        "name": f"{event}",
                        "path": str(settings_path),
                        "exists": True,
                        "command": command,
                    }
                )

    return meta


def _get_platform_overview() -> dict:
    """Return platform detection, install, and telemetry state."""
    specs = platform_specs()
    hook_manifest = read_hook_manifest()
    data: dict[str, dict] = {}
    for slug, spec in specs.items():
        hook_entry = hook_manifest.get(slug, {})
        scripts_meta: list[dict] = []
        script_hashes = hook_entry.get("scripts", {})

        if slug == "claude":
            scripts_meta = _collect_claude_hook_meta(spec.config_path)
        else:
            for name, expected_hash in script_hashes.items():
                target = spec.hooks_dir / name
                exists = target.exists()
                actual_hash = hashlib.sha256(target.read_bytes()).hexdigest() if exists else ""
                scripts_meta.append(
                    {
                        "name": name,
                        "path": str(target),
                        "exists": exists,
                        "expected_hash": expected_hash,
                        "actual_hash": actual_hash,
                    }
                )

        events = read_events(slug, limit=400)
        counts = Counter(e.get("event", "unknown") for e in events)
        recent = events[-6:]
        skill_record = get_skill_record(slug) or {}
        data[slug] = {
            "slug": slug,
            "title": spec.title,
            "detected": spec.detected,
            "detection_reason": spec.detection_reason,
            "skill_path": str(spec.skill_path),
            "skill_installed": spec.skill_path.exists(),
            "skill_hash": skill_record.get("hash", ""),
            "hooks_dir": str(spec.hooks_dir),
            "hook_config": hook_entry.get("config_path", str(spec.config_path)),
            "hook_scripts": scripts_meta,
            "telemetry": {
                "total": len(events),
                "by_event": dict(counts),
                "recent": recent,
            },
            "hook_count": len(scripts_meta),
        }
    return {"platforms": data}


def _get_brain_data() -> dict:
    """Agent brain view: knowledge corpus, pending work, telemetry snapshot."""
    from keephive.storage import (
        get_meaningful_entries,
        open_todos,
        read_memory,
        read_rules,
    )

    memory_text = read_memory()
    mem_lines = [ln.strip() for ln in memory_text.splitlines() if ln.strip()]
    rules_text = read_rules()
    rules_lines = [
        ln.strip() for ln in rules_text.splitlines() if ln.strip() and not ln.startswith("#")
    ]
    todos = open_todos()
    latest_entries = get_meaningful_entries()[:6]

    return {
        "memory": {"total": len(mem_lines), "preview": mem_lines[:12]},
        "rules": {"total": len(rules_lines), "preview": rules_lines[:8]},
        "todos": {
            "total": len(todos),
            "items": [
                {"date": d, "time": ts, "text": text} for d, ts, text in list(reversed(todos[-6:]))
            ],
        },
        "log": [{"text": line} for line in latest_entries],
        "backend": _get_backend_overview(),
        "platforms": _get_platform_overview(),
        "pending_llm": list_pending(limit=6),
    }


def _render_settings_panel(data: dict) -> str:
    """Render settings with grouped layout (preferences + backend + platforms)."""
    settings = data["settings"]
    defaults = data["defaults"]
    descriptions = data["descriptions"]
    builtin_sounds = data.get("builtin_sounds", [])

    rows: list[str] = []
    for key in sorted(defaults):
        val = settings.get(key, defaults[key])
        desc = _e(descriptions.get(key, ""))
        if isinstance(val, bool):
            checked = " checked" if val else ""
            control = (
                f'<label class="setting-toggle">'
                f'<input type="checkbox" class="setting-cb" data-key="{_e(key)}"{checked}>'
                f'<span class="slider"></span>'
                f"</label>"
            )
        elif key in ("sound_success", "sound_error"):
            options = []
            for s in builtin_sounds:
                sel = " selected" if s == val else ""
                options.append(f'<option value="{_e(s)}"{sel}>{_e(s)}</option>')
            opts_html = "\n".join(options)
            test_type = "error" if key == "sound_error" else ""
            control = (
                f'<select class="setting-select" data-key="{_e(key)}">'
                f"{opts_html}"
                f"</select>"
                f'<button class="sound-test-btn" data-sound-type="{test_type}" title="Test sound">&#9654;</button>'
            )
        else:
            control = f"<span>{_e(str(val))}</span>"
        rows.append(
            f'<div class="setting-row" tabindex="0">'
            f'<span class="setting-label">{_e(key)}</span>'
            f"{control}"
            f'<span class="setting-desc">{desc}</span>'
            f"</div>"
        )

    settings_rows_html = "".join(rows) if rows else '<div class="empty">No settings</div>'
    settings_card = (
        '<div class="card" tabindex="0" role="region" aria-label="Settings toggles">'
        '<div class="card-header"><span class="card-title">Preferences</span></div>'
        f'<div class="card-body">{settings_rows_html}</div>'
        "</div>"
    )

    backend = data.get("backend", {})
    backend_rows: list[str] = []
    for item in backend.get("backends", []):
        status_chip = "brain-chip-ok" if item.get("available") else "brain-chip-warn"
        backend_rows.append(
            '<div class="brain-line">'
            f'<span class="brain-chip {status_chip}">{_e(item.get("name", ""))}</span>'
            f'<span class="brain-detail">{_e(item.get("reason", ""))}</span>'
            "</div>"
        )
    backend_rows.append(
        f'<div class="brain-meta">Preferred: {_e(backend.get("preferred", "auto"))}'
        + (
            f" · Override: {_e(backend.get('env_override', ''))}"
            if backend.get("env_override")
            else ""
        )
        + "</div>"
    )
    pending = backend.get("pending", 0)
    if pending:
        backend_rows.append(
            f'<div class="brain-meta brain-meta-warn">{pending} queued LLM request(s)</div>'
        )
    backend_card = (
        '<div class="card" tabindex="0" role="region" aria-label="Backend status">'
        '<div class="card-header"><span class="card-title">LLM Backend</span></div>'
        f'<div class="card-body">{"".join(backend_rows)}</div>'
        "</div>"
    )

    platforms = data.get("platforms", {}).get("platforms", {})
    platform_rows = []
    for slug, info in platforms.items():
        status_chip = "brain-chip-ok" if info.get("detected") else "brain-chip-dim"
        hooks = info.get("hook_scripts", [])
        telemetry = info.get("telemetry", {})
        platform_rows.append(
            '<div class="platform-row">'
            f'<span class="brain-chip {status_chip}">{_e(slug)}</span>'
            f'<span class="platform-title">{_e(info.get("title", slug.title()))}</span>'
            f'<span class="platform-meta">skill: {"✓" if info.get("skill_installed") else "×"}</span>'
            f'<span class="platform-meta">hooks: {len(hooks)}</span>'
            f'<span class="platform-meta">events: {telemetry.get("total", 0)}</span>'
            "</div>"
        )
    platform_body = (
        "".join(platform_rows)
        if platform_rows
        else '<div class="empty">No platforms detected</div>'
    )
    platform_card = (
        '<div class="card" tabindex="0" role="region" aria-label="Platform integrations">'
        '<div class="card-header"><span class="card-title">Platform Integrations</span></div>'
        f'<div class="card-body">{platform_body}</div>'
        "</div>"
    )

    mascot_uri = _mascot_data_uri()
    mascot_html = (
        f'<div style="text-align:center;margin-bottom:1rem">'
        f'<img src="{mascot_uri}" alt="keephive mascot" '
        f'style="width:180px;image-rendering:auto">'
        f"</div>"
        if mascot_uri
        else ""
    )
    rows = [
        f'<div class="grid-row grid-cols-2">{settings_card}{backend_card}</div>',
        f'<div class="grid-row grid-cols-1">{platform_card}</div>',
    ]
    return f"{mascot_html}" + "".join(rows)


def _render_brain_panel(data: dict) -> str:
    backend = data.get("backend", {})
    platforms = data.get("platforms", {}).get("platforms", {})
    pending = data.get("pending_llm", [])
    memory = data.get("memory", {})
    rules = data.get("rules", {})
    todos = data.get("todos", {})
    log_entries = data.get("log", [])

    def _brain_card(
        title: str,
        body_html: str,
        *,
        meta: str | None = None,
        aria: str | None = None,
        accent: bool = False,
    ) -> str:
        classes = ["card", "brain-card"]
        if accent:
            classes.append("brain-card-emphasis")
        meta_html = f'<span class="card-meta">{_e(meta)}</span>' if meta else ""
        aria_label = _e(aria or title)
        return (
            f'<div class="{" ".join(classes)}" tabindex="0" role="region" aria-label="{aria_label}">'
            f'<div class="card-header"><span class="card-title">{_e(title)}</span>{meta_html}</div>'
            f'<div class="card-body">{body_html}</div>'
            "</div>"
        )

    def _list_lines(lines: list[str], empty_copy: str) -> str:
        if not lines:
            return f'<div class="brain-empty">{_e(empty_copy)}</div>'
        return "".join(
            f'<div class="brain-line"><span class="brain-text">{_e(line)}</span></div>'
            for line in lines
        )

    memory_card = _brain_card(
        "Memory",
        _list_lines(memory.get("preview", [])[:12], "No memory entries yet."),
        meta=f"{memory.get('total', 0)} lines",
        aria="Working memory summary",
    )

    rules_card = _brain_card(
        "Rules",
        _list_lines(rules.get("preview", [])[:8], "No rules captured yet."),
        meta=f"{rules.get('total', 0)} entries",
        aria="Behavior rules summary",
    )

    todo_rows: list[str] = []
    for item in todos.get("items", []):
        label = f"{item.get('date', '')} {item.get('time', '')}".strip()
        todo_rows.append(
            '<div class="brain-line">'
            f'<span class="brain-chip">{_e(label)}</span>'
            f'<span class="brain-text">{_e(item.get("text", ""))}</span>'
            "</div>"
        )
    todos_body = "".join(todo_rows) or '<div class="brain-empty">No open TODOs</div>'
    todos_card = _brain_card(
        "TODOs",
        todos_body,
        meta=f"{todos.get('total', 0)} open",
        aria="Active TODO list",
    )

    log_body = (
        "".join(
            f'<div class="brain-line"><span class="brain-text">{_e(entry.get("text", ""))}</span></div>'
            for entry in log_entries
        )
        or '<div class="brain-empty">No recent highlights</div>'
    )
    log_card = _brain_card("Recent Highlights", log_body, aria="Recent captured highlights")

    backend_rows: list[str] = []
    for item in backend.get("backends", []):
        available = item.get("available")
        chip_class = "brain-chip-ok" if available else "brain-chip-warn"
        suffix = " · tools" if item.get("supports_tools") else ""
        backend_rows.append(
            '<div class="brain-line">'
            f'<span class="brain-chip {chip_class}">{_e(item.get("name", ""))}</span>'
            f'<span class="brain-text">{_e(item.get("reason", "")) or "available"}{suffix}</span>'
            "</div>"
        )
    backend_rows.append(
        f'<div class="brain-meta">Preferred: {_e(backend.get("preferred", "auto"))}</div>'
    )
    env_override = backend.get("env_override")
    if env_override:
        backend_rows.append(f'<div class="brain-meta">Env override: {_e(env_override)}</div>')
    pending_count = backend.get("pending", 0)
    if pending_count:
        backend_rows.append(
            f'<div class="brain-meta brain-meta-warn">{pending_count} queued LLM task(s)</div>'
        )
    backend_card = _brain_card(
        "LLM Backend",
        "".join(backend_rows),
        accent=True,
        aria="LLM backend overview",
    )

    pending_rows = "".join(
        '<div class="brain-line">'
        f'<span class="brain-chip brain-chip-warn">{_e(item.get("model", ""))}</span>'
        f'<span class="brain-text">{_e(item.get("prompt_preview", "")[:160])}</span>'
        "</div>"
        for item in pending
    )
    if not pending_rows:
        pending_rows = '<div class="brain-empty">Queue clear</div>'
    else:
        pending_rows += (
            '<div class="brain-meta">Re-run the command once a backend is available.</div>'
        )
    pending_card = _brain_card("Queued LLM Tasks", pending_rows, aria="Pending LLM work items")

    summary_rows: list[str] = []
    for slug, info in sorted(platforms.items()):
        detected = info.get("detected")
        chip = "brain-chip-ok" if detected else "brain-chip-dim"
        telemetry = info.get("telemetry", {})
        total = telemetry.get("total", 0)
        summary_rows.append(
            '<div class="brain-line">'
            f'<span class="brain-chip {chip}">{_e(slug)}</span>'
            f'<span class="brain-text">{_e(info.get("title", slug.title()))}</span>'
            f'<span class="brain-detail">{total} event(s)</span>'
            "</div>"
        )
    if not summary_rows:
        summary_rows = [
            '<div class="brain-empty">No platform telemetry yet.</div>',
            _cmd_hints(["keephive setup --yes", "hive doctor"]),
        ]
    platform_summary_card = _brain_card(
        "Platform Integrations",
        "".join(summary_rows),
        aria="Platform integration summary",
    )

    platform_detail_cards: list[str] = []
    for slug, info in sorted(platforms.items()):
        detected = info.get("detected")
        chip = "brain-chip-ok" if detected else "brain-chip-dim"
        telemetry = info.get("telemetry", {})
        by_event = telemetry.get("by_event", {})
        recent_rows: list[str] = []
        if by_event:
            for event, count in sorted(by_event.items(), key=lambda x: (-x[1], x[0]))[:6]:
                recent_rows.append(
                    '<div class="brain-line">'
                    f'<span class="brain-chip">{_e(event)}</span>'
                    f'<span class="brain-detail">{count}</span>'
                    "</div>"
                )
        else:
            recent_rows.append('<div class="brain-empty">No telemetry yet.</div>')
            if detected:
                recent_rows.append(
                    '<div class="brain-hint">Open a session in this agent to start streaming telemetry.</div>'
                )
        recent_rows.append(
            f'<div class="brain-meta">Skill: {"✓" if info.get("skill_installed") else "×"} · Hooks: {len(info.get("hook_scripts", []))}</div>'
        )
        recent_rows.append(f'<div class="brain-meta">{_e(info.get("detection_reason", ""))}</div>')
        platform_detail_cards.append(
            _brain_card(
                info.get("title", slug.title()),
                "".join(recent_rows),
                meta=slug,
                aria=f"{slug} platform telemetry details",
            )
        )

    cards = [
        backend_card,
        memory_card,
        rules_card,
        todos_card,
        pending_card,
        log_card,
        platform_summary_card,
        *platform_detail_cards,
    ]
    if not cards:
        return ""
    rows: list[str] = []
    chunk = 3
    for idx in range(0, len(cards), chunk):
        row_cards = cards[idx : idx + chunk]
        cols = min(3, max(1, len(row_cards)))
        rows.append(f'<div class="grid-row grid-cols-{cols}">{"".join(row_cards)}</div>')
    return "".join(rows)


# ---- Pipeline health panels ----


def _get_pulse_data() -> dict:
    """Pulse hero panel: composite score + mini-KPIs."""
    from keephive.commands.stats import (
        _calculate_streak,
        _capture_mix,
        _knowledge_health,
        _productivity_pulse,
        _session_productivity,
    )
    from keephive.storage import read_stats

    health = _knowledge_health()
    capture = _capture_mix()
    sess = _session_productivity()
    stats = read_stats()
    pulse = _productivity_pulse(health, capture, sess, stats)
    days_data = stats.get("days", {})
    curr_streak, _ = _calculate_streak(days_data)

    return {
        "pulse": pulse,
        "health": health,
        "sess": sess,
        "streak": curr_streak,
    }


def _render_pulse_panel(data: dict) -> str:
    """Full-width hero: pulse score + 6 mini-KPIs using 3-tier number hierarchy."""
    pulse = data.get("pulse", {})
    health = data.get("health", {})
    sess = data.get("sess", {})
    streak = data.get("streak", 0)
    score = pulse.get("score", 0)
    delta = pulse.get("delta", 0)

    # Tier 1: Hero metric with delta
    if delta > 0:
        delta_html = f' <span class="metric-hero-delta up">&#9650;{delta}</span>'
    elif delta < 0:
        delta_html = f' <span class="metric-hero-delta down">&#9660;{abs(delta)}</span>'
    else:
        delta_html = ""

    # Tier 2: KPI row
    kpis = [
        (
            f"{health.get('fresh_pct', 0):.0f}%",
            "fresh",
            "Memory freshness: % of facts under 14 days old",
        ),
        (
            f"{health.get('capture_recall_ratio', 0):.0f}%",
            "recall",
            "Capture-recall ratio: recalls vs. captures",
        ),
        (
            f"{health.get('fact_survival_rate', 0):.0f}%",
            "survival",
            "Fact survival rate: % not corrected or removed",
        ),
        (
            f"{sess.get('avg_prompts_per_convo', 0):.0f}",
            "prompts/convo",
            "Average prompts per conversation (30d)",
        ),
        (f"{sess.get('convos_week', 0)}", "conversations", "Total conversations this week"),
        (f"{streak}d", "day streak", "Consecutive days with at least one hive command"),
    ]

    kpi_html = ""
    for val, label, tooltip in kpis:
        kpi_html += (
            f'<div class="kpi-item" title="{_e(tooltip)}">'
            f'<div class="kpi-value">{val}</div>'
            f'<div class="kpi-label">{label}</div>'
            f"</div>"
        )

    # Component breakdown (expandable, uses gauge-row classes)
    components = pulse.get("components", {})
    comp_defs = [
        ("freshness", "Freshness", "#3fb950", "Memory freshness (25% weight)"),
        ("recall", "Recall", "#58a6ff", "Capture-recall ratio (20% weight)"),
        ("depth", "Depth", "#a371f7", "Session depth quality (20% weight)"),
        ("todo_rate", "TODO", "#e3b341", "TODO completion rate (15% weight)"),
        ("verify", "Verify", "#d2a8ff", "Verification cadence (10% weight)"),
        ("streak", "Streak", "#39d353", "Usage streak (10% weight)"),
    ]
    comp_bars = ""
    for key, label, color, tooltip in comp_defs:
        pct = components.get(key, 0)
        clamped = max(0, min(100, pct))
        comp_bars += (
            f'<div class="gauge-row" title="{_e(tooltip)}">'
            f'<span class="gauge-label" style="width:65px">{label}</span>'
            f'<div class="gauge-track">'
            f'<div class="gauge-fill" style="width:{clamped}%;background:{color}"></div></div>'
            f'<span class="gauge-pct">{pct}%</span>'
            f"</div>"
        )
    comp_details = (
        f'<details style="margin-top:10px;text-align:left">'
        f'<summary style="font-size:11px;color:#484f58;cursor:pointer;text-align:center;list-style:none">'
        f'<span style="border-bottom:1px dotted #484f58">Component Breakdown</span></summary>'
        f'<div style="margin-top:8px;padding:0 16px">{comp_bars}</div>'
        f"</details>"
    )

    return (
        f'<div class="card pulse-hero" tabindex="0" role="region" aria-label="Pulse">'
        f'<div class="card-body" style="text-align:center;padding:16px 24px">'
        f'<div class="kpi-label" style="letter-spacing:1px">Productivity Pulse</div>'
        f'<div style="margin:4px 0"><span class="metric-hero">{score}</span>'
        f'<span class="metric-hero-unit">/100</span>{delta_html}</div>'
        f'<div class="kpi-row" style="margin-top:8px">{kpi_html}</div>'
        f"{comp_details}"
        f"</div></div>"
    )


def _get_pipeline_data() -> dict:
    """Pipeline health: freshness gauge, capture-recall, survival, pulse, capture mix, recalled."""
    from keephive.commands.stats import (
        _calculate_streak,
        _capture_mix,
        _knowledge_health,
        _most_recalled,
        _productivity_pulse,
        _session_productivity,
    )
    from keephive.storage import read_stats

    health = _knowledge_health()
    capture = _capture_mix()
    sess = _session_productivity()
    stats = read_stats()
    pulse = _productivity_pulse(health, capture, sess, stats)
    days_data = stats.get("days", {})
    curr_streak, _ = _calculate_streak(days_data)
    recalled = _most_recalled()

    # Pipeline action statuses: last run dates + context for key commands
    actions: list[dict] = []
    days_data = stats.get("days", {})
    pipeline_cmds = {
        "verify": {
            "label": "verify",
            "hint": "hive verify",
            "desc": "check facts against codebase",
            "aliases": {"v", "verify"},
        },
        "reflect": {
            "label": "reflect",
            "hint": "hive reflect",
            "desc": "find patterns in logs",
            "aliases": {"rf", "reflect"},
        },
        "audit": {
            "label": "audit",
            "hint": "hive audit",
            "desc": "quality analysis",
            "aliases": {"a", "audit"},
        },
    }
    for cmd, meta in pipeline_cmds.items():
        aliases = meta.get("aliases", {cmd})
        last_date = None
        for day in sorted(days_data.keys(), reverse=True):
            day_cmds = days_data[day].get("commands", {})
            if any(a in day_cmds for a in aliases):
                last_date = day
                break
        ctx = ""
        if cmd == "verify":
            ctx = f"{health.get('total_facts', 0)} facts to check"
        elif cmd == "reflect":
            # Count recent daily logs
            from keephive.storage import daily_dir

            dd = daily_dir()
            log_count = len(list(dd.glob("*.md"))) if dd.exists() else 0
            if log_count > 0:
                ctx = f"{log_count} daily logs"
        actions.append(
            {
                "cmd": cmd,
                "label": meta["label"],
                "hint": meta["hint"],
                "desc": meta["desc"],
                "last_date": last_date,
            }
            | ({"ctx": ctx} if ctx else {})
        )

    return {
        **health,
        "pulse": pulse,
        "capture": capture,
        "streak": curr_streak,
        "recalled": recalled,
        "actions": actions,
    }


def _render_pipeline_panel(data: dict) -> str:
    """Dense pipeline panel: pulse score, freshness gauge, metrics, capture pills."""
    total = data.get("total_facts", 0)
    fresh = data.get("fresh", 0)
    aging = data.get("aging", 0)
    stale = data.get("stale", 0)
    fresh_pct = data.get("fresh_pct", 0)
    corrected_wk = data.get("corrected_this_week", 0)
    capture_recall = data.get("capture_recall_ratio", 0)
    survival = data.get("fact_survival_rate", 0)
    pulse = data.get("pulse", {})
    capture = data.get("capture", {})

    if total == 0:
        return (
            '<div class="card" tabindex="0" role="region" aria-label="Pipeline Health">'
            '<div class="card-header"><span class="card-title">Pipeline Health</span></div>'
            '<div class="card-body"><div class="empty">No verified facts yet</div></div>'
            "</div>"
        )

    # Header detail
    detail_parts: list[str] = [f"{fresh} fresh"]
    if aging > 0:
        detail_parts.append(f"{aging} aging")
    if stale > 0:
        detail_parts.append(f'<span style="color:#f85149">{stale} stale</span>')
    detail_str = ", ".join(detail_parts)

    # Tier 1: Hero metric (pulse score)
    pulse_score = pulse.get("score", 0)
    pulse_delta = pulse.get("delta", 0)
    if pulse_delta > 0:
        p_delta_html = f' <span class="metric-hero-delta up">&#9650;{pulse_delta}</span>'
    elif pulse_delta < 0:
        p_delta_html = f' <span class="metric-hero-delta down">&#9660;{abs(pulse_delta)}</span>'
    else:
        p_delta_html = ""
    hero_html = (
        f'<div style="text-align:center;padding:8px 0 4px">'
        f'<div class="kpi-label" style="letter-spacing:1px;margin-bottom:2px">PULSE</div>'
        f'<span class="metric-hero">{pulse_score}</span>'
        f'<span class="metric-hero-unit">/100</span>{p_delta_html}'
        f"</div>"
    )

    # Tier 2: KPI row
    kpi_items = (
        f'<div class="kpi-row" style="margin-bottom:8px">'
        f'<div class="kpi-item"><div class="kpi-value">{fresh_pct:.0f}%</div><div class="kpi-label">fresh</div></div>'
        f'<div class="kpi-item"><div class="kpi-value">{capture_recall:.0f}%</div><div class="kpi-label">recall</div></div>'
        f'<div class="kpi-item"><div class="kpi-value">{survival:.0f}%</div><div class="kpi-label">survival</div></div>'
        f'<div class="kpi-item"><div class="kpi-value">{data.get("streak", 0)}d</div><div class="kpi-label">streak</div></div>'
        f"</div>"
    )

    # Knowledge health gauges (transparent component bars, not opaque composite)
    components = pulse.get("components", {})

    def _gauge_bar(label: str, pct: float, color: str) -> str:
        """Single labeled gauge bar using gauge-row CSS classes."""
        clamped = max(0, min(100, pct))
        return (
            f'<div class="gauge-row">'
            f'<span class="gauge-label">{label}</span>'
            f'<div class="gauge-track">'
            f'<div class="gauge-fill" style="width:{clamped:.0f}%;background:{color}"></div></div>'
            f'<span class="gauge-pct">{pct:.0f}%</span>'
            f"</div>"
        )

    # Build gauges for each transparent metric
    gauges_html = '<div style="margin-bottom:8px">'
    gauges_html += _gauge_bar("memory freshness", fresh_pct, "#3fb950")
    gauges_html += _gauge_bar("recall rate", capture_recall, "#58a6ff")
    gauges_html += _gauge_bar("fact survival", survival, "#a371f7")
    # TODO rate and streak from pulse components
    todo_rate = components.get("todo_rate", 0)
    streak_score = components.get("streak", 0)
    if todo_rate > 0:
        gauges_html += _gauge_bar("todo completion", todo_rate, "#e3b341")
    if streak_score > 0:
        gauges_html += _gauge_bar("usage streak", streak_score, "#39d353")
    if corrected_wk > 0:
        gauges_html += (
            f'<div style="font-size:10px;color:#484f58;text-align:right;margin-top:2px">'
            f"{corrected_wk} corrected this week</div>"
        )
    gauges_html += "</div>"

    # Capture mix pills (compact)
    capture_counts = capture.get("counts", {})
    cat_colors = {
        "FACT": "#3fb950",
        "DECISION": "#a371f7",
        "INSIGHT": "#58a6ff",
        "TODO": "#e3b341",
        "DONE": "#484f58",
    }
    pills_html = ""
    cap_total = capture.get("total", 0)
    if cap_total > 0:
        pills = ""
        for cat_name in ("FACT", "DECISION", "INSIGHT", "TODO", "DONE"):
            c = capture_counts.get(cat_name, 0)
            if c > 0:
                color = cat_colors.get(cat_name, "#484f58")
                pills += (
                    f'<span style="display:inline-block;padding:1px 6px;border-radius:8px;'
                    f"font-size:10px;margin-right:4px;background:{color}22;color:{color};border:1px solid {color}33"
                    f'">{c} {cat_name.lower()}</span>'
                )
        pills_html = f'<div style="margin-top:4px">{pills}</div>'

    # Pipeline action statuses (actionable callouts)
    from datetime import date

    actions = data.get("actions", [])
    actions_html = ""
    if actions:
        from keephive.clock import get_today

        today = get_today().isoformat()
        action_items = ""
        for act in actions:
            last = act.get("last_date")
            hint = _e(act.get("hint", ""))
            _ = act.get("desc", "")
            ctx = act.get("ctx", "")
            if last is None:
                dot_cls = "never"
                ago_text = "never"
            else:
                try:
                    days_ago = (date.fromisoformat(today) - date.fromisoformat(last)).days
                    if days_ago <= 1:
                        dot_cls = "recent"
                        ago_text = "today" if days_ago == 0 else "1d ago"
                    elif days_ago <= 7:
                        dot_cls = "recent"
                        ago_text = f"{days_ago}d ago"
                    else:
                        dot_cls = "overdue"
                        ago_text = f"{days_ago}d ago"
                except ValueError:
                    dot_cls = "never"
                    ago_text = _e(last) if last else "?"
            ctx_span = f' <span class="action-ago">&middot; {_e(ctx)}</span>' if ctx else ""
            action_items += (
                f'<div class="action-item">'
                f'<span class="action-dot {dot_cls}"></span>'
                f'<span class="action-label">{hint}</span>'
                f'<span class="action-ago">{ago_text}</span>'
                f"{ctx_span}"
                f"</div>"
            )
        actions_html = (
            f'<div class="action-timeline" style="padding-top:8px;border-top:1px solid #21262d">'
            f"{action_items}</div>"
        )

    # Most recalled facts (compact, fills vertical space)
    recalled = data.get("recalled", [])
    recalled = [
        r
        for r in recalled
        if r.get("text", "").strip() and "fact removed" not in r.get("text", "").lower()
    ]
    recalled_html = ""
    if recalled:
        items = ""
        for r in recalled[:3]:
            text = _e(r.get("text", ""))
            if len(text) > 55:
                text = text[:52] + "..."
            count = r.get("count", 0)
            items += (
                f'<div style="display:flex;justify-content:space-between;align-items:center;'
                f'padding:2px 0;font-size:10px">'
                f'<span style="color:#8b949e;flex:1;overflow:hidden;text-overflow:ellipsis;'
                f'white-space:nowrap">{text}</span>'
                f'<span style="color:#58a6ff;margin-left:8px;font-weight:600">{count}&times;</span>'
                f"</div>"
            )
        recalled_html = (
            f'<div style="margin-top:6px;padding-top:6px;border-top:1px solid #21262d">'
            f'<div style="font-size:9px;color:#484f58;text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px">Recalled</div>'
            f"{items}</div>"
        )

    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Pipeline Health">'
        f'<div class="card-header"><span class="card-title">Pipeline Health</span>'
        f'<span class="card-meta">{total} facts ({detail_str})</span></div>'
        f'<div class="card-body">{hero_html}{kpi_items}'
        f'<details style="margin-top:4px"><summary style="font-size:11px;color:#484f58;cursor:pointer;list-style:none">'
        f'<span style="border-bottom:1px dotted #484f58">Gauges</span></summary>'
        f'<div style="margin-top:6px">{gauges_html}</div></details>'
        f"{pills_html}{actions_html}{recalled_html}</div>"
        f"</div>"
    )


def _get_capture_data() -> dict:
    """Capture mix: category breakdown + sparkline + consistency."""
    from keephive.commands.stats import _capture_mix

    return _capture_mix()


def _render_capture_panel(data: dict) -> str:
    """7-day capture sparkline + category breakdown."""
    counts = data.get("counts", {})
    total = data.get("total", 0)
    consistency = data.get("consistency", 0)
    sparkline = data.get("sparkline_str", "")

    if total == 0:
        return (
            '<div class="card" tabindex="0" role="region" aria-label="Capture Mix">'
            '<div class="card-header"><span class="card-title">Capture Mix</span></div>'
            '<div class="card-body"><div class="empty">No entries in last 7 days</div></div>'
            "</div>"
        )

    # Color-coded category pills
    cat_colors = {
        "FACT": "#3fb950",
        "DECISION": "#a371f7",
        "INSIGHT": "#58a6ff",
        "TODO": "#e3b341",
        "DONE": "#484f58",
        "CORRECTION": "#f85149",
    }

    pills_html = ""
    for cat_name in ("FACT", "DECISION", "INSIGHT", "TODO", "DONE"):
        c = counts.get(cat_name, 0)
        if c > 0:
            color = cat_colors.get(cat_name, "#484f58")
            pills_html += (
                f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
                f"font-size:11px;margin:2px;background:{color}22;color:{color};border:1px solid {color}44"
                f'">{c} {cat_name.lower()}</span>'
            )

    consistency_html = ""
    if consistency > 0:
        consistency_html = (
            f'<div style="font-size:11px;color:#484f58;margin-top:6px">'
            f"consistency: {consistency}%</div>"
        )

    sparkline_html = ""
    if sparkline.strip():
        sparkline_html = (
            f'<div style="font-family:monospace;font-size:16px;letter-spacing:2px;margin-bottom:6px">'
            f"{_e(sparkline)}</div>"
        )

    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Capture Mix">'
        f'<div class="card-header"><span class="card-title">Capture Mix</span>'
        f'<span class="card-meta">last 7 days</span></div>'
        f'<div class="card-body">{sparkline_html}<div>{pills_html}</div>{consistency_html}</div>'
        f"</div>"
    )


def _get_recalled_data() -> dict:
    """Most-recalled facts."""
    from keephive.commands.stats import _most_recalled

    return {"recalled": _most_recalled()}


def _render_recalled_panel(data: dict) -> str:
    """Top recalled facts with counts. Compact list."""
    recalled = data.get("recalled", [])

    # Filter out removed/empty facts
    recalled = [
        r
        for r in recalled
        if r.get("text", "").strip() and "fact removed" not in r.get("text", "").lower()
    ]

    if not recalled:
        return (
            '<div class="card" tabindex="0" role="region" aria-label="Most Recalled">'
            '<div class="card-header"><span class="card-title">Most Recalled</span></div>'
            '<div class="card-body"><div class="empty">No recall data yet</div></div>'
            "</div>"
        )

    items_html = ""
    for r in recalled:
        text = _e(r.get("text", ""))
        # Trim long fact text for display
        if len(text) > 80:
            text = text[:77] + "..."
        count = r.get("count", 0)
        items_html += (
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:4px 0;border-bottom:1px solid #21262d;font-size:12px">'
            f'<span style="color:#c9d1d9;flex:1;overflow:hidden;text-overflow:ellipsis;'
            f'white-space:nowrap">{text}</span>'
            f'<span style="color:#58a6ff;margin-left:8px;white-space:nowrap;font-weight:600">{count}&times;</span>'
            f"</div>"
        )

    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Most Recalled">'
        f'<div class="card-header"><span class="card-title">Most Recalled</span></div>'
        f'<div class="card-body">{items_html}</div>'
        f"</div>"
    )


# ---- Profiles + Transfer panels ----


def _get_profiles_data() -> dict:
    """Get profile list and active profile."""
    from keephive.storage import active_profile, list_profiles

    return {
        "profiles": list_profiles(),
        "active": active_profile() or "default",
    }


def _render_profiles_panel(data: dict) -> str:
    """Render profile management card."""
    profiles = data.get("profiles", [])

    rows = ""
    for p in profiles:
        name = _e(p["name"])
        is_active = p.get("active", False)
        if is_active:
            rows += (
                f'<div class="kpi-row" style="justify-content:flex-start;gap:12px;padding:6px 0">'
                f'<span style="color:#3fb950;font-size:14px">&#9679;</span>'
                f'<span class="kpi-value" style="font-size:14px">{name}</span>'
                f'<span class="kpi-label" style="margin-top:0;color:#3fb950">active</span>'
                f"</div>"
            )
        else:
            rows += (
                f'<div class="kpi-row" style="justify-content:flex-start;gap:12px;padding:6px 0">'
                f'<span style="color:#484f58;font-size:14px">&#9675;</span>'
                f'<span style="font-size:14px;color:#e6edf3">{name}</span>'
                f'<button class="todo-done-btn" onclick="profileSwitch(\'{name}\')">switch</button>'
            )
            if name != "default":
                rows += f'<button class="todo-done-btn" style="color:#f85149" onclick="profileDelete(\'{name}\')">&#10005;</button>'
            rows += "</div>"

    # Create form
    create_form = (
        '<div style="margin-top:12px;display:flex;gap:6px;align-items:center">'
        '<input type="text" id="profile-name" placeholder="new profile name" '
        'style="background:#0d1117;border:1px solid #30363d;color:#e6edf3;padding:4px 8px;border-radius:4px;flex:1;font-size:12px">'
        '<label style="font-size:11px;color:#8b949e;display:flex;align-items:center;gap:4px">'
        '<input type="checkbox" id="profile-seed"> seed</label>'
        '<button class="todo-done-btn" onclick="profileCreate()">Create</button>'
        "</div>"
    )

    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Profiles">'
        f'<div class="card-header"><span class="card-title">Profiles</span>'
        f'<span class="card-meta">{len(profiles)} profiles</span></div>'
        f'<div class="card-body">{rows}{create_form}</div>'
        f"</div>"
    )


def _get_transfer_data() -> dict:
    """Get transfer data (just active profile label)."""
    from keephive.storage import active_profile_label

    return {"profile": active_profile_label()}


def _render_transfer_panel(data: dict) -> str:
    """Render data transfer card (export/import)."""
    profile = _e(data.get("profile", "default"))

    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Data Transfer">'
        f'<div class="card-header"><span class="card-title">Data Transfer</span></div>'
        f'<div class="card-body">'
        f'<div style="margin-bottom:12px">'
        f'<div style="font-size:11px;color:#8b949e;margin-bottom:6px">Export current profile as archive</div>'
        f'<button class="todo-done-btn" onclick="transferExport()">Export &ldquo;{profile}&rdquo; &#8595;</button>'
        f"</div>"
        f"<div>"
        f'<div style="font-size:11px;color:#8b949e;margin-bottom:6px">Import from archive file</div>'
        f'<input type="file" id="import-file" accept=".tar.gz,.tgz" '
        f'style="font-size:11px;color:#8b949e;background:none;border:none">'
        f'<button class="todo-done-btn" onclick="transferImport()" style="margin-left:6px">Import</button>'
        f"</div>"
        f"</div>"
        f"</div>"
    )


# ---- Panel registry ----

PANELS: dict[str, tuple] = {
    "status": (_get_status_data, _render_status_panel),
    "status-brief": (_get_status_data, _render_status_brief_panel),
    "log": (_get_log_data, _render_log_panel),
    "log-brief": (_get_log_data, _render_log_brief_panel),
    "log-home": (_get_log_data, _render_log_home_panel),
    "todos": (_get_todo_data, _render_todo_panel),
    "todos-brief": (_get_todo_data, _render_todo_brief_panel),
    "recurring": (_get_todo_data, _render_recurring_panel),
    "knowledge": (_get_knowledge_data, _render_knowledge_panel),
    "knowledge-limited": (_get_knowledge_data, _render_knowledge_limited_panel),
    "knowledge-compact": (_get_knowledge_data, _render_knowledge_compact_panel),
    "soul": (_get_soul_data, _render_soul_panel),
    "memory": (_get_memory_data, _render_memory_panel),
    "notes": (_get_notes_data, _render_notes_panel),
    "notes-compact": (_get_notes_data, _render_notes_compact_panel),
    "stats": (_get_stats_data, _render_stats_panel),
    "stats-commands": (_get_stats_data, _render_stats_commands_panel),
    "brain": (_get_brain_data, _render_brain_panel),
    "stats-platforms": (_get_platform_overview, _render_stats_platforms_panel),
    "ps": (_get_ps_data, _render_ps_panel),
    "facts": (_get_recent_facts_data, _render_recent_facts_panel),
    "standup": (_get_standup_data, _render_standup_panel),
    "stats-summary": (_get_stats_summary_data, _render_stats_summary_panel),
    "knowledge-tabbed": (_get_knowledge_all_data, _render_knowledge_tabbed_panel),
    "sessions": (_get_session_data, _render_sessions_panel),
    "stats-trends": (_get_trend_data, _render_trends_panel),
    "settings": (_get_settings_data, _render_settings_panel),
    "stats-pulse": (_get_pulse_data, _render_pulse_panel),
    "stats-pipeline": (_get_pipeline_data, _render_pipeline_panel),
    "stats-capture": (_get_capture_data, _render_capture_panel),
    "stats-recalled": (_get_recalled_data, _render_recalled_panel),
    "profiles": (_get_profiles_data, _render_profiles_panel),
    "transfer": (_get_transfer_data, _render_transfer_panel),
}

# ---- View definitions ----

VIEWS: dict[str, dict] = {
    "home": {
        "path": "/",
        "title": "Home",
        "rows": [
            ["status"],
            ["log-home", "todos"],
            ["ps", "recurring"],
            ["standup"],
        ],
    },
    "dev": {
        "path": "/dev",
        "title": "Dev",
        "rows": [
            ["soul", "memory"],
            ["status-brief"],
            ["todos-brief", "log-brief"],
            ["facts"],
            ["knowledge-compact"],
        ],
    },
    "brain": {
        "path": "/brain",
        "title": "Agent Brain",
        "rows": [["brain"]],
    },
    "know": {
        "path": "/know",
        "title": "Knowledge",
        "rows": [["knowledge-tabbed"]],
    },
    "stats": {
        "path": "/stats",
        "title": "Stats",
        "cols": [
            ["stats-pipeline", "sessions"],  # Left column: Pipeline Health → Sessions
            ["stats", "stats-commands"],  # Right column: Activity → What You Use
        ],
        "rows": [
            ["stats-platforms"],
            ["stats-trends"],  # Full-width row below both columns
        ],
    },
    "settings": {
        "path": "/settings",
        "title": "Settings",
        "rows": [["settings", "profiles"], ["transfer"]],
    },
}

# Redirect old paths to new equivalents
_REDIRECTS: dict[str, str] = {
    "/daily": "/",
    "/simple": "/dev",
    "/mem": "/know",
    "/notes": "/know",
}

_PATH_TO_VIEW: dict[str, str] = {v["path"]: k for k, v in VIEWS.items()}


# ---- Fragment + page rendering ----


def _render_panel_safe(name: str, extra_params: dict | None = None) -> str:
    if name not in PANELS:
        return f'<div class="card"><div class="card-body"><div class="empty">Unknown panel: {_e(name)}</div></div></div>'
    data_fn, render_fn = PANELS[name]
    try:
        # Log panels accept an optional date param
        if (
            name in ("log", "log-brief", "log-home")
            and extra_params
            and extra_params.get("log_date")
        ):
            data = data_fn(extra_params["log_date"])
        else:
            data = data_fn()
        return render_fn(data)
    except Exception as exc:
        return (
            f'<div class="card"><div class="card-body">'
            f'<div class="empty">Error in {_e(name)}: {_e(str(exc))}</div>'
            f"</div></div>"
        )


def render_fragment(view_name: str, extra_params: dict | None = None) -> str:
    view_def = VIEWS.get(view_name)
    if not view_def:
        return '<div class="empty">Unknown view</div>'
    parts = []

    # Column-based layout (puzzle pieces — independent columns, no height matching)
    if "cols" in view_def:
        col_htmls = []
        for col_panels in view_def["cols"]:
            col_items = [
                f'<div class="grid-panel" data-panel-id="{_e(name)}">{_render_panel_safe(name, extra_params)}</div>'
                for name in col_panels
            ]
            col_htmls.append(f'<div class="layout-col">{"".join(col_items)}</div>')
        parts.append(f'<div class="layout-cols">{"".join(col_htmls)}</div>')

    # Row-based layout (existing behavior)
    for row in view_def.get("rows", []):
        row_panels = []
        for name in row:
            panel_html = _render_panel_safe(name, extra_params)
            row_panels.append(
                f'<div class="grid-panel" data-panel-id="{_e(name)}">{panel_html}</div>'
            )
        cols = len(row)
        parts.append(f'<div class="grid-row grid-cols-{cols}">{"".join(row_panels)}</div>')
    return "\n".join(parts)


def render_page(view_name: str, port: int) -> str:
    nav_tabs = ""
    for vname, vdef in VIEWS.items():
        active_cls = " active" if vname == view_name else ""
        nav_tabs += (
            f'<a class="nav-tab{active_cls}" href="{_e(vdef["path"])}">{_e(vdef["title"])}</a>'
        )

    bee_uri = _keepbee_data_uri()
    if bee_uri:
        brand_html = f'<a class="nav-brand" href="/"><img src="{bee_uri}" alt="hive" class="nav-logo">hive</a>'
    else:
        brand_html = '<a class="nav-brand" href="/">hive</a>'

    from keephive.storage import active_profile

    prof = active_profile()
    if prof:
        brand_html += f' <span class="profile-badge">{_e(prof)}</span>'

    content = render_fragment(view_name)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="{_get_favicon()}">
<title>hive \u2014 {_e(VIEWS.get(view_name, {}).get("title", view_name))}</title>
<style>{_CSS}</style>
</head>
<body data-view="{_e(view_name)}" data-port="{port}">
<nav role="navigation" aria-label="Dashboard views">
  {brand_html}
  {nav_tabs}
  <div class="nav-right">
    <input id="search-input" type="text" placeholder="search memory\u2026" autocomplete="off" role="searchbox" aria-label="Search memory">
    <span class="refresh-label">refresh</span>
    <select class="refresh-select" id="refresh-select">
      <option value="5">5s</option>
      <option value="10" selected>10s</option>
      <option value="30">30s</option>
      <option value="60">60s</option>
      <option value="0">off</option>
    </select>
    <span id="refresh-ts">just now</span>
  </div>
</nav>
<main>
  <div id="main-content" aria-live="polite">{content}</div>
</main>
<div id="search-overlay" role="dialog" aria-modal="true" aria-label="Search results">
  <div class="search-panel">
    <div class="search-header">
      <span class="search-title">Search Results</span>
      <button id="search-close" class="search-close" aria-label="Close search">&#10005;</button>
    </div>
    <div id="search-body" class="search-body"></div>
  </div>
</div>
<div id="help-overlay" aria-label="Keyboard shortcuts">
  <div class="help-panel">
    <h2>Keyboard Shortcuts</h2>
    <h3>Navigation</h3>
    <div class="help-keys">
      <span class="help-key">j / k</span><span class="help-desc">Next / previous card (or item inside card)</span>
      <span class="help-key">h / l</span><span class="help-desc">Left / right card (exits inner mode)</span>
      <span class="help-key">Tab</span><span class="help-desc">Next card (Shift+Tab = previous)</span>
      <span class="help-key">J / K</span><span class="help-desc">Half-page scroll down / up</span>
      <span class="help-key">gg</span><span class="help-desc">Focus first card</span>
      <span class="help-key">G</span><span class="help-desc">Focus last card</span>
    </div>
    <h3>Views</h3>
    <div class="help-keys">
      <span class="help-key">gh</span><span class="help-desc">Home (/)</span>
      <span class="help-key">gd</span><span class="help-desc">Dev (/dev)</span>
      <span class="help-key">gk</span><span class="help-desc">Knowledge (/know)</span>
      <span class="help-key">gs</span><span class="help-desc">Stats (/stats)</span>
      <span class="help-key">gc</span><span class="help-desc">Settings (/settings)</span>
    </div>
    <h3>Actions</h3>
    <div class="help-keys">
      <span class="help-key">Enter / o</span><span class="help-desc">Dive into card items (or toggle inside)</span>
      <span class="help-key">x</span><span class="help-desc">Collapse focused accordion</span>
      <span class="help-key">d</span><span class="help-desc">Mark focused TODO done</span>
      <span class="help-key">e</span><span class="help-desc">Edit focused guide / memory</span>
      <span class="help-key">/</span><span class="help-desc">Focus search</span>
      <span class="help-key">i</span><span class="help-desc">Focus first input</span>
      <span class="help-key">r</span><span class="help-desc">Refresh now</span>
      <span class="help-key">[ / ]</span><span class="help-desc">Previous / next log date</span>
      <span class="help-key">1-9</span><span class="help-desc">Switch note slot (or Know tab)</span>
      <span class="help-key">n / N</span><span class="help-desc">Next / prev search result</span>
      <span class="help-key">?</span><span class="help-desc">Toggle this help</span>
      <span class="help-key">Esc</span><span class="help-desc">Exit inner mode / clear focus</span>
    </div>
    <h3>Tridactyl</h3>
    <p style="font-size:12px;color:#8b949e;margin-top:4px">
      Add to tridactylrc: <code style="background:#21262d;padding:1px 5px;border-radius:3px;color:#ffa657">autocmd DocStart http://localhost:3847 mode ignore</code><br>
      <span style="color:#6e7681">Shift+Escape re-enters tridactyl normal mode.</span>
    </p>
  </div>
</div>
<div id="edit-overlay">
  <div class="edit-modal">
    <div class="edit-toolbar">
      <span id="edit-title" class="edit-toolbar-title">Edit</span>
      <button class="edit-btn" onclick="closeEdit()">Cancel</button>
      <button class="edit-btn edit-btn-save" onclick="saveEdit()">Save (Ctrl+Enter)</button>
    </div>
    <div class="edit-panes">
      <textarea id="edit-textarea" spellcheck="false"></textarea>
      <div id="edit-preview-body" class="edit-preview md"></div>
    </div>
  </div>
</div>
<div id="g-prefix">g...</div>
<script>{_JS}</script>
</body>
</html>"""


# ---- HTTP handler ----


class _HiveHandler(BaseHTTPRequestHandler):
    server_port: int = DEFAULT_PORT
    project_name: str = ""

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/favicon.ico":
            # Browsers request this even with the inline SVG data URI
            self.send_response(204)
            self.end_headers()
            return

        if path == "/api/fragment":
            qs = parse_qs(parsed.query)
            view_name = qs.get("view", ["home"])[0]
            log_date = qs.get("log_date", [None])[0] or qs.get("date", [None])[0]

            # Special case: view=log returns just the log panel (for date navigation)
            if view_name == "log":
                data = _get_log_data(log_date)
                limit_str = qs.get("limit", ["25"])[0]
                try:
                    log_limit = int(limit_str)
                except ValueError:
                    log_limit = 25
                body = _render_log_panel(data, limit=log_limit, show_nav=True).encode()
            else:
                if view_name not in VIEWS:
                    view_name = "home"
                extra_params: dict = {}
                if log_date:
                    extra_params["log_date"] = log_date
                body = render_fragment(view_name, extra_params or None).encode()

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._cors()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/search":
            qs = parse_qs(parsed.query)
            query = (qs.get("q", [""])[0] or "").strip()
            results: list[dict] = []
            if query:
                try:
                    from keephive.commands.remember import (
                        _daily_path_for_result,
                        _get_context_lines,
                        _search_all_tiers,
                    )

                    results = _search_all_tiers(query)[:30]
                    # Add context lines for results that have a file path
                    for r in results:
                        fp = r.get("file")
                        if fp:
                            from pathlib import Path as _P

                            prev, nxt = _get_context_lines(_P(fp), r.get("line", ""))
                            if prev:
                                r["prev_line"] = prev
                            if nxt:
                                r["next_line"] = nxt
                        elif r.get("tier") in ("daily", "archive"):
                            dp = _daily_path_for_result(r)
                            if dp:
                                prev, nxt = _get_context_lines(dp, r.get("line", ""))
                                if prev:
                                    r["prev_line"] = prev
                                if nxt:
                                    r["next_line"] = nxt
                except Exception:
                    pass
            # Filter out session/compaction lines
            _session_pat = re.compile(r"\bsession\b|\bcompact", re.I)
            results = [r for r in results if not _session_pat.search(r.get("line", ""))]
            # Strip the raw log timestamp prefix `- [HH:MM:SS] ` from displayed text
            _prefix_pat = re.compile(r"^- \[\d{2}:\d{2}:\d{2}\]\s*")
            for r in results:
                r["line"] = _prefix_pat.sub("", r.get("line", ""))
            resp_data = json.dumps({"results": results}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors()
            self.send_header("Content-Length", str(len(resp_data)))
            self.end_headers()
            self.wfile.write(resp_data)
            return

        if path == "/api/content":
            qs = parse_qs(parsed.query)
            content_type = (qs.get("type", [""])[0] or "").strip()
            name = (qs.get("name", [""])[0] or "").strip()
            slot_str = (qs.get("slot", [""])[0] or "").strip()
            content = ""
            title = content_type
            try:
                from keephive.storage import (
                    active_slot,
                    guides_dir,
                    memory_file,
                    rules_file,
                    slot_file,
                )

                if content_type == "memory":
                    mf = memory_file()
                    content = mf.read_text() if mf.exists() else ""
                    title = "Working Memory"
                elif content_type == "guide":
                    gf = guides_dir() / f"{name}.md"
                    content = gf.read_text() if gf.exists() else ""
                    title = name
                elif content_type == "note":
                    slot_n = int(slot_str) if slot_str else active_slot()
                    nf = slot_file(slot_n)
                    content = nf.read_text() if nf.exists() else ""
                    title = f"Note {slot_n}"
                elif content_type == "rules":
                    rf = rules_file()
                    content = rf.read_text() if rf.exists() else ""
                    title = "Rules"
            except Exception as exc:
                content = f"Error: {exc}"
            resp_body = json.dumps(
                {"content": content, "title": title, "html": render_md(content)}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors()
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
            return

        if path == "/api/profiles":
            try:
                from keephive.storage import active_profile, list_profiles

                data = {
                    "profiles": list_profiles(),
                    "active": active_profile() or "default",
                }
                resp_body = json.dumps(data).encode()
            except Exception as exc:
                resp_body = json.dumps({"error": str(exc)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors()
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
            return

        if path == "/api/transfer/export":
            try:
                import io
                import tarfile

                from keephive.commands.transfer import _EXPORT_DIRS, _EXPORT_DOTS
                from keephive.storage import active_profile, hive_dir

                source = hive_dir()
                profile = active_profile() or "default"
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                    manifest = json.dumps(
                        {"version": "export", "profile": profile, "source": str(source)},
                        indent=2,
                    ).encode()
                    info = tarfile.TarInfo(name="manifest.json")
                    info.size = len(manifest)
                    tar.addfile(info, io.BytesIO(manifest))
                    for dirname in _EXPORT_DIRS:
                        dirpath = source / dirname
                        if dirpath.exists():
                            for fpath in sorted(dirpath.rglob("*")):
                                if fpath.is_file():
                                    arcname = str(fpath.relative_to(source))
                                    tar.add(str(fpath), arcname=arcname)
                    for dotfile in _EXPORT_DOTS:
                        fpath = source / dotfile
                        if fpath.exists():
                            tar.add(str(fpath), arcname=dotfile)

                body = buf.getvalue()
                filename = f"hive-{profile}.tar.gz"
                self.send_response(200)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self._cors()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                resp_body = json.dumps({"error": str(exc)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
            return

        # Redirect old paths to consolidated views
        if path in _REDIRECTS:
            self.send_response(302)
            self.send_header("Location", _REDIRECTS[path])
            self.end_headers()
            return

        view_name = _PATH_TO_VIEW.get(path, "")
        if not view_name:
            self.send_response(404)
            self.end_headers()
            return

        body = render_page(view_name, self.server_port).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
        except Exception:
            self.send_response(400)
            self._cors()
            self.end_headers()
            return

        ok = True
        error = ""

        if self.path == "/ui-feedback":
            try:
                from keephive.storage import ui_queue_path

                q = ui_queue_path(None)
                with q.open("a") as fh:
                    fh.write(json.dumps(data) + "\n")
            except Exception as exc:
                ok = False
                error = str(exc)

        elif self.path == "/api/remember":
            text = (data.get("text") or "").strip()
            if not text:
                ok = False
                error = "text required"
            else:
                try:
                    from keephive.clock import get_now
                    from keephive.storage import append_to_daily

                    ts = get_now().strftime("%H:%M:%S")
                    append_to_daily(f"- [{ts}] {text}")
                except Exception as exc:
                    ok = False
                    error = str(exc)

        elif self.path == "/api/todo/add":
            text = (data.get("text") or "").strip()
            if not text:
                ok = False
                error = "text required"
            else:
                try:
                    from keephive.clock import get_now
                    from keephive.storage import append_to_daily

                    ts = get_now().strftime("%H:%M:%S")
                    append_to_daily(f"- [{ts}] TODO: {text}")
                except Exception as exc:
                    ok = False
                    error = str(exc)

        elif self.path == "/api/todo/done":
            pattern = (data.get("pattern") or "").strip()
            if not pattern:
                ok = False
                error = "pattern required"
            else:
                try:
                    from keephive.commands.todo import _todo_done

                    _todo_done(pattern)
                except Exception as exc:
                    ok = False
                    error = str(exc)

        elif self.path == "/api/note/append":
            text = (data.get("text") or "").strip()
            if not text:
                ok = False
                error = "text required"
            else:
                try:
                    from keephive.storage import active_slot, slot_file

                    f = slot_file(active_slot())
                    with f.open("a") as fh:
                        fh.write(text + "\n")
                except Exception as exc:
                    ok = False
                    error = str(exc)

        elif self.path == "/api/note/switch":
            slot = data.get("slot")
            if not isinstance(slot, int) or not 1 <= slot <= 10:
                ok = False
                error = "slot must be integer 1-10"
            else:
                try:
                    from keephive.storage import set_active_slot

                    set_active_slot(slot)
                except Exception as exc:
                    ok = False
                    error = str(exc)

        elif self.path == "/api/edit":
            content_type = (data.get("type") or "").strip()
            content = data.get("content", "")
            name = (data.get("name") or "").strip()
            slot_n = data.get("slot")
            try:
                from keephive.storage import (
                    active_slot,
                    backup_and_write,
                    guides_dir,
                    memory_file,
                    rules_file,
                    slot_file,
                )

                if content_type == "memory":
                    backup_and_write(memory_file(), content)
                elif content_type == "guide" and name:
                    backup_and_write(guides_dir() / f"{name}.md", content)
                elif content_type == "note":
                    sn = int(slot_n) if slot_n else active_slot()
                    backup_and_write(slot_file(sn), content)
                elif content_type == "rules":
                    backup_and_write(rules_file(), content)
                else:
                    ok = False
                    error = "unknown content type"
            except Exception as exc:
                ok = False
                error = str(exc)

        elif self.path == "/api/preview":
            text = data.get("text", "")
            html_out = render_md(text)
            resp_body = json.dumps({"html": html_out}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors()
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
            return

        elif self.path == "/api/settings":
            key = (data.get("key") or "").strip()
            value = data.get("value")
            if not key:
                ok = False
                error = "key required"
            else:
                try:
                    from keephive.settings import DEFAULTS, set_setting

                    if key not in DEFAULTS:
                        ok = False
                        error = f"unknown setting: {key}"
                    else:
                        set_setting(key, value)
                except Exception as exc:
                    ok = False
                    error = str(exc)

        elif self.path == "/api/sound-test":
            try:
                import platform
                import shutil
                import subprocess
                from pathlib import Path

                if platform.system() != "Darwin":
                    ok = False
                    error = "Sound only supported on macOS"
                elif not shutil.which("afplay"):
                    ok = False
                    error = "afplay not found"
                else:
                    from keephive.settings import BUILTIN_SOUNDS, get_setting

                    is_error = data.get("type") == "error"
                    name = get_setting("sound_error") if is_error else get_setting("sound_success")
                    if name in BUILTIN_SOUNDS:
                        sound = f"/System/Library/Sounds/{name}.aiff"
                    else:
                        sound = str(name)
                    if not Path(sound).exists():
                        ok = False
                        error = f"Sound file not found: {sound}"
                    else:
                        subprocess.Popen(
                            ["afplay", sound],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
            except Exception as exc:
                ok = False
                error = str(exc)

        elif self.path == "/api/mem/add":
            text = (data.get("text") or "").strip()
            if not text:
                ok = False
                error = "text required"
            else:
                try:
                    from keephive.storage import memory_file

                    mf = memory_file()
                    existing = mf.read_text() if mf.exists() else ""
                    if not existing.endswith("\n"):
                        existing += "\n"
                    mf.write_text(existing + f"- {text}\n")
                except Exception as exc:
                    ok = False
                    error = str(exc)

        elif self.path == "/api/profiles/use":
            name = (data.get("name") or "").strip()
            if not name:
                ok = False
                error = "name required"
            else:
                try:
                    from keephive.commands.profile import _validate_name
                    from keephive.storage import profile_dir, profile_exists, set_active_profile

                    err = _validate_name(name)
                    if err:
                        ok = False
                        error = err
                    elif name == "default":
                        set_active_profile(None)
                    else:
                        if not profile_exists(name):
                            ok = False
                            error = f"Profile '{name}' does not exist"
                        else:
                            profile_dir(name)  # ensure directories and migrations
                            set_active_profile(name)
                except Exception as exc:
                    ok = False
                    error = str(exc)

        elif self.path == "/api/profiles/create":
            name = (data.get("name") or "").strip()
            seed = data.get("seed", False)
            if not name:
                ok = False
                error = "name required"
            else:
                try:
                    from keephive.commands.profile import _validate_name
                    from keephive.storage import (
                        active_profile,
                        ensure_dirs,
                        profile_dir,
                        set_active_profile,
                    )

                    err = _validate_name(name)
                    if err:
                        ok = False
                        error = err
                    elif name == "default":
                        ok = False
                        error = "Cannot create 'default' profile"
                    else:
                        target = profile_dir(name)
                        if target.exists():
                            ok = False
                            error = f"Profile '{name}' already exists"
                        else:
                            target.mkdir(parents=True, exist_ok=True)
                            old_profile = active_profile()
                            set_active_profile(name)
                            try:
                                ensure_dirs()
                            finally:
                                set_active_profile(old_profile)
                            if seed:
                                try:
                                    from keephive.commands.seed import cmd_seed

                                    set_active_profile(name)
                                    try:
                                        cmd_seed(["--force"])
                                    finally:
                                        set_active_profile(old_profile)
                                except ImportError:
                                    pass
                except Exception as exc:
                    ok = False
                    error = str(exc)

        elif self.path == "/api/profiles/delete":
            name = (data.get("name") or "").strip()
            if not name:
                ok = False
                error = "name required"
            else:
                try:
                    import shutil

                    from keephive.commands.profile import _validate_name
                    from keephive.storage import active_profile, profile_exists, profile_paths

                    err = _validate_name(name)
                    if err:
                        ok = False
                        error = err
                    elif name == "default":
                        ok = False
                        error = "Cannot delete the default profile"
                    elif active_profile() == name:
                        ok = False
                        error = f"Cannot delete active profile '{name}'"
                    else:
                        if not profile_exists(name):
                            ok = False
                            error = f"Profile '{name}' does not exist"
                        else:
                            preferred, legacy = profile_paths(name)
                            for target in (preferred, legacy):
                                if not target.exists():
                                    continue
                                try:
                                    if target.is_symlink():
                                        target.unlink(missing_ok=True)
                                    elif target.is_dir():
                                        shutil.rmtree(target)
                                    else:
                                        target.unlink(missing_ok=True)
                                except FileNotFoundError:
                                    continue
                except Exception as exc:
                    ok = False
                    error = str(exc)

        elif self.path == "/api/transfer/import":
            import base64

            b64 = (data.get("data") or "").strip()
            if not b64:
                ok = False
                error = "data required (base64 tar.gz)"
            else:
                try:
                    import io
                    import tarfile

                    from keephive.storage import hive_dir

                    archive_bytes = base64.b64decode(b64)
                    buf = io.BytesIO(archive_bytes)
                    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                        target = hive_dir()
                        for member in tar.getmembers():
                            if member.name.startswith("/") or ".." in member.name:
                                continue
                            if member.name == "manifest.json":
                                continue
                            if member.isfile():
                                dest = target / member.name
                                dest.parent.mkdir(parents=True, exist_ok=True)
                                src = tar.extractfile(member)
                                if src:
                                    dest.write_bytes(src.read())
                except Exception as exc:
                    ok = False
                    error = str(exc)

        else:
            self.send_response(404)
            self._cors()
            self.end_headers()
            return

        if ok:
            resp = json.dumps({"ok": True}).encode()
        else:
            resp = json.dumps({"ok": False, "error": error}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # Silence request logging


# ---- Entry point ----


def _hot_watcher(port: int) -> None:
    """Watch source files and restart the HTTP server on change."""
    import os
    import subprocess
    import time
    from pathlib import Path

    src_dir = Path(__file__).parent.parent  # src/keephive/

    def _mtimes() -> dict[str, float]:
        result: dict[str, float] = {}
        for p in src_dir.rglob("*.py"):
            try:
                result[str(p)] = p.stat().st_mtime
            except OSError:
                pass
        return result

    env = {**os.environ, "HIVE_SERVE_WORKER": "1"}
    worker_cmd = [sys.executable, "-m", "keephive", "serve", str(port)]

    print(f"  hive serve --hot  (port {port})")
    print(f"  Watching: {src_dir}")
    print()

    first = True
    while True:
        if first:
            print(f"  Starting...  http://localhost:{port}")
        else:
            print("  Restarting...")

        proc = subprocess.Popen(worker_cmd, env=env)
        snapshot = _mtimes()
        first = False

        try:
            while True:
                if proc.poll() is not None:
                    print("  Server exited unexpectedly, restarting in 1s...")
                    time.sleep(1)
                    break

                time.sleep(0.4)
                now = _mtimes()
                changed = [
                    Path(p).relative_to(src_dir) for p in now if now[p] != snapshot.get(p, 0)
                ]
                changed += [Path(p).relative_to(src_dir) for p in snapshot if p not in now]
                if changed:
                    names = ", ".join(str(c) for c in changed[:3])
                    suffix = f" (+{len(changed) - 3} more)" if len(changed) > 3 else ""
                    print(f"\n  [hot] {names}{suffix}")
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    time.sleep(0.1)
                    break
                snapshot = now
        except KeyboardInterrupt:
            print("\n  Stopped.")
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
            return


def cmd_serve(args: list[str]) -> None:
    import os

    port = DEFAULT_PORT
    hot = False
    remaining = []
    for a in args:
        if a in ("--hot", "--hot-reload"):
            hot = True
        else:
            remaining.append(a)

    if remaining:
        try:
            port = int(remaining[0])
        except ValueError:
            print(f"Invalid port: {remaining[0]}", file=sys.stderr)
            return

    if hot and not os.environ.get("HIVE_SERVE_WORKER"):
        _hot_watcher(port)
        return

    _HiveHandler.server_port = port
    _HiveHandler.project_name = os.path.basename(os.getcwd())

    try:
        httpd = HTTPServer(("localhost", port), _HiveHandler)
    except OSError as exc:
        print(f"Could not start server on port {port}: {exc}", file=sys.stderr)
        return

    url = f"http://localhost:{port}"
    if os.environ.get("HIVE_SERVE_WORKER"):
        print(f"  Ready: {url}")
    else:
        print(f"Dashboard at {url}  \u2014  ctrl+c to stop")
        webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        if not os.environ.get("HIVE_SERVE_WORKER"):
            print("\nStopped.")
