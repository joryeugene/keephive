"""hive serve: live web dashboard for keephive data.

Serves a local web dashboard at localhost:3847 (default).
Views: / (all), /daily, /dev, /simple, /stats, /know, /mem, /notes

Usage: hive serve [port] [--hot]
       --hot   Watch source files, restart server on change
"""

from __future__ import annotations

import html as _html
import json
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

DEFAULT_PORT = 3847

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
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9;font-size:13px;line-height:1.6}
nav{background:#161b22;border-bottom:1px solid #30363d;padding:0 16px;display:flex;align-items:center;gap:2px;position:sticky;top:0;z-index:100}
.nav-brand{color:#f0f6fc;font-weight:700;font-size:14px;padding:12px 12px 12px 0;margin-right:10px;border-right:1px solid #30363d}
.nav-tab{color:#8b949e;text-decoration:none;padding:12px 10px;border-bottom:2px solid transparent;font-size:13px;white-space:nowrap}
.nav-tab:hover{color:#c9d1d9}.nav-tab.active{color:#58a6ff;border-bottom-color:#58a6ff}
.nav-right{margin-left:auto;display:flex;align-items:center;gap:8px;padding-left:12px}
.refresh-label{color:#8b949e;font-size:12px}
select.refresh-select{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:3px 8px;border-radius:4px;font-size:12px;cursor:pointer}
#refresh-ts{color:#6e7681;font-size:11px;min-width:88px;text-align:right}
#search-input{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:3px 8px;border-radius:4px;font-size:12px;width:140px;outline:none}
#search-input:focus{border-color:#58a6ff}
#search-input::placeholder{color:#6e7681}
main{max-width:1400px;margin:0 auto;padding:16px}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
@media(max-width:900px){.grid-2{grid-template-columns:1fr}}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin-bottom:16px}
.card-header{padding:9px 14px;background:#1c2128;border-bottom:1px solid #30363d;display:flex;align-items:center;justify-content:space-between;gap:8px}
.card-title{font-weight:600;font-size:13px;color:#f0f6fc}
.card-meta{color:#6e7681;font-size:12px}
.card-body{padding:12px 14px}
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
.status-brief{font-size:12px;color:#8b949e;padding:4px 0}
.status-brief span{color:#c9d1d9;font-weight:600}
.log-date-nav{display:flex;align-items:center;gap:4px}
.date-nav-btn{background:none;border:1px solid #30363d;color:#8b949e;padding:1px 6px;border-radius:3px;cursor:pointer;font-size:14px;line-height:1.4}
.date-nav-btn:hover:not([disabled]){color:#c9d1d9;border-color:#58a6ff}
.date-nav-btn:disabled{opacity:0.3;cursor:default}
.log-date-label{font-size:12px;color:#6e7681;min-width:82px;text-align:center}
.log-entry{display:flex;gap:8px;padding:4px 0;border-bottom:1px solid #21262d;font-size:12px}
.log-entry:last-child{border-bottom:none}
.log-time{color:#6e7681;font-family:monospace;min-width:52px;flex-shrink:0}
.log-text{flex:1;color:#c9d1d9;word-break:break-word}
.fact{color:#79c0ff}.decision{color:#d2a8ff}.insight{color:#56d364}
.todo-color{color:#e3b341}.correction{color:#ffa657}
.log-see-more{display:block;padding:6px 0;font-size:12px;color:#58a6ff;text-decoration:none;text-align:center}
.log-see-more:hover{color:#79c0ff}
.todo-item{padding:5px 0;border-bottom:1px solid #21262d;display:flex;gap:8px;align-items:baseline;font-size:12px}
.todo-item:last-child{border-bottom:none}
.todo-age{color:#6e7681;font-size:11px;min-width:36px;text-align:right;flex-shrink:0}
.todo-age.old{color:#e3b341}.todo-age.vold{color:#f85149}
.todo-text{flex:1;color:#c9d1d9}
.recurring-item{display:flex;gap:8px;padding:4px 0;border-bottom:1px solid #21262d;font-size:12px}
.recurring-item:last-child{border-bottom:none}
.recurring-freq{color:#8b949e;font-family:monospace;min-width:54px;flex-shrink:0}
.recurring-text{flex:1;color:#c9d1d9}
.recurring-due{color:#e3b341;font-size:11px}.recurring-due.overdue{color:#f85149}
.accordion{border:1px solid #30363d;border-radius:6px;overflow:hidden;margin-bottom:8px}
.acc-header{padding:8px 12px;background:#1c2128;cursor:pointer;display:flex;align-items:center;gap:8px;font-size:13px;color:#c9d1d9;user-select:none}
.acc-header:hover{background:#262c36}
.acc-toggle{color:#6e7681;font-size:10px;width:10px;flex-shrink:0}
.acc-name{flex:1}
.acc-meta{font-size:11px;color:#6e7681}
.acc-type{font-size:10px;padding:1px 6px;border-radius:10px;background:#21262d;color:#8b949e}
.acc-body{padding:12px 14px;display:none;font-size:13px}
.acc-body.open{display:block}
.guide-overflow{display:none}
.show-more-btn{background:none;border:1px solid #30363d;color:#8b949e;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:12px;width:100%;margin-top:4px;text-align:center}
.show-more-btn:hover{color:#c9d1d9;border-color:#58a6ff}
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
.fact-item{padding:4px 0;border-bottom:1px solid #21262d;font-size:12px}
.fact-item:last-child{border-bottom:none}
.fact-date{color:#6e7681;font-size:11px}
.fact-text{color:#c9d1d9}
.empty{color:#6e7681;font-size:12px;padding:6px 0;font-style:italic}
.cmd-hints{display:flex;flex-wrap:wrap;gap:5px;padding:6px 12px;border-bottom:1px solid #21262d;background:#0a0e13}
.cmd-hint{font-family:monospace;font-size:11px;color:#8b949e;background:#161b22;border:1px solid #30363d;border-radius:3px;padding:2px 7px;cursor:default;user-select:all;transition:border-color .15s}
.cmd-hint:hover{border-color:#58a6ff;color:#c9d1d9}
.standup-section{margin-bottom:8px}
.standup-label{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.04em;display:block;margin-bottom:3px}
.standup-item{font-size:12px;color:#c9d1d9;padding:2px 0}
.done-item{color:#3fb950}.pr-item{color:#79c0ff}
#search-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;z-index:200;background:rgba(0,0,0,0.6);justify-content:center;align-items:flex-start;padding-top:80px}
.search-panel{background:#161b22;border:1px solid #30363d;border-radius:8px;width:600px;max-width:92vw;max-height:70vh;overflow-y:auto}
.search-header{padding:10px 14px;border-bottom:1px solid #30363d;display:flex;justify-content:space-between;align-items:center}
.search-title{color:#f0f6fc;font-weight:600;font-size:13px}
.search-close{background:none;border:none;color:#8b949e;font-size:16px;cursor:pointer;padding:0 4px;line-height:1}
.search-close:hover{color:#c9d1d9}
.search-body{padding:12px 14px}
.search-result{padding:5px 0;border-bottom:1px solid #21262d;font-size:12px}
.search-result:last-child{border-bottom:none}
.search-date{color:#6e7681;font-size:11px;margin-right:8px;font-family:monospace}
.search-line{color:#c9d1d9;word-break:break-word}
"""

_JS = """
(function(){
  var view=document.body.dataset.view||'all';
  var iv=null;
  var tsIv=null;
  var lastSuccess=Date.now();
  var lastInterval=10;
  var logDate=null;

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
    var url='/api/fragment?view='+view;
    if(logDate)url+='&log_date='+logDate;
    fetch(url)
      .then(function(r){return r.text();})
      .then(function(h){
        var mc=document.getElementById('main-content');
        if(mc){mc.innerHTML=h;}
        lastSuccess=Date.now();
        updateTs();
      }).catch(function(){
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

  // --- Accordion toggle ---
  document.addEventListener('click',function(e){
    var h=e.target.closest('.acc-header');
    if(!h)return;
    var b=h.nextElementSibling;
    if(b&&b.classList.contains('acc-body')){
      b.classList.toggle('open');
      var t=h.querySelector('.acc-toggle');
      if(t)t.textContent=b.classList.contains('open')?'\u25bc':'\u25b6';
    }
  });

  // --- Guide show-more ---
  document.addEventListener('click',function(e){
    if(!e.target.classList.contains('show-more-btn'))return;
    var btn=e.target;
    var ov=btn.previousElementSibling;
    if(ov&&ov.classList.contains('guide-overflow'))ov.style.display='';
    btn.style.display='none';
  });

  // --- Search overlay ---
  function escHtml(s){
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  function closeSearch(){
    var ov=document.getElementById('search-overlay');
    if(ov)ov.style.display='none';
    var si=document.getElementById('search-input');
    if(si)si.value='';
  }
  var si=document.getElementById('search-input');
  if(si){
    si.addEventListener('keydown',function(e){
      if(e.key==='Enter'&&this.value.trim()){
        var q=encodeURIComponent(this.value.trim());
        fetch('/api/search?q='+q)
          .then(function(r){return r.json();})
          .then(function(data){
            var results=data.results||[];
            var html='';
            if(!results.length){
              html='<div class="empty">No results</div>';
            } else {
              results.forEach(function(r){
                html+='<div class="search-result">'
                  +'<span class="search-date">'+escHtml(r.date||'')+'</span>'
                  +'<span class="search-line">'+escHtml(r.line||'')+'</span>'
                  +'</div>';
              });
            }
            var sb=document.getElementById('search-body');
            if(sb)sb.innerHTML=html;
            var ov=document.getElementById('search-overlay');
            if(ov)ov.style.display='flex';
          });
      } else if(e.key==='Escape'){
        closeSearch();
      }
    });
  }
  document.addEventListener('keydown',function(e){
    if(e.key==='Escape')closeSearch();
  });
  var sc=document.getElementById('search-close');
  if(sc)sc.addEventListener('click',closeSearch);
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

    return {
        "stale": stale,
        "total_verified": total_verified,
        "guide_count": guide_count,
        "today_entries": count_daily_entries(),
        "hooks_ok": hooks_ok,
        "mcp_ok": mcp_ok,
        "data_ok": data_ok,
        "stale_facts": stale_facts,
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
            skills.append(d.name)
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
                lines = sum(1 for l in content.splitlines() if l.strip())
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

    return {
        "commands": top,
        "today": cmd_today,
        "week": cmd_week,
        "total_days": total_days,
        "projects": top_projects,
        "curr_streak": curr_streak,
        "longest_streak": longest_streak,
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
    guides = data.get("guide_count", 0)
    hooks_ok = data.get("hooks_ok", False)
    mcp_ok = data.get("mcp_ok", False)
    data_ok = data.get("data_ok", True)
    stale_facts = data.get("stale_facts", [])

    stale_cls = "warn" if stale > 0 else "ok"
    stale_val = f'<span class="stat-value {stale_cls}">{stale}</span>'
    rows = (
        f'<div class="stat-row">'
        f'<div class="stat-item"><span class="stat-value">{total}</span><span class="stat-label">facts</span></div>'
        f'<div class="stat-item">{stale_val}<span class="stat-label">stale</span></div>'
        f'<div class="stat-item"><span class="stat-value">{today_entries}</span><span class="stat-label">today</span></div>'
        f'<div class="stat-item"><span class="stat-value">{guides}</span><span class="stat-label">guides</span></div>'
        f"</div>"
    )

    def _dot(ok: bool, label: str) -> str:
        dot_cls = "dot-ok" if ok else "dot-off"
        sym = "●" if ok else "○"
        return f'<span class="{dot_cls}">{sym}</span><span class="health-label">{label}</span>'

    health = (
        f'<div class="health-row">'
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
        label = f'{stale} stale fact{"s" if stale != 1 else ""} &#9658;'
        stale_accordion = (
            f'<details class="stale-accordion">'
            f'<summary class="stale-summary">{label}</summary>'
            f'<div style="margin-top:6px">{items_html}</div>'
            f"</details>"
        )

    hints = ["hive s", "hive v", "hive dr"]
    if stale > 0:
        hints = ["hive v  \u2190 stale facts!", "hive s", "hive dr"]
    elif not hooks_ok or not mcp_ok:
        hints = ["hive setup", "hive s", "hive dr"]
    return (
        f'<div class="card">'
        f'<div class="card-header"><span class="card-title">Status</span></div>'
        f'{_cmd_hints(hints)}'
        f'<div class="card-body">{rows}{health}{stale_accordion}</div>'
        f"</div>"
    )


def _render_status_brief_panel(data: dict) -> str:
    stale = data.get("stale", 0)
    total = data.get("total_verified", 0)
    today_entries = data.get("today_entries", 0)
    guides = data.get("guide_count", 0)
    stale_str = f' <span style="color:#e3b341">{stale} stale</span>' if stale > 0 else ""
    return (
        f'<div class="card">'
        f'<div class="card-body">'
        f'<div class="status-brief">'
        f"<span>{total}</span> facts{stale_str} &nbsp;|&nbsp; "
        f"<span>{today_entries}</span> today &nbsp;|&nbsp; "
        f"<span>{guides}</span> guides"
        f"</div></div></div>"
    )


def _render_log_panel(data: dict, limit: int = 0, show_nav: bool = True, see_more_url: str = "") -> str:
    from datetime import date as _date, timedelta

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
            f'</div>'
        )

    rows = ""
    for e in reversed(entries):
        cat = e.get("cat", "")
        cat_cls = f" {cat}" if cat else ""
        rows += (
            f'<div class="log-entry">'
            f'<span class="log-time">{_e(e["time"])}</span>'
            f'<span class="log-text{cat_cls}">{_e(e["text"])}</span>'
            f"</div>"
        )
    if not rows:
        rows = '<div class="empty">No entries for this date</div>'

    see_more_html = ""
    if truncated and see_more_url:
        see_more_html = f'<a class="log-see-more" href="{_e(see_more_url)}">See all {total} entries \u2192</a>'
    elif truncated:
        see_more_html = f'<div class="empty" style="text-align:center;padding-top:6px">{total - limit} more entries \u2014 visit /daily</div>'

    data_panel_attr = ' data-panel="log"' if show_nav else ""
    title = "Today's Log" if not date_str or date_str == _date.today().isoformat() else "Log"
    log_hints = _cmd_hints(['hive r "FACT: ..."', "hive l", "hive l summarize"])
    return (
        f'<div class="card"{data_panel_attr}>'
        f'<div class="card-header">'
        f'<span class="card-title">{title}</span>'
        f'{nav_html}'
        f'<span class="card-meta">{meta}</span>'
        f'</div>'
        f'{log_hints}'
        f'<div class="card-body">{rows}{see_more_html}</div>'
        f"</div>"
    )


def _render_log_brief_panel(data: dict) -> str:
    return _render_log_panel(data, limit=5, show_nav=False)


def _render_log_home_panel(data: dict) -> str:
    """Log panel for 'all' view: 10 entries max with 'see all' link."""
    return _render_log_panel(data, limit=10, show_nav=False, see_more_url="/daily")


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
            f'<div class="todo-item">'
            f'<span class="todo-age {age_cls}">{age_label}</span>'
            f'<span class="todo-text">{_e(text)}</span>'
            f"</div>"
        )
    if not rows:
        rows = '<div class="empty">No open TODOs</div>'
    meta = f"{total}" if total else ""
    return (
        f'<div class="card">'
        f'<div class="card-header"><span class="card-title">Open TODOs</span><span class="card-meta">{meta}</span></div>'
        f'{_cmd_hints(["hive t <text>", "hive todo done <pat>", "hive todo"])}'
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
        f'<div class="card">'
        f'<div class="card-header"><span class="card-title">Recurring</span><span class="card-meta">{meta}</span></div>'
        f'{_cmd_hints(["hive todo repeat daily <task>", "hive todo done <pat>"])}'
        f'<div class="card-body">{rows}</div>'
        f"</div>"
    )


def _render_knowledge_panel(data: dict, guide_limit: int = 0, prompt_limit: int = 0) -> str:
    guides = data.get("guides", [])
    prompts = data.get("prompts", [])
    skills = data.get("skills", [])
    rows = ""

    # Guides: apply limit if specified
    shown_guides = guides if guide_limit <= 0 else guides[:guide_limit]
    hidden_guides = [] if guide_limit <= 0 else guides[guide_limit:]
    for g in shown_guides:
        body = f'<div class="acc-body md">{render_md(g["content"])}</div>'
        rows += (
            f'<div class="accordion">'
            f'<div class="acc-header">'
            f'<span class="acc-toggle">&#9654;</span>'
            f'<span class="acc-name">{_e(g["name"])}</span>'
            f'<span class="acc-type">guide</span>'
            f"</div>{body}</div>"
        )
    if hidden_guides:
        overflow_html = ""
        for g in hidden_guides:
            body = f'<div class="acc-body md">{render_md(g["content"])}</div>'
            overflow_html += (
                f'<div class="accordion">'
                f'<div class="acc-header">'
                f'<span class="acc-toggle">&#9654;</span>'
                f'<span class="acc-name">{_e(g["name"])}</span>'
                f'<span class="acc-type">guide</span>'
                f"</div>{body}</div>"
            )
        n_hidden = len(hidden_guides)
        rows += (
            f'<div class="guide-overflow">{overflow_html}</div>'
            f'<button class="show-more-btn">Show {n_hidden} more guide{"s" if n_hidden != 1 else ""}</button>'
        )

    # Prompts: apply limit if specified
    shown_prompts = prompts if prompt_limit <= 0 else prompts[:prompt_limit]
    hidden_prompts = [] if prompt_limit <= 0 else prompts[prompt_limit:]
    for p in shown_prompts:
        body = f'<div class="acc-body md">{render_md(p["content"])}</div>'
        rows += (
            f'<div class="accordion">'
            f'<div class="acc-header">'
            f'<span class="acc-toggle">&#9654;</span>'
            f'<span class="acc-name">{_e(p["name"])}</span>'
            f'<span class="acc-type">prompt</span>'
            f"</div>{body}</div>"
        )
    if hidden_prompts:
        overflow_html = ""
        for p in hidden_prompts:
            body = f'<div class="acc-body md">{render_md(p["content"])}</div>'
            overflow_html += (
                f'<div class="accordion">'
                f'<div class="acc-header">'
                f'<span class="acc-toggle">&#9654;</span>'
                f'<span class="acc-name">{_e(p["name"])}</span>'
                f'<span class="acc-type">prompt</span>'
                f"</div>{body}</div>"
            )
        n_hidden = len(hidden_prompts)
        rows += (
            f'<div class="guide-overflow">{overflow_html}</div>'
            f'<button class="show-more-btn">Show {n_hidden} more prompt{"s" if n_hidden != 1 else ""}</button>'
        )

    for s in skills:
        rows += (
            f'<div class="accordion">'
            f'<div class="acc-header">'
            f'<span class="acc-toggle" style="color:#30363d">&#8212;</span>'
            f'<span class="acc-name">{_e(s)}</span>'
            f'<span class="acc-type">skill</span>'
            f"</div></div>"
        )
    if not rows:
        rows = '<div class="empty">No knowledge guides yet — hive ke &lt;name&gt;</div>'
    total = len(guides) + len(prompts) + len(skills)
    meta = f"{len(guides)}g / {len(prompts)}p / {len(skills)}sk" if total else ""
    return (
        f'<div class="card">'
        f'<div class="card-header"><span class="card-title">Knowledge</span><span class="card-meta">{meta}</span></div>'
        f'{_cmd_hints(["hive ke <name>", "hive pe <name>", "hive k <name>", "hive rf draft <topic>"])}'
        f'<div class="card-body">{rows}</div>'
        f"</div>"
    )


def _render_knowledge_limited_panel(data: dict) -> str:
    """Knowledge panel with guide/prompt limits for the 'all' overview."""
    return _render_knowledge_panel(data, guide_limit=3, prompt_limit=2)


def _render_memory_panel(data: dict) -> str:
    memory = data.get("memory", "")
    rules = data.get("rules", "")
    mem_html = ""
    for line in memory.splitlines():
        if line.strip():
            mem_html += f'<div class="mem-line">{_e(line)}</div>'
    rules_html = ""
    for line in rules.splitlines():
        if line.strip():
            rules_html += f'<div class="mem-line">{_e(line)}</div>'
    _empty_div = '<div class="empty">Empty</div>'
    mem_section = (
        f'<div class="accordion">'
        f'<div class="acc-header"><span class="acc-toggle">&#9654;</span>'
        f'<span class="acc-name">Working Memory</span></div>'
        f'<div class="acc-body">{mem_html or _empty_div}</div>'
        f"</div>"
    )
    rules_section = (
        f'<div class="accordion">'
        f'<div class="acc-header"><span class="acc-toggle">&#9654;</span>'
        f'<span class="acc-name">Rules</span></div>'
        f'<div class="acc-body">{rules_html or _empty_div}</div>'
        f"</div>"
    )
    return (
        f'<div class="card">'
        f'<div class="card-header"><span class="card-title">Memory</span></div>'
        f'{_cmd_hints(["hive m <fact>", "hive m rm <pat>", "hive rule <text>", "hive e memory"])}'
        f'<div class="card-body">{mem_section}{rules_section}</div>'
        f"</div>"
    )


def _render_notes_panel(data: dict) -> str:
    slots = data.get("slots", [])
    rows = ""
    for s in slots:
        active_cls = " active" if s["active"] else ""
        badge = f'<span class="slot-badge{active_cls}">Slot {s["slot"]}{" \u2605" if s["active"] else ""}</span>'
        body = f'<div class="acc-body md">{badge}<br>{render_md(s["content"])}</div>'
        meta = f'{s["lines"]}L'
        rows += (
            f'<div class="accordion">'
            f'<div class="acc-header">'
            f'<span class="acc-toggle">&#9654;</span>'
            f'<span class="acc-name">Note {s["slot"]}{" (active)" if s["active"] else ""}</span>'
            f'<span class="acc-meta">{meta}</span>'
            f"</div>{body}</div>"
        )
    if not rows:
        rows = '<div class="empty">No notes — hive n</div>'
    meta = f"{len(slots)} slots" if slots else ""
    return (
        f'<div class="card">'
        f'<div class="card-header"><span class="card-title">Notes</span><span class="card-meta">{meta}</span></div>'
        f'{_cmd_hints(["hive n", "hive n show", "hive nc", "hive n.3"])}'
        f'<div class="card-body">{rows}</div>'
        f"</div>"
    )


def _render_stats_panel(data: dict) -> str:
    commands = data.get("commands", [])
    today_map = data.get("today", {})
    week_map = data.get("week", {})
    total_days = data.get("total_days", 0)
    curr_streak = data.get("curr_streak", 0)
    longest_streak = data.get("longest_streak", 0)

    streak_html = ""
    if total_days > 0:
        streak_html = (
            f'<div class="stat-row" style="margin-bottom:12px">'
            f'<div class="stat-item"><span class="stat-value">{curr_streak}</span><span class="stat-label">curr streak</span></div>'
            f'<div class="stat-item"><span class="stat-value">{longest_streak}</span><span class="stat-label">best streak</span></div>'
            f'<div class="stat-item"><span class="stat-value">{total_days}</span><span class="stat-label">days active</span></div>'
            f'</div>'
        )

    rows = ""
    if commands:
        rows = (
            '<table class="stats-table">'
            "<thead><tr><th>Command</th><th>Today</th><th>Week</th><th>All-time</th></tr></thead><tbody>"
        )
        for name, total in commands:
            t = today_map.get(name, 0)
            w = week_map.get(name, 0)
            rows += f"<tr><td>{_e(name)}</td><td>{t or ''}</td><td>{w or ''}</td><td>{total}</td></tr>"
        rows += "</tbody></table>"
    else:
        rows = '<div class="empty">No usage data yet</div>'
    meta = f"{total_days} days tracked" if total_days else ""
    return (
        f'<div class="card">'
        f'<div class="card-header"><span class="card-title">Usage Stats</span><span class="card-meta">{meta}</span></div>'
        f'{_cmd_hints(["hive st", "hive st -p <project>", "hive st yesterday"])}'
        f'<div class="card-body">{streak_html}{rows}</div>'
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
        f'<div class="card">'
        f'<div class="card-header"><span class="card-title">Projects</span><span class="card-meta">{_e(proc_str)}</span></div>'
        f'{_cmd_hints(["hive ps", "hive go", "hive su"])}'
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
        f'<div class="card">'
        f'<div class="card-header"><span class="card-title">Recent Insights</span><span class="card-meta">past 7d</span></div>'
        f'{facts_hints}'
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
        f'<div class="card">'
        f"<div class=\"card-header\"><span class=\"card-title\">Today's Focus</span></div>"
        f'{_cmd_hints(["hive su", "hive todo", "hive todo done <pat>"])}'
        f'<div class="card-body">{rows}</div>'
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
    "memory": (_get_memory_data, _render_memory_panel),
    "notes": (_get_notes_data, _render_notes_panel),
    "stats": (_get_stats_data, _render_stats_panel),
    "ps": (_get_ps_data, _render_ps_panel),
    "facts": (_get_recent_facts_data, _render_recent_facts_panel),
    "standup": (_get_standup_data, _render_standup_panel),
}

# ---- View definitions ----

VIEWS: dict[str, dict] = {
    "all": {
        "path": "/",
        "title": "All",
        "rows": [
            ["status", "ps"],
            ["log-home"],
            ["todos", "recurring"],
            ["knowledge-limited", "notes"],
            ["memory"],
        ],
    },
    "daily": {
        "path": "/daily",
        "title": "Daily",
        "rows": [
            ["status", "ps"],
            ["standup"],
            ["log"],
            ["todos", "recurring"],
        ],
    },
    "dev": {
        "path": "/dev",
        "title": "Dev",
        "rows": [
            ["status-brief"],
            ["knowledge", "memory"],
            ["facts"],
        ],
    },
    "simple": {
        "path": "/simple",
        "title": "Simple",
        "rows": [
            ["status-brief"],
            ["log-brief", "todos-brief"],
        ],
    },
    "stats": {
        "path": "/stats",
        "title": "Stats",
        "rows": [["stats", "ps"]],
    },
    "know": {
        "path": "/know",
        "title": "Know",
        "rows": [["knowledge"]],
    },
    "mem": {
        "path": "/mem",
        "title": "Mem",
        "rows": [["memory"]],
    },
    "notes": {
        "path": "/notes",
        "title": "Notes",
        "rows": [["notes"]],
    },
}

_PATH_TO_VIEW: dict[str, str] = {v["path"]: k for k, v in VIEWS.items()}


# ---- Fragment + page rendering ----


def _render_panel_safe(name: str, extra_params: dict | None = None) -> str:
    if name not in PANELS:
        return f'<div class="card"><div class="card-body"><div class="empty">Unknown panel: {_e(name)}</div></div></div>'
    data_fn, render_fn = PANELS[name]
    try:
        # Log panels accept an optional date param
        if name in ("log", "log-brief", "log-home") and extra_params and extra_params.get("log_date"):
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
        else:
            cols = "".join(_render_panel_safe(name, extra_params) for name in row)
            parts.append(f'<div class="grid-2">{cols}</div>')
    return "\n".join(parts)


def render_page(view_name: str, port: int) -> str:
    nav_tabs = ""
    for vname, vdef in VIEWS.items():
        active_cls = " active" if vname == view_name else ""
        nav_tabs += f'<a class="nav-tab{active_cls}" href="{_e(vdef["path"])}">{_e(vdef["title"])}</a>'

    content = render_fragment(view_name)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>hive \u2014 {_e(VIEWS.get(view_name, {}).get("title", view_name))}</title>
<style>{_CSS}</style>
</head>
<body data-view="{_e(view_name)}" data-port="{port}">
<nav>
  <span class="nav-brand">hive</span>
  {nav_tabs}
  <div class="nav-right">
    <input id="search-input" type="text" placeholder="search memory\u2026" autocomplete="off">
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
  <div id="main-content">{content}</div>
</main>
<div id="search-overlay">
  <div class="search-panel">
    <div class="search-header">
      <span class="search-title">Search Results</span>
      <button id="search-close" class="search-close">&#10005;</button>
    </div>
    <div id="search-body" class="search-body"></div>
  </div>
</div>
<script>{_JS}</script>
</body>
</html>"""


# ---- HTTP handler ----


class _HiveHandler(BaseHTTPRequestHandler):
    server_port: int = DEFAULT_PORT

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/fragment":
            qs = parse_qs(parsed.query)
            view_name = qs.get("view", ["all"])[0]
            log_date = qs.get("log_date", [None])[0] or qs.get("date", [None])[0]

            # Special case: view=log returns just the log panel (for date navigation)
            if view_name == "log":
                data = _get_log_data(log_date)
                body = _render_log_panel(data).encode()
            else:
                if view_name not in VIEWS:
                    view_name = "all"
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
                    from keephive.storage import fts_search

                    results = fts_search(query, limit=20)
                except Exception:
                    pass
            resp_data = json.dumps({"results": results}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors()
            self.send_header("Content-Length", str(len(resp_data)))
            self.end_headers()
            self.wfile.write(resp_data)
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
        if self.path != "/ui-feedback":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
        except Exception:
            self.send_response(400)
            self._cors()
            self.end_headers()
            return

        try:
            from keephive.storage import ui_queue_path

            ui_queue_path().write_text(json.dumps(data, indent=2))
        except Exception:
            pass

        resp = json.dumps({"ok": True}).encode()
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

    def log_message(self, format: str, *args) -> None:  # noqa: A002
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
                    Path(p).relative_to(src_dir)
                    for p in now
                    if now[p] != snapshot.get(p, 0)
                ]
                changed += [
                    Path(p).relative_to(src_dir)
                    for p in snapshot
                    if p not in now
                ]
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
