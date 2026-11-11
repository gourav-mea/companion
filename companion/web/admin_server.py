"""Admin web UI: account management, plus an optional per-user Insights view.

By default this module only touches account metadata (identity.repository) and
the enrollment plumbing -- admins manage accounts, not content. The ONE
exception is the Insights knowledge graph: when config.ADMIN_INSIGHTS_ENABLED
is true, the `/admin/users/<id>/knowledge` endpoint lazily imports
memory_notes to show what the agent has learned about a user. That deliberately
relaxes the "accounts, not content" boundary, so it is gated behind the flag
(and auth); turn the flag off to restore the strict separation.
"""
import hashlib
import http.server
import json
import os
import secrets
import socketserver
import threading
import time
import urllib.parse

from .. import config
from ..identity import repository

_sessions = {}   # token -> expiry epoch


def _hash_password(password, salt):
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def set_admin_password(password):
    salt = secrets.token_hex(16)
    with open(config.ADMIN_PASSWORD_FILE, "w") as f:
        f.write(f"{salt}:{_hash_password(password, salt)}")


def _check_password(password):
    try:
        salt, digest = open(config.ADMIN_PASSWORD_FILE).read().strip().split(":", 1)
    except Exception:
        return False
    return secrets.compare_digest(_hash_password(password, salt), digest)


def _new_session():
    token = secrets.token_urlsafe(24)
    _sessions[token] = time.time() + config.ADMIN_SESSION_SECONDS
    return token


def _session_valid(token):
    expiry = _sessions.get(token)
    if not expiry or expiry < time.time():
        _sessions.pop(token, None)
        return False
    return True


ADMIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Companion Admin</title>
<style>
body{font-family:Arial,sans-serif;background:#0a2e5c;color:#eaf2fb;padding:24px;}
h1{color:#28b9e8;font-size:20px;}
input,select{padding:6px;margin:4px 0;border-radius:4px;border:1px solid #39506e;}
button{padding:6px 12px;border-radius:4px;border:none;background:#28b9e8;color:#0a2e5c;
font-weight:bold;cursor:pointer;margin:2px;}
table{border-collapse:collapse;width:100%;margin-top:16px;}
td,th{border:1px solid #39506e;padding:8px;text-align:left;font-size:14px;}
#login{max-width:320px;}
.hidden{display:none;}
</style></head>
<body>
<div id="login">
  <h1>Admin login</h1>
  <input id="password" type="password" placeholder="Admin password">
  <button onclick="login()">Log in</button>
  <div id="loginError" style="color:#e67e22;"></div>
</div>
<div id="panel" class="hidden">
  <h1>Users</h1>
  <table id="users"><thead><tr>
    <th>Name</th><th>Role</th><th>Active</th><th>Samples</th><th>Last active</th><th>Actions</th>
  </tr></thead><tbody></tbody></table>
  <h1>Add user (account only -- enroll their voice separately)</h1>
  <input id="newName" placeholder="Display name">
  <select id="newRole"><option value="user">user</option><option value="admin">admin</option></select>
  <button onclick="addUser()">Create</button>
</div>
<script>
async function api(path, opts) {
  const r = await fetch(path, Object.assign({credentials: "same-origin"}, opts || {}));
  return r.json();
}
async function login() {
  const password = document.getElementById("password").value;
  const res = await api("/admin/login", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({password})});
  if (res.ok) { document.getElementById("login").classList.add("hidden");
    document.getElementById("panel").classList.remove("hidden"); loadUsers(); }
  else { document.getElementById("loginError").textContent = "Invalid password"; }
}
async function loadUsers() {
  const res = await api("/admin/users");
  const tbody = document.querySelector("#users tbody");
  tbody.innerHTML = "";
  (res.users || []).forEach(u => {
    const tr = document.createElement("tr");
    const lastActive = u.last_active_ts ? new Date(u.last_active_ts * 1000).toLocaleString() : "never";
    tr.innerHTML = `<td>${u.display_name}</td><td>${u.role}</td><td>${u.active ? "yes" : "no"}</td>
      <td>${u.sample_count}</td><td>${lastActive}</td><td></td>`;
    const actions = tr.lastElementChild;
    const mkBtn = (label, fn) => { const b = document.createElement("button");
      b.textContent = label; b.onclick = fn; actions.appendChild(b); };
    mkBtn(u.active ? "Deactivate" : "Activate", () => updateUser(u.id, {active: u.active ? 0 : 1}));
    mkBtn(u.role === "admin" ? "Demote" : "Promote",
      () => updateUser(u.id, {role: u.role === "admin" ? "user" : "admin"}));
    mkBtn("Insights", () => window.open("/admin/insights?user=" + u.id +
      "&name=" + encodeURIComponent(u.display_name), "_blank"));
    mkBtn("Re-enroll voice", () => enrollUser(u.id));
    mkBtn("Delete", () => deleteUser(u.id));
    tbody.appendChild(tr);
  });
}
async function updateUser(id, body) {
  await api(`/admin/users/${id}`, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)});
  loadUsers();
}
async function deleteUser(id) {
  if (!confirm("Delete this account? This removes their voice samples too.")) return;
  await api(`/admin/users/${id}`, {method: "DELETE"});
  loadUsers();
}
async function enrollUser(id) {
  alert("Have the person speak into the device microphone now.");
  const res = await api(`/admin/users/${id}/enroll`, {method: "POST"});
  alert(res.message || "Enrollment finished.");
  loadUsers();
}
async function addUser() {
  const display_name = document.getElementById("newName").value.trim();
  if (!display_name) return;
  const role = document.getElementById("newRole").value;
  await api("/admin/users", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({display_name, role})});
  document.getElementById("newName").value = "";
  loadUsers();
}
</script>
</body></html>
"""


INSIGHTS_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Insights</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:Arial,Helvetica,sans-serif;background:#0a2e5c;color:#eaf2fb;padding:20px;}
h1{color:#28b9e8;font-size:20px;} h2{color:#8fd0ff;font-size:14px;letter-spacing:1px;
text-transform:uppercase;margin:18px 0 8px;}
#err{color:#e67e22;margin:12px 0;}
#stats{display:flex;gap:12px;flex-wrap:wrap;margin:10px 0 4px;}
.stat{background:#13396b;border:1px solid #2a4a7a;border-radius:10px;padding:10px 16px;min-width:120px;}
.stat .v{font-size:22px;color:#28b9e8;font-weight:bold;} .stat .l{font-size:12px;color:#9fc4e8;}
#wrap{display:flex;gap:20px;flex-wrap:wrap;align-items:flex-start;}
#graphbox{flex:1 1 520px;background:#081f3f;border:1px solid #244a7c;border-radius:12px;padding:8px;min-width:320px;}
#side{flex:1 1 260px;min-width:240px;}
ul{list-style:none;} li{background:#0d336b;border:1px solid #244a7c;border-radius:8px;
padding:8px 10px;margin-bottom:6px;font-size:13px;}
li.muted{color:#6f93bd;background:none;border:none;}
.src{font-size:10px;padding:2px 6px;border-radius:8px;margin-left:4px;}
.src.explicit{background:#14415f;color:#28b9e8;} .src.auto{background:#143f2a;color:#2ecc71;}
.when{color:#6f93bd;font-size:11px;margin-right:6px;}
#timeline{margin-top:4px;}
.tl{display:flex;gap:8px;align-items:flex-start;background:#0d336b;border:1px solid #244a7c;
border-radius:8px;padding:8px 10px;margin-bottom:6px;font-size:13px;}
.tl .ic{font-size:15px;} .tl .txt{flex:1;} .tl.corrected{border-color:#7a3b2a;}
.tl i{color:#c98;}
.legend{font-size:12px;color:#9fc4e8;margin:6px 0 2px;display:flex;gap:16px;flex-wrap:wrap;}
.legend span{display:inline-flex;align-items:center;gap:5px;}
.key{width:10px;height:10px;border-radius:50%;display:inline-block;}
a{color:#28b9e8;}
</style></head>
<body>
<a href="/admin">&larr; back to admin</a>
<h1>What the agent has learned about <span id="who">...</span></h1>
<div id="err"></div>
<div id="stats"></div>
<div class="legend">
  <span><i class="key" style="background:#28b9e8"></i>told explicitly</span>
  <span><i class="key" style="background:#2ecc71"></i>learned automatically</span>
  <span><i class="key" style="background:#c0392b"></i>corrected / superseded</span>
</div>
<div id="wrap">
  <div id="graphbox"><div id="graph"></div></div>
  <div id="side">
    <h2>Traits</h2><ul id="traits"></ul>
    <h2>Episodes</h2><ul id="episodes"></ul>
  </div>
</div>
<h2>Learning timeline</h2>
<div id="timeline"></div>
<script>
var params=new URLSearchParams(location.search);
var uid=params.get("user"); var uname=params.get("name")||("user "+uid);
var firstName=uname.split(" ")[0];
document.getElementById("who").textContent=uname;

fetch("/admin/users/"+uid+"/knowledge",{credentials:"same-origin"}).then(function(r){
  if(r.status===401) throw new Error("Please log in on the admin page first, then reopen Insights.");
  if(r.status===403) throw new Error("Insights are disabled (config.ADMIN_INSIGHTS_ENABLED = False).");
  return r.json();
}).then(function(d){ buildStats(d); buildGraph(d); buildPanels(d); buildTimeline(d); })
  .catch(function(e){ document.getElementById("err").textContent=e.message; });

function esc(s){return (s||"").replace(/[&<>]/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;"}[c];});}
function shorten(s,n){s=s||"";return s.length>n?s.slice(0,n-1)+"\\u2026":s;}
function dt(ts){ if(!ts) return "-"; var d=new Date(ts*1000);
  return d.toLocaleDateString(undefined,{month:"short",day:"numeric"}); }
function anchor(x,cx){return x<cx-20?"end":(x>cx+20?"start":"middle");}
function line(x1,y1,x2,y2,c,w,dash){return '<line x1="'+x1+'" y1="'+y1+'" x2="'+x2+'" y2="'+y2+
  '" stroke="'+c+'" stroke-width="'+w+'"'+(dash?' stroke-dasharray="4 4"':'')+'/>';}
function circ(x,y,r,fill,stroke,dash){return '<circle cx="'+x+'" cy="'+y+'" r="'+r+'" fill="'+fill+
  '" stroke="'+stroke+'" stroke-width="2"'+(dash?' stroke-dasharray="3 3"':'')+'/>';}
function txt(x,y,s,c,sz,an,w){return '<text x="'+x+'" y="'+y+'" fill="'+c+'" font-size="'+(sz||11)+
  '" text-anchor="'+(an||"middle")+'"'+(w?' font-weight="'+w+'"':'')+'>'+esc(s)+'</text>';}

function buildStats(d){
  var f=d.facts||[]; var active=f.filter(function(x){return x.active;});
  var last=f.reduce(function(m,x){return Math.max(m,x.created_ts||0);},0);
  function stat(v,l){return '<div class="stat"><div class="v">'+v+'</div><div class="l">'+l+'</div></div>';}
  document.getElementById("stats").innerHTML=
    stat(active.length,"facts known")+stat((d.conflicts||[]).length,"corrections")+
    stat((d.episodes||[]).length,"episodes")+stat(last?dt(last):"-","last learned");
}

function buildGraph(d){
  var facts=d.facts||[]; var slots={};
  facts.forEach(function(f){ if(f.attribute){ (slots[f.attribute]=slots[f.attribute]||[]).push(f); } });
  var keys=Object.keys(slots).sort();
  var W=760,H=520,cx=W/2,cy=H/2,R=160,RS=255;
  var edges="",nodes=""; var n=keys.length||1;
  keys.forEach(function(k,i){
    var ang=(-90+i*360/n)*Math.PI/180;
    var x=cx+R*Math.cos(ang), y=cy+R*Math.sin(ang);
    var arr=slots[k];
    var active=arr.filter(function(f){return f.active;})[0]||arr[arr.length-1];
    var sup=arr.filter(function(f){return !f.active;});
    edges+=line(cx,cy,x,y,"#2a4a7a",1.5);
    sup.forEach(function(sf,si){
      var ox=cx+(RS+si*22)*Math.cos(ang), oy=cy+(RS+si*22)*Math.sin(ang);
      edges+=line(ox,oy,x,y,"#c0392b",1,true);
      nodes+=circ(ox,oy,5,"#3a2030","#c0392b",true)+"<title>was: "+esc(sf.content)+"</title>";
      nodes+=txt(ox,oy+(oy<cy?-9:15),shorten(sf.content,20),"#c98",10,anchor(ox,cx));
    });
    var col=active.source==="explicit"?"#28b9e8":"#2ecc71";
    nodes+=circ(x,y,9,"#13396b",col)+"<title>"+esc(active.content)+" ("+active.source+
      ", conf "+active.confidence+")</title>";
    nodes+=txt(x,y-15,k.replace(/_/g," "),"#8fd0ff",12,"middle","bold");
    nodes+=txt(x,y+22,shorten(active.content,24),"#eaf2fb",12,anchor(x,cx));
  });
  var svg='<svg viewBox="0 0 '+W+' '+H+'" width="100%">'+edges+
    circ(cx,cy,30,"#28b9e8","#0a2e5c")+txt(cx,cy+5,firstName,"#0a2e5c",14,"middle","bold")+
    nodes+'</svg>';
  document.getElementById("graph").innerHTML=svg;
}

function buildPanels(d){
  var traits=(d.facts||[]).filter(function(f){return !f.attribute && f.active;});
  document.getElementById("traits").innerHTML = traits.length? traits.map(function(f){
    return '<li>'+esc(f.content)+'<span class="src '+(f.source||"explicit")+'">'+(f.source||"explicit")+
      '</span></li>';}).join("") : '<li class="muted">none</li>';
  var eps=d.episodes||[];
  document.getElementById("episodes").innerHTML = eps.length? eps.map(function(e){
    return '<li><span class="when">'+dt(e.created_ts)+'</span>'+esc(e.summary_text)+'</li>';}).join("")
    : '<li class="muted">none</li>';
}

function buildTimeline(d){
  var ev=[];
  (d.facts||[]).forEach(function(f){ ev.push({t:f.created_ts,type:"learned",
    text:f.content,active:f.active}); });
  (d.conflicts||[]).forEach(function(c){ ev.push({t:c.created_ts,type:"corrected",
    text:(c.attribute?c.attribute.replace(/_/g," ")+": ":"")+shorten(c.old_text,28)+" \\u2192 "+
    shorten(c.new_text,28)}); });
  (d.episodes||[]).forEach(function(e){ ev.push({t:e.created_ts,type:"episode",text:e.summary_text}); });
  ev.sort(function(a,b){return (b.t||0)-(a.t||0);});
  var icon={learned:"\\ud83e\\udde0",corrected:"\\u270f\\ufe0f",episode:"\\ud83d\\udcac"};
  document.getElementById("timeline").innerHTML = ev.length? ev.map(function(e){
    return '<div class="tl '+e.type+'"><span class="ic">'+icon[e.type]+'</span>'+
      '<span class="when">'+dt(e.t)+'</span><span class="txt">'+esc(e.text)+
      (e.type==="learned"&&!e.active?' <i>(superseded)</i>':'')+'</span></div>';}).join("")
    : '<div class="tl">Nothing learned yet.</div>';
}
</script>
</body></html>
"""


class AdminHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass

    def _cookie_token(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("session="):
                return part.split("=", 1)[1]
        return None

    def _authed(self):
        token = self._cookie_token()
        return token is not None and _session_valid(token)

    def _send_json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code, html):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return {}

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/admin", "/admin/"):
            return self._send_html(200, ADMIN_HTML)
        if path == "/admin/insights":
            return self._send_html(200, INSIGHTS_HTML)   # shell only; its data endpoint is authed
        if path == "/admin/users":
            if not self._authed():
                return self._send_json(401, {"error": "unauthorized"})
            return self._send_json(200, {"users": repository.list_users()})
        parts = [p for p in path.split("/") if p]
        if len(parts) == 4 and parts[:2] == ["admin", "users"] and parts[3] == "knowledge":
            if not self._authed():
                return self._send_json(401, {"error": "unauthorized"})
            if not config.ADMIN_INSIGHTS_ENABLED:
                return self._send_json(403, {"error": "insights disabled"})
            # The one place admin reads learned content -- gated + lazy-imported.
            from ..skills import memory_notes
            try:
                return self._send_json(200, memory_notes.knowledge_snapshot(int(parts[2])))
            except Exception as exc:
                return self._send_json(500, {"error": str(exc)})
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        data = self._read_json()
        if path == "/admin/login":
            if _check_password(data.get("password", "")):
                token = _new_session()
                body = json.dumps({"ok": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Set-Cookie", f"session={token}; HttpOnly; Path=/")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return self._send_json(401, {"error": "invalid password"})
        if not self._authed():
            return self._send_json(401, {"error": "unauthorized"})
        parts = [p for p in path.split("/") if p]
        if path == "/admin/users":
            name = (data.get("display_name") or "").strip()
            if not name:
                return self._send_json(400, {"error": "display_name required"})
            user_id = repository.create_user(name, role=data.get("role", "user"))
            return self._send_json(200, {"id": user_id})
        if len(parts) == 4 and parts[:2] == ["admin", "users"] and parts[3] == "enroll":
            return self._enroll(int(parts[2]))
        if len(parts) == 3 and parts[:2] == ["admin", "users"]:
            repository.update_user(int(parts[2]), display_name=data.get("display_name"),
                                   role=data.get("role"), active=data.get("active"))
            return self._send_json(200, {"ok": True})
        self._send_json(404, {"error": "not found"})

    def _enroll(self, user_id):
        user = repository.get_user(user_id)
        if not user:
            return self._send_json(404, {"error": "user not found"})
        from ..audio.capture import mic_session, record
        from ..audio.playback import speak
        from ..identity.enrollment import enroll_user

        def record_fn(seconds, tag):
            # The wake-word listener streams the mic continuously; without
            # taking ownership this records from a busy device and gets nothing.
            with mic_session():
                return record(seconds, denoise=False, tag=tag, stop_on_silence=True)

        ok, msg = enroll_user(user["display_name"], record_fn, speak_fn=speak)
        return self._send_json(200, {"ok": ok, "message": msg})

    def do_DELETE(self):
        if not self._authed():
            return self._send_json(401, {"error": "unauthorized"})
        parts = [p for p in urllib.parse.urlparse(self.path).path.split("/") if p]
        if len(parts) == 3 and parts[:2] == ["admin", "users"]:
            repository.delete_user(int(parts[2]))
            return self._send_json(200, {"ok": True})
        self._send_json(404, {"error": "not found"})


class _ThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True   # rebind immediately after a restart (no TIME_WAIT "won't start")
    daemon_threads = True        # requests never block process shutdown


def start_admin_server():
    if not os.path.exists(config.ADMIN_PASSWORD_FILE):
        print("admin UI: no admin password set -- run `talk.py set-admin-password <password>`")
        return
    try:
        httpd = _ThreadingServer(("", config.ADMIN_PORT), AdminHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        print(f"admin UI: http://localhost:{config.ADMIN_PORT}/admin")
    except Exception as exc:
        print(f"admin server not started ({exc})")
