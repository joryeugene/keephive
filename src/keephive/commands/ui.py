"""hive ui: UI feedback queue and bookmarklet installer.

hive ui           Show pending UI feedback queue
hive ui-install   Print bookmarklet URL (drag to bookmarks bar)
hive ui-clear     Clear pending feedback
"""

from __future__ import annotations

import json
import sys
from urllib.parse import quote

from keephive.commands.serve import DEFAULT_PORT

# Bookmarklet source (runs on click; posts element context to hive serve)
_BOOKMARKLET_SRC = """(function(){
var PORT=3847;
var BASE='http://localhost:'+PORT;
var overlay=document.createElement('div');
overlay.style.cssText='position:fixed;top:0;left:0;width:100%;height:100%;z-index:2147483647;cursor:crosshair;background:rgba(0,0,0,0.08)';
var hi=null,origOutline='';
overlay.addEventListener('mousemove',function(e){
  overlay.style.pointerEvents='none';
  var el=document.elementFromPoint(e.clientX,e.clientY);
  overlay.style.pointerEvents='';
  if(!el||el===overlay)return;
  if(hi&&hi!==el){hi.style.outline=origOutline;}
  origOutline=el.style.outline;
  el.style.outline='2px solid #f97316';
  hi=el;
});
overlay.addEventListener('click',function(e){
  e.preventDefault();e.stopPropagation();
  overlay.style.pointerEvents='none';
  var el=document.elementFromPoint(e.clientX,e.clientY);
  overlay.style.pointerEvents='';
  if(hi)hi.style.outline=origOutline;
  document.body.removeChild(overlay);
  if(!el||el===overlay)return;
  var sel=getSelector(el);
  var outerHTML=el.outerHTML.substring(0,600);
  var styles=getStyles(el);
  showDialog(sel,outerHTML,styles);
});
document.addEventListener('keydown',function esc(e){
  if(e.key==='Escape'){
    if(hi)hi.style.outline=origOutline;
    if(document.body.contains(overlay))document.body.removeChild(overlay);
    document.removeEventListener('keydown',esc);
  }
},true);
document.body.appendChild(overlay);
function getSelector(el){
  if(el.id)return '#'+el.id;
  var parts=[],cur=el;
  for(var i=0;i<4&&cur&&cur!==document.body;i++){
    var p=cur.tagName.toLowerCase();
    if(cur.className&&typeof cur.className==='string'){
      var cls=cur.className.trim().split(/\\s+/).slice(0,2).join('.');
      if(cls)p+='.'+cls;
    }
    parts.unshift(p);cur=cur.parentElement;
  }
  return parts.join(' > ');
}
function getStyles(el){
  var cs=window.getComputedStyle(el);
  var keys=['padding','margin','color','background-color','font-size','font-weight','display','border-radius','gap'];
  return keys.map(function(k){return k+': '+cs.getPropertyValue(k);}).join('\\n');
}
function showDialog(sel,outerHTML,styles){
  var d=document.createElement('div');
  d.style.cssText='position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:2147483647;background:#1c2128;border:1px solid #30363d;border-radius:8px;padding:16px;width:500px;max-width:92vw;font-family:monospace;box-shadow:0 8px 32px rgba(0,0,0,0.6)';
  var ctx='[UI Feedback]\\nPage: '+window.location.href+'\\nElement: '+sel+'\\nHTML: '+outerHTML+'\\nStyles:\\n'+styles+'\\n\\nNote: ';
  d.innerHTML='<div style="color:#f0f6fc;font-size:13px;margin-bottom:8px;font-family:sans-serif;font-weight:600">UI Feedback</div>'
    +'<div style="color:#8b949e;font-size:11px;margin-bottom:6px;font-family:sans-serif">Element: '+sel.replace(/</g,'&lt;')+'</div>'
    +'<textarea id="hf-text" style="width:100%;height:130px;background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:4px;padding:8px;font-family:monospace;font-size:11px;resize:vertical">'+ctx.replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</textarea>'
    +'<div style="margin-top:8px;display:flex;gap:8px">'
    +'<button id="hf-submit" style="flex:1;background:#238636;color:#fff;border:none;border-radius:4px;padding:6px 12px;cursor:pointer;font-size:13px;font-family:sans-serif">Queue for Claude</button>'
    +'<button id="hf-cancel" style="background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:4px;padding:6px 12px;cursor:pointer;font-size:13px;font-family:sans-serif">Cancel</button>'
    +'</div>';
  document.body.appendChild(d);
  var ta=d.querySelector('#hf-text');
  ta.focus();ta.setSelectionRange(ta.value.length,ta.value.length);
  function doSubmit(){
    fetch(BASE+'/ui-feedback',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({page:window.location.href,selector:sel,html:outerHTML,styles:styles,note:ta.value})
    }).then(function(){
      d.innerHTML='<div style="color:#3fb950;padding:10px;font-family:sans-serif;font-size:13px">Queued \u2014 type anything in Claude Code to inject</div>';
      setTimeout(function(){if(document.body.contains(d))document.body.removeChild(d);},2000);
    }).catch(function(){
      d.innerHTML='<div style="color:#f85149;padding:10px;font-family:sans-serif;font-size:13px">Failed \u2014 is hive serve running on port '+PORT+'?</div>';
      setTimeout(function(){if(document.body.contains(d))document.body.removeChild(d);},3000);
    });
  }
  d.querySelector('#hf-submit').addEventListener('click',doSubmit);
  d.querySelector('#hf-cancel').addEventListener('click',function(){document.body.removeChild(d);});
  ta.addEventListener('keydown',function(e){
    if(e.key==='Enter'&&(e.metaKey||e.ctrlKey)){e.preventDefault();doSubmit();}
    if(e.key==='Escape'){e.preventDefault();document.body.removeChild(d);}
  });
}
})();"""


def _build_bookmarklet_url() -> str:
    """Build the javascript: URL for the bookmarklet."""
    # Minify: remove comments, collapse whitespace across lines
    src = _BOOKMARKLET_SRC.strip()
    return "javascript:" + quote(src, safe="!~*'()")


def cmd_ui(args: list[str]) -> None:
    """hive ui — show pending UI feedback queue."""
    from keephive.storage import ui_queue_path

    if args and args[0] in ("install", "--install"):
        cmd_ui_install([])
        return
    if args and args[0] in ("clear", "--clear"):
        cmd_ui_clear([])
        return

    q = ui_queue_path()
    if not q.exists():
        print("No pending UI feedback in queue.")
        print("  Use the bookmarklet on hive serve or any page to capture feedback.")
        print("  hive ui-install  — print bookmarklet URL")
        return

    try:
        data = json.loads(q.read_text())
    except Exception:
        print("Queue file exists but could not be read.")
        return

    page = data.get("page", "?")
    selector = data.get("selector", "?")
    note = data.get("note", "")

    print("Pending UI feedback:")
    print(f"  Page:     {page}")
    print(f"  Element:  {selector}")
    if note:
        # Show just the note part (after "Note: ")
        note_display = note.split("\n\nNote: ")[-1].strip() if "\n\nNote: " in note else note
        if note_display:
            print(f"  Note:     {note_display[:120]}")
    print()
    print("This will be prepended to your next Claude Code prompt.")
    print("  hive ui-clear  — discard without using")


def cmd_ui_install(args: list[str]) -> None:
    """hive ui-install — print bookmarklet drag URL."""
    url = _build_bookmarklet_url()
    print()
    print("Bookmarklet installer")
    print("─" * 50)
    print()
    print("1. Copy the URL below")
    print("2. Create a new bookmark in your browser")
    print("3. Paste as the bookmark URL (not as address bar)")
    print("4. Name it 'hive ui' or similar")
    print()
    print("Then: click the bookmark on any page (including hive serve)")
    print("      hover over elements, click one, type feedback, submit")
    print("      your next Claude Code prompt will include the context")
    print()
    print("javascript: URL (long — paste as bookmark URL, not address bar):")
    print()
    print(url)
    print()
    from keephive.output import copy_to_clipboard

    if copy_to_clipboard(url):
        print("  (copied to clipboard)")
        print()


def cmd_ui_clear(args: list[str]) -> None:
    """hive ui-clear — discard pending UI feedback."""
    from keephive.storage import ui_queue_path

    q = ui_queue_path()
    if q.exists():
        q.unlink()
        print("UI feedback queue cleared.")
    else:
        print("Queue is already empty.")
