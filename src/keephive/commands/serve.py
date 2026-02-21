"""hive serve: live web dashboard for keephive data.

Serves a local web dashboard at localhost:3847 (default).
Views: / (home), /dev, /know (tabbed: guides/memory/notes), /stats

Usage: hive serve [port] [--hot]
       --hot   Watch source files, restart server on change
"""

from __future__ import annotations

import base64
import html as _html
import json
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

DEFAULT_PORT = 3847

_FAVICON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><polygon points="16,2 28,9 28,23 16,30 4,23 4,9" fill="#f59e0b" stroke="#d97706" stroke-width="2"/><polygon points="16,8 22,12 22,20 16,24 10,20 10,12" fill="#fbbf24"/></svg>'
_FAVICON = "data:image/svg+xml;base64," + base64.b64encode(_FAVICON_SVG.encode()).decode()

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
.nav-brand{color:#f0f6fc;font-weight:700;font-size:14px;padding:10px 10px 10px 0;margin-right:10px;border-right:1px solid #30363d}
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
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px;align-items:start}
@media(max-width:900px){.grid-2{grid-template-columns:1fr}}
.split-pane{display:flex;gap:0;margin-bottom:16px;align-items:start}
.split-left{flex:1;min-width:200px}
.split-right{flex:1;min-width:200px}
.split-divider{width:8px;cursor:col-resize;background:transparent;flex-shrink:0;position:relative;margin:0 4px}
.split-divider::after{content:'';position:absolute;top:20%;bottom:20%;left:3px;width:2px;background:#30363d;border-radius:1px}
.split-divider:hover::after,.split-divider.dragging::after{background:#58a6ff}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin-bottom:12px;transition:border-color .1s}
.card-header{padding:7px 14px;background:#1e252e;border-bottom:1px solid #30363d;display:flex;align-items:center;justify-content:space-between;gap:8px}
.card-title{font-weight:600;font-size:13px;color:#f0f6fc}
.card-meta{color:#6e7681;font-size:12px}
.card-body{padding:10px 14px}
.stat-row{display:flex;gap:20px;flex-wrap:wrap;margin-bottom:8px;justify-content:center}
.stat-item{display:flex;flex-direction:column;gap:2px;align-items:center;text-align:center}
.stat-value{font-size:22px;font-weight:700;color:#f0f6fc}
.stat-value.warn{color:#e3b341}.stat-value.err{color:#f85149}.stat-value.ok{color:#3fb950}
.stat-label{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.04em}
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
.summary-stats{display:flex;gap:12px;padding:6px 12px;justify-content:center}
.summary-stat{text-align:center}
.summary-stat .stat-value{font-size:18px;font-weight:700;color:#e6edf3;display:block}
.summary-stat .stat-label{font-size:10px;color:#8b949e}
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

  // --- Helpers ---
  function escHtml(s){
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  // All cards (flat list in DOM order)
  function _cards(){
    return Array.from(document.querySelectorAll('#main-content .card[tabindex]'));
  }
  // Build row structure: array of arrays of card indices.
  // Each row = cards sharing the same layout container (split-pane, grid-2, or solo).
  function _rows(){
    var mc=document.getElementById('main-content');
    if(!mc)return [];
    var cards=_cards();
    if(!cards.length)return [];
    var rows=[];var seen=new Set();
    // Walk top-level children of #main-content
    var children=mc.children;
    for(var i=0;i<children.length;i++){
      var child=children[i];
      var rowCards=[];
      if(child.classList.contains('split-pane')||child.classList.contains('grid-2')){
        // Multi-card row: find all cards inside
        var inner=child.querySelectorAll('.card[tabindex]');
        for(var j=0;j<inner.length;j++){
          var ci=cards.indexOf(inner[j]);
          if(ci>=0&&!seen.has(ci)){rowCards.push(ci);seen.add(ci);}
        }
      }else if(child.matches&&child.matches('.card[tabindex]')){
        // Solo card row
        var ci2=cards.indexOf(child);
        if(ci2>=0&&!seen.has(ci2)){rowCards.push(ci2);seen.add(ci2);}
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
    return Array.from(card.querySelectorAll('.todo-item[tabindex], .log-entry[tabindex], .accordion[tabindex], .note-tile[tabindex]'));
  }
  // All focusables (flat, for search-result nav)
  function _focusables(){
    return Array.from(document.querySelectorAll('#main-content .card[tabindex], #main-content .todo-item[tabindex], #main-content .log-entry[tabindex], #main-content .accordion[tabindex], #main-content .note-tile[tabindex]'));
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
    if(mc)mc.classList.add('is-loading');
    fetch(url)
      .then(function(r){return r.text();})
      .then(function(h){
        if(mc){mc.innerHTML=h;mc.classList.remove('is-loading');}
        lastSuccess=Date.now();
        updateTs();
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
  });

  // --- Auto-expand first accordion on /know (memory/notes tabs) ---
  if(view==='know'){
    var firstBody=document.querySelector('.tab-content.active .acc-body');
    var firstHdr=document.querySelector('.tab-content.active .acc-header');
    if(firstBody){firstBody.classList.add('open');}
    if(firstHdr){firstHdr.classList.add('open');}
  }

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
    container.querySelectorAll('.log-filter-btn').forEach(function(b){
      b.classList.toggle('active',b===btn);
      b.setAttribute('aria-pressed',String(b===btn));
    });
    container.querySelectorAll('.log-entry').forEach(function(entry){
      if(!type){entry.classList.remove('filtered');}
      else{entry.classList.toggle('filtered',entry.dataset.type!==type);}
    });
  });

  // --- Note slot switcher ---
  window.switchNote=function(n){
    document.querySelectorAll('.slot-btn').forEach(function(b){b.disabled=true;});
    fetch('/api/note/switch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slot:n})})
      .then(function(r){return r.json();})
      .then(function(d){if(d.ok)refresh();})
      .catch(function(){document.querySelectorAll('.slot-btn').forEach(function(b){b.disabled=false;});});
  };

  // --- Split pane drag ---
  document.addEventListener('mousedown',function(e){
    var d=e.target.closest('.split-divider');
    if(!d)return;
    e.preventDefault();
    var left=d.previousElementSibling;
    var right=d.nextElementSibling;
    var startX=e.clientX;
    var startLeft=left.getBoundingClientRect().width;
    var startRight=right.getBoundingClientRect().width;
    var total=startLeft+startRight;
    d.classList.add('dragging');
    function move(ev){
      var dx=ev.clientX-startX;
      var newLeft=Math.max(200,Math.min(total-200,startLeft+dx));
      left.style.flex='0 0 '+newLeft+'px';
      right.style.flex='0 0 '+(total-newLeft)+'px';
    }
    function up(){
      d.classList.remove('dragging');
      document.removeEventListener('mousemove',move);
      document.removeEventListener('mouseup',up);
    }
    document.addEventListener('mousemove',move);
    document.addEventListener('mouseup',up);
  });

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
  });

  // --- Keyboard navigation ---
  var _VIEW_KEYS={h:'/',d:'/dev',k:'/know',s:'/stats'};

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
    else if(k==='h'&&!_innerMode){
      var cellH=_findCell(_focusIdx);var rsH=_rows();
      if(cellH){
        var rowH=rsH[cellH.rowIdx];
        if(cellH.colIdx>0){_setFocus(rowH[cellH.colIdx-1]);}
        else if(cellH.rowIdx>0){var pr=rsH[cellH.rowIdx-1];_setFocus(pr[pr.length-1]);}
      }
      e.preventDefault();
    }
    else if(k==='l'&&!_innerMode){
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
    // Note slot switching (1-9)
    else if(k>='1'&&k<='9'){
      if(view==='know'){window.switchNote(parseInt(k,10));}
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
})();
"""


# ---- Data providers ----


def _safe_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


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

    from datetime import date as _date
    from datetime import timedelta as _timedelta

    yesterday = (_date.today() - _timedelta(days=1)).isoformat()

    # Activity metrics from stats (for merged activity section)
    activity_today = 0
    activity_week = 0
    activity_streak = 0
    activity_hours: dict[str, int] = {}
    try:
        from keephive.storage import read_stats

        _stats = read_stats()
        _days = _stats.get("days", {})
        _today_str = _date.today().isoformat()
        _week_start = (_date.today() - _timedelta(days=7)).isoformat()
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
    }


def _get_log_data(date_str: str | None = None) -> dict:
    import re as re_mod

    from keephive.storage import daily_file, safe_read_text

    path = daily_file(date_str)
    if not path.exists():
        from datetime import date

        used_date = date_str if date_str else date.today().isoformat()
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
                elif rest.upper().startswith("AUTO-PROMOTED:"):
                    cat = "auto"
            entries.append({"time": ts[:5], "text": rest, "cat": cat})
    from datetime import date

    used_date = date_str if date_str else date.today().isoformat()
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


def _get_stats_data() -> dict:
    from datetime import date, timedelta

    from keephive.storage import read_stats

    stats = read_stats()
    days = stats.get("days", {})
    today = date.today().isoformat()
    week_start = (date.today() - timedelta(days=7)).isoformat()

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
        d = date.today() - timedelta(days=i)
        day_s = d.isoformat()
        total_cmds = sum(days.get(day_s, {}).get("commands", {}).values())
        daily_spark.append((d.strftime("%b %d"), total_cmds, day_s))

    # Today's hourly data
    today_str = date.today().isoformat()
    today_day = days.get(today_str, {})
    today_hours: dict[str, int] = today_day.get("hours", {})

    return {
        "commands": top,
        "today": cmd_today,
        "week": cmd_week,
        "total_days": total_days,
        "projects": top_projects,
        "curr_streak": curr_streak,
        "longest_streak": longest_streak,
        "daily_spark": daily_spark,
        "today_hours": today_hours,
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
    yesterday_entries = data.get("yesterday_entries", 0)
    hooks_ok = data.get("hooks_ok", False)
    mcp_ok = data.get("mcp_ok", False)
    data_ok = data.get("data_ok", True)
    stale_facts = data.get("stale_facts", [])
    todo_count = data.get("todo_count", 0)

    stale_cls = "warn" if stale > 0 else "ok"
    stale_val = f'<span class="stat-value {stale_cls}">{stale}</span>'
    rows = (
        f'<div class="stat-row">'
        f'<div class="stat-item"><span class="stat-value">{total}</span><span class="stat-label">verified facts</span></div>'
        f'<div class="stat-item">{stale_val}<span class="stat-label">stale facts</span></div>'
        f'<div class="stat-item"><span class="stat-value">{todo_count}</span><span class="stat-label">open todos</span></div>'
        f'<div class="stat-item"><span class="stat-value">{today_entries}</span><span class="stat-label">logged today</span></div>'
        f'<div class="stat-item"><span class="stat-value">{yesterday_entries}</span><span class="stat-label">logged yesterday</span></div>'
        f"</div>"
    )

    def _dot(ok: bool, label: str) -> str:
        dot_cls = "dot-ok" if ok else "dot-off"
        sym = "●" if ok else "○"
        return f'<span class="{dot_cls}">{sym}</span><span class="health-label">{label}</span>'

    health = (
        '<div class="health-row">'
        + _dot(hooks_ok, "hooks")
        + _dot(mcp_ok, "mcp")
        + _dot(data_ok, "data")
        + "</div>"
    )

    # Stale facts accordion
    stale_accordion = ""
    if stale > 0 and stale_facts:
        items_html = "".join(
            f'<div class="fact-item"><span class="fact-text">{_e(f)}</span></div>'
            for f in stale_facts
        )
        label = f"{stale} stale fact{'s' if stale != 1 else ''} &#9658;"
        verify_hint = '<div style="margin-top:6px"><span class="cmd-hint">hive v</span></div>'
        stale_accordion = (
            f'<details class="stale-accordion">'
            f'<summary class="stale-summary">{label}</summary>'
            f'<div style="margin-top:6px">{items_html}</div>'
            f"{verify_hint}"
            f"</details>"
        )

    # Activity section (merged from stats-summary)
    activity_today = data.get("activity_today", 0)
    activity_week = data.get("activity_week", 0)
    activity_streak = data.get("activity_streak", 0)
    activity_hours = data.get("activity_hours", {})

    activity_html = ""
    if activity_today > 0 or activity_week > 0 or activity_streak > 0:
        activity_html = (
            f'<div class="status-divider"></div>'
            f'<div class="stat-row">'
            f'<div class="stat-item"><span class="stat-value">{activity_today}</span><span class="stat-label">cmds today</span></div>'
            f'<div class="stat-item"><span class="stat-value">{activity_week}</span><span class="stat-label">this week</span></div>'
            f'<div class="stat-item"><span class="stat-value">{activity_streak}d</span><span class="stat-label">streak</span></div>'
            f"</div>"
        )
        hourly = _render_hourly_heatmap(activity_hours)
        if hourly:
            activity_html += hourly
        activity_html += '<a class="summary-link" href="/stats">Full stats &rarr;</a>'

    hints = ["hive s", "hive v", "hive dr"]
    if stale > 0:
        hints = ["hive v  \u2190 stale facts!", "hive s", "hive dr"]
    elif not hooks_ok or not mcp_ok:
        hints = ["hive setup", "hive s", "hive dr"]
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Status">'
        f'<div class="card-header"><span class="card-title">Status</span></div>'
        f"{_cmd_hints(hints)}"
        f'<div class="card-body">{rows}{health}{stale_accordion}{activity_html}</div>'
        f"</div>"
    )


def _render_status_brief_panel(data: dict) -> str:
    stale = data.get("stale", 0)
    total = data.get("total_verified", 0)
    today_entries = data.get("today_entries", 0)
    todo_count = data.get("todo_count", 0)
    activity_today = data.get("activity_today", 0)
    stale_str = f' <span style="color:#e3b341">({stale} stale)</span>' if stale > 0 else ""
    activity_str = (
        f" &nbsp;|&nbsp; <span>{activity_today}</span> cmds today" if activity_today > 0 else ""
    )
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Status">'
        f'<div class="card-body">'
        f'<div class="status-brief">'
        f"<span>{total}</span> verified facts{stale_str} &nbsp;|&nbsp; "
        f"<span>{today_entries}</span> logged today &nbsp;|&nbsp; "
        f"<span>{todo_count}</span> open todos"
        f"{activity_str}"
        f"</div></div></div>"
    )


def _render_log_panel(
    data: dict, limit: int = 0, show_nav: bool = True, see_more_url: str = ""
) -> str:
    from datetime import date as _date
    from datetime import timedelta

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
        today_str = _date.today().isoformat()
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
        "auto": "AUTO-PROMOTED:",
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
    title = "Today's Log" if not date_str or date_str == _date.today().isoformat() else "Log"
    log_hints = _cmd_hints(['hive r "FACT: ..."', "hive l", "hive l summarize"])
    log_input = (
        '<form class="panel-input" data-action="/api/remember" data-field="text">'
        '<input type="text" placeholder="hive r \u2014 fact or note..." autocomplete="off">'
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

    todos = data.get("todos", [])
    total = len(todos)
    if limit > 0:
        todos = list(reversed(todos))[:limit]
    else:
        todos = list(reversed(todos))
    today_str = date.today().isoformat()
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
        f"{_cmd_hints(['hive t <text>', 'hive todo done <pat>', 'hive todo'])}"
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
        rows = '<div class="empty">No knowledge guides yet — hive ke &lt;name&gt;</div>'
    total = len(guides) + len(prompts) + len(skills)
    meta = f"{len(guides)}g / {len(prompts)}p / {len(skills)}sk" if total else ""
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Knowledge">'
        f'<div class="card-header"><span class="card-title">Knowledge</span><span class="card-meta">{meta}</span></div>'
        f"{_cmd_hints(['hive ke <name>', 'hive pe <name>', 'hive k <name>', 'hive rf draft <topic>'])}"
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
        tiles = '<div class="empty">No notes \u2014 hive n</div>'
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
        f"{_cmd_hints(['hive m <fact>', 'hive m rm <pat>', 'hive rule <text>', 'hive e memory'])}"
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
        rows = '<div class="empty">No notes — hive n</div>'
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
        f"{_cmd_hints(['hive n', 'hive n show', 'hive nc', 'hive n.3'])}"
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
    total_days = data.get("total_days", 0)
    curr_streak = data.get("curr_streak", 0)
    longest_streak = data.get("longest_streak", 0)
    daily_spark = data.get("daily_spark", [])
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

            bars += f'<div class="spark-bar{cls}" style="height:{h}px{extra_bg}" title="{_e(label)}: {count} cmds"></div>'

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
            f'<div class="stat-item"><span class="stat-value">{curr_streak}</span><span class="stat-label">curr streak</span></div>'
            f'<div class="stat-item"><span class="stat-value">{longest_streak}</span><span class="stat-label">best streak</span></div>'
            f'<div class="stat-item"><span class="stat-value">{total_days}</span><span class="stat-label">days active</span></div>'
            f"</div>"
        )

    meta = f"{total_days} days tracked" if total_days else ""
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Usage stats">'
        f'<div class="card-header"><span class="card-title">Usage Stats</span><span class="card-meta">{meta}</span></div>'
        f"{_cmd_hints(['hive st', 'hive st -p <project>', 'hive st yesterday'])}"
        f"{sparkline_html}"
        f"{hourly_html}"
        f'<div class="card-body">{streak_html}</div>'
        f"</div>"
    )


def _render_stats_commands_panel(data: dict) -> str:
    """Command breakdown table as a standalone card."""
    commands = data.get("commands", [])
    today_map = data.get("today", {})
    week_map = data.get("week", {})

    rows = ""
    if commands:
        rows = (
            '<table class="stats-table">'
            "<thead><tr><th>Command</th><th>Today</th><th>Week</th><th>All-time</th></tr></thead><tbody>"
        )
        for name, total in commands:
            t = today_map.get(name, 0)
            w = week_map.get(name, 0)
            rows += (
                f"<tr><td>{_e(name)}</td><td>{t or ''}</td><td>{w or ''}</td><td>{total}</td></tr>"
            )
        rows += "</tbody></table>"
    else:
        rows = '<div class="empty">No usage data yet</div>'
    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Commands">'
        f'<div class="card-header"><span class="card-title">Commands</span></div>'
        f'<div class="card-body">{rows}</div>'
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
        f"{_cmd_hints(['hive ps', 'hive go', 'hive su'])}"
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
    facts_hints = _cmd_hints(['hive r "FACT: ..."', "hive rc <query>", "hive rf scan"])
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
        f"{_cmd_hints(['hive su', 'hive todo', 'hive todo done <pat>'])}"
        f'<div class="card-body">{rows}</div>'
        f"</div>"
    )


def _get_stats_summary_data() -> dict:
    """Compact stats for the home view: today, week, streak, hourly."""
    from datetime import date, timedelta

    from keephive.storage import read_stats

    data = read_stats()
    days = data.get("days", {})
    today_str = date.today().isoformat()
    week_start = (date.today() - timedelta(days=7)).isoformat()

    today_data = days.get(today_str, {})
    today_total = sum(today_data.get("commands", {}).values())
    today_hours: dict[str, int] = today_data.get("hours", {})

    week_total = 0
    for day_str, day_data in days.items():
        if day_str >= week_start:
            week_total += sum(day_data.get("commands", {}).values())

    curr_streak = 0
    try:
        from keephive.commands.stats import _calculate_streak

        curr_streak, _ = _calculate_streak(days)
    except Exception:
        pass

    return {
        "today_total": today_total,
        "week_total": week_total,
        "curr_streak": curr_streak,
        "today_hours": today_hours,
    }


def _render_stats_summary_panel(data: dict) -> str:
    today_total = data.get("today_total", 0)
    week_total = data.get("week_total", 0)
    curr_streak = data.get("curr_streak", 0)
    today_hours = data.get("today_hours", {})

    stats_row = (
        f'<div class="summary-stats">'
        f'<div class="summary-stat"><span class="stat-value">{today_total}</span><span class="stat-label">today</span></div>'
        f'<div class="summary-stat"><span class="stat-value">{week_total}</span><span class="stat-label">this week</span></div>'
        f'<div class="summary-stat"><span class="stat-value">{curr_streak}d</span><span class="stat-label">streak</span></div>'
        f"</div>"
    )

    hourly = _render_hourly_heatmap(today_hours)
    link = '<a class="summary-link" href="/stats">Full stats &rarr;</a>'

    return (
        f'<div class="card" tabindex="0" role="region" aria-label="Activity">'
        f'<div class="card-header"><span class="card-title">Activity</span></div>'
        f'<div class="card-body">{stats_row}{hourly}</div>'
        f"{link}"
        f"</div>"
    )


def _get_knowledge_all_data() -> dict:
    """Composite data for the tabbed knowledge view: guides + memory + notes."""
    return {
        "knowledge": _get_knowledge_data(),
        "memory": _get_memory_data(),
        "notes": _get_notes_data(),
    }


def _render_knowledge_tabbed_panel(data: dict) -> str:
    """Knowledge view with client-side tabs: Guides / Memory / Notes."""
    know_data = data.get("knowledge", {})
    mem_data = data.get("memory", {})
    notes_data = data.get("notes", {})

    guides_html = _render_knowledge_panel(know_data)
    memory_html = _render_memory_panel(mem_data)
    notes_html = _render_notes_panel(notes_data)

    tab_bar = (
        '<div class="tab-bar">'
        '<button class="tab-btn active" data-tab="guides">Guides</button>'
        '<button class="tab-btn" data-tab="memory">Memory</button>'
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
    "memory": (_get_memory_data, _render_memory_panel),
    "notes": (_get_notes_data, _render_notes_panel),
    "notes-compact": (_get_notes_data, _render_notes_compact_panel),
    "stats": (_get_stats_data, _render_stats_panel),
    "stats-commands": (_get_stats_data, _render_stats_commands_panel),
    "ps": (_get_ps_data, _render_ps_panel),
    "facts": (_get_recent_facts_data, _render_recent_facts_panel),
    "standup": (_get_standup_data, _render_standup_panel),
    "stats-summary": (_get_stats_summary_data, _render_stats_summary_panel),
    "knowledge-tabbed": (_get_knowledge_all_data, _render_knowledge_tabbed_panel),
}

# ---- View definitions ----

VIEWS: dict[str, dict] = {
    "home": {
        "path": "/",
        "title": "Home",
        "rows": [
            ["status", "ps"],
            ["log-home"],
            ["todos", "recurring"],
            ["standup"],
        ],
    },
    "dev": {
        "path": "/dev",
        "title": "Dev",
        "rows": [
            ["status-brief"],
            ["todos-brief", "log-brief"],
            ["facts"],
            ["knowledge-compact", "memory"],
        ],
    },
    "know": {
        "path": "/know",
        "title": "Knowledge",
        "rows": [["knowledge-tabbed"]],
    },
    "stats": {
        "path": "/stats",
        "title": "Stats",
        "rows": [
            ["stats", "ps"],
            ["stats-commands"],
        ],
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
    for row in view_def.get("rows", []):
        if len(row) == 1:
            parts.append(_render_panel_safe(row[0], extra_params))
        elif len(row) == 2:
            left = _render_panel_safe(row[0], extra_params)
            right = _render_panel_safe(row[1], extra_params)
            parts.append(
                f'<div class="split-pane">'
                f'<div class="split-left">{left}</div>'
                f'<div class="split-divider" title="Drag to resize"></div>'
                f'<div class="split-right">{right}</div>'
                f"</div>"
            )
        else:
            cols = "".join(_render_panel_safe(name, extra_params) for name in row)
            parts.append(f'<div class="grid-2">{cols}</div>')
    return "\n".join(parts)


def render_page(view_name: str, port: int) -> str:
    nav_tabs = ""
    for vname, vdef in VIEWS.items():
        active_cls = " active" if vname == view_name else ""
        nav_tabs += (
            f'<a class="nav-tab{active_cls}" href="{_e(vdef["path"])}">{_e(vdef["title"])}</a>'
        )

    content = render_fragment(view_name)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="{_FAVICON}">
<title>hive \u2014 {_e(VIEWS.get(view_name, {}).get("title", view_name))}</title>
<style>{_CSS}</style>
</head>
<body data-view="{_e(view_name)}" data-port="{port}">
<nav role="navigation" aria-label="Dashboard views">
  <span class="nav-brand">hive</span>
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
      <span class="help-key">h / l</span><span class="help-desc">Left / right card in same row</span>
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
      <span class="help-key">1-9</span><span class="help-desc">Switch note slot</span>
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

                ui_queue_path(self.__class__.project_name or None).write_text(
                    json.dumps(data, indent=2)
                )
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
                    from datetime import datetime

                    from keephive.storage import append_to_daily

                    ts = datetime.now().strftime("%H:%M:%S")
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
                    from datetime import datetime

                    from keephive.storage import append_to_daily

                    ts = datetime.now().strftime("%H:%M:%S")
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
