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
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Companion Admin</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:-apple-system,Segoe UI,Arial,sans-serif;background:#071d3a;color:#eaf2fb;
min-height:100vh;padding:0 0 40px;}
.top{background:linear-gradient(90deg,#0a2e5c,#0d3b78);padding:18px 26px;display:flex;
align-items:center;gap:12px;box-shadow:0 2px 14px rgba(0,0,0,.35);}
.top .logo{width:30px;height:30px;border-radius:50%;background:radial-gradient(circle,#5ad1ff,#1b86c9);}
.top h1{font-size:19px;color:#eaf2fb;letter-spacing:.5px;}
.wrap{max-width:900px;margin:26px auto;padding:0 20px;}
.card{background:#0c2b55;border:1px solid #1d3f70;border-radius:14px;padding:22px;margin-bottom:20px;
box-shadow:0 6px 20px rgba(0,0,0,.25);}
h2{color:#28b9e8;font-size:15px;letter-spacing:1px;text-transform:uppercase;margin-bottom:14px;}
input,select{padding:9px 11px;margin:4px 6px 4px 0;border-radius:8px;border:1px solid #2a4a7a;
background:#071d3a;color:#eaf2fb;font-size:14px;}
input:focus,select:focus{outline:none;border-color:#28b9e8;}
button{padding:7px 13px;border-radius:8px;border:none;background:#28b9e8;color:#042038;
font-weight:bold;cursor:pointer;margin:2px;font-size:13px;transition:transform .08s,background .15s;}
button:hover{background:#4ccbf5;transform:translateY(-1px);}
button.ghost{background:#13396b;color:#cfe3f5;border:1px solid #2a4a7a;}
button.ghost:hover{background:#1b4b86;}
button.danger{background:#c0392b;color:#fff;} button.danger:hover{background:#e04b3a;}
table{border-collapse:collapse;width:100%;margin-top:6px;}
th{color:#8fb5e0;font-size:12px;text-transform:uppercase;letter-spacing:1px;text-align:left;
padding:8px 10px;border-bottom:2px solid #1d3f70;}
td{padding:10px;border-bottom:1px solid #15335f;font-size:14px;vertical-align:middle;}
tr:hover td{background:#0e2f5e;}
.pill{font-size:11px;padding:3px 9px;border-radius:12px;background:#13396b;color:#8fd0ff;}
.pill.on{background:#143f2a;color:#2ecc71;} .pill.off{background:#3a1f1f;color:#e08a7a;}
#login{max-width:340px;margin:60px auto;}
.hidden{display:none;}
#loginError{color:#e67e22;margin-top:8px;font-size:14px;}
.actions{display:flex;flex-wrap:wrap;gap:4px;}
</style></head>
<body>
<div class="top"><div class="logo"></div><h1>Companion Admin</h1></div>

<div id="login" class="card">
  <h2>Admin login</h2>
  <input id="password" type="password" placeholder="Admin password" style="width:220px">
  <button onclick="login()">Log in</button>
  <div id="loginError"></div>
</div>

<div id="panel" class="wrap hidden">
  <div class="card">
    <h2>Users</h2>
    <table id="users"><thead><tr>
      <th>Name</th><th>Role</th><th>Active</th><th>Samples</th><th>Last active</th><th>Actions</th>
    </tr></thead><tbody></tbody></table>
  </div>
  <div class="card">
    <h2>Add user <span style="color:#6f93bd;font-size:12px;text-transform:none;letter-spacing:0;">(account only -- enroll their voice separately)</span></h2>
    <input id="newName" placeholder="Display name">
    <select id="newRole"><option value="user">user</option><option value="admin">admin</option></select>
    <button onclick="addUser()">Create</button>
  </div>
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
    tr.innerHTML = `<td>${u.display_name}</td><td><span class="pill">${u.role}</span></td>
      <td><span class="pill ${u.active ? "on" : "off"}">${u.active ? "yes" : "no"}</span></td>
      <td>${u.sample_count}</td><td>${lastActive}</td><td><div class="actions"></div></td>`;
    const actions = tr.querySelector(".actions");
    const mkBtn = (label, fn, cls) => { const b = document.createElement("button");
      b.textContent = label; b.onclick = fn; if (cls) b.className = cls; actions.appendChild(b); };
    mkBtn("Insights", () => window.open("/admin/insights?user=" + u.id +
      "&name=" + encodeURIComponent(u.display_name), "_blank"));
    mkBtn(u.active ? "Deactivate" : "Activate", () => updateUser(u.id, {active: u.active ? 0 : 1}), "ghost");
    mkBtn(u.role === "admin" ? "Demote" : "Promote",
      () => updateUser(u.id, {role: u.role === "admin" ? "user" : "admin"}), "ghost");
    mkBtn("Re-enroll voice", () => enrollUser(u.id), "ghost");
    mkBtn("Delete", () => deleteUser(u.id), "danger");
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
body{font-family:-apple-system,Segoe UI,Arial,sans-serif;background:#071d3a;color:#eaf2fb;padding:0 0 60px;}
.top{background:linear-gradient(90deg,#0a2e5c,#0d3b78);padding:16px 26px;display:flex;align-items:center;
gap:14px;box-shadow:0 2px 14px rgba(0,0,0,.35);flex-wrap:wrap;}
.top a{color:#8fd0ff;text-decoration:none;font-size:14px;} .top a:hover{text-decoration:underline;}
.top h1{font-size:18px;font-weight:600;} .top h1 b{color:#28b9e8;}
.wrap{max-width:1180px;margin:20px auto;padding:0 20px;}
#err{color:#e67e22;margin:12px 0;}
#stats{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px;}
.stat{background:#0c2b55;border:1px solid #1d3f70;border-radius:12px;padding:12px 18px;min-width:130px;
cursor:pointer;transition:transform .1s,border-color .15s,background .15s;user-select:none;}
.stat:hover{transform:translateY(-2px);border-color:#28b9e8;}
.stat.on{background:#123a6e;border-color:#28b9e8;box-shadow:0 0 0 2px rgba(40,185,232,.4);}
.stat .v{font-size:24px;color:#28b9e8;font-weight:bold;} .stat .l{font-size:12px;color:#9fc4e8;margin-top:2px;}
.stat .hint{font-size:10px;color:#6f93bd;margin-top:3px;}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:#9fc4e8;margin:4px 2px 14px;}
.legend span{display:inline-flex;align-items:center;gap:6px;}
.key{width:11px;height:11px;border-radius:50%;display:inline-block;}
#board{display:flex;gap:20px;flex-wrap:wrap;align-items:flex-start;}
#graphbox{flex:1 1 640px;min-width:320px;background:radial-gradient(circle at 50% 42%,#0a2a54,#071d3a);
border:1px solid #1d3f70;border-radius:16px;padding:6px;position:relative;}
#side{flex:1 1 260px;min-width:240px;}
.card{background:#0c2b55;border:1px solid #1d3f70;border-radius:14px;padding:16px;margin-bottom:16px;}
.card h2{color:#28b9e8;font-size:13px;letter-spacing:1px;text-transform:uppercase;margin-bottom:10px;}
ul{list-style:none;} li{background:#0e2f5e;border:1px solid #1d3f70;border-radius:9px;padding:8px 10px;
margin-bottom:6px;font-size:13px;line-height:1.35;}
li.muted{color:#6f93bd;background:none;border:none;}
.src{font-size:10px;padding:2px 7px;border-radius:9px;margin-left:5px;}
.src.explicit{background:#113a56;color:#28b9e8;} .src.auto{background:#113f2a;color:#2ecc71;}
.when{color:#7aa0cc;font-size:11px;margin-right:6px;}
.tl{display:flex;gap:10px;align-items:flex-start;background:#0c2b55;border:1px solid #1d3f70;
border-radius:10px;padding:9px 12px;margin-bottom:6px;font-size:13px;}
.tl .ic{font-size:16px;line-height:1.3;} .tl .txt{flex:1;} .tl.corrected{border-color:#7a3b2a;}
.tl i{color:#d9a;}
#detail{position:fixed;left:18px;bottom:18px;max-width:360px;background:#0c2b55;border:1px solid #28b9e8;
border-radius:12px;padding:12px 14px;font-size:13px;display:none;box-shadow:0 8px 24px rgba(0,0,0,.4);z-index:9;}
#detail .meta{color:#7aa0cc;font-size:11px;margin-top:6px;}
#detail .x{float:right;cursor:pointer;color:#8fb5e0;margin-left:10px;}
#svg g.node{transition:opacity .25s;cursor:pointer;}
#svg.flt g.node{opacity:.1;} #svg.flt g.node.hit{opacity:1;}
#svg g.node:hover{opacity:1 !important;}
</style></head>
<body>
<div class="top">
  <a href="/admin">&larr; Admin</a>
  <h1>What the agent has learned about <b id="who">...</b></h1>
</div>
<div class="wrap">
  <div id="err"></div>
  <div id="stats"></div>
  <div class="legend">
    <span><i class="key" style="background:#28b9e8"></i>told explicitly</span>
    <span><i class="key" style="background:#2ecc71"></i>learned automatically</span>
    <span><i class="key" style="background:#c0392b"></i>corrected / superseded</span>
    <span><i class="key" style="background:#b07cff"></i>episode</span>
    <span style="color:#6f93bd">click a tile to highlight &middot; click a node for details</span>
  </div>
  <div id="board">
    <div id="graphbox"><div id="graph"></div></div>
    <div id="side">
      <div class="card"><h2>Traits</h2><ul id="traits"></ul></div>
      <div class="card"><h2>Episodes</h2><ul id="episodes"></ul></div>
    </div>
  </div>
  <div class="card"><h2>Learning timeline</h2><div id="timeline"></div></div>
</div>
<div id="detail"></div>
<script>
var params=new URLSearchParams(location.search);
var uid=params.get("user"); var uname=params.get("name")||("user "+uid);
var firstName=uname.split(" ")[0];
document.getElementById("who").textContent=uname;
var current=null;

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
function anchor(x,cx){return x<cx-24?"end":(x>cx+24?"start":"middle");}
function valText(f){ var c=f.content||"";
  var m=c.match(/(?: is | are | as an | as a | named | in )(.*)$/i); return m?m[1]:c; }

function line(x1,y1,x2,y2,c,w,cls,dash){return '<line class="'+(cls||"")+'" x1="'+x1+'" y1="'+y1+'" x2="'+x2+
  '" y2="'+y2+'" stroke="'+c+'" stroke-width="'+w+'"'+(dash?' stroke-dasharray="4 4"':'')+'/>';}
function circ(x,y,r,fill,stroke,dash){return '<circle cx="'+x+'" cy="'+y+'" r="'+r+'" fill="'+fill+
  '" stroke="'+stroke+'" stroke-width="2"'+(dash?' stroke-dasharray="3 3"':'')+'/>';}
function txt(x,y,s,c,sz,an,w){return '<text x="'+x+'" y="'+y+'" fill="'+c+'" font-size="'+(sz||11)+
  '" text-anchor="'+(an||"middle")+'"'+(w?' font-weight="'+w+'"':'')+'>'+esc(s)+'</text>';}
function grp(cls,data,inner){return '<g class="'+cls+'" '+(data||"")+'>'+inner+'</g>';}
function dataAttrs(f,slot){return 'data-full="'+esc(f.content||f.summary_text||"")+'" data-src="'+
  (f.source||"")+'" data-when="'+(f.created_ts||"")+'" data-slot="'+esc(slot||"")+'"';}
function defs(){return '<defs>'+
  '<radialGradient id="cg"><stop offset="0%" stop-color="#6ad6ff"/><stop offset="100%" stop-color="#1b86c9"/></radialGradient>'+
  '<filter id="glow" x="-70%" y="-70%" width="240%" height="240%"><feGaussianBlur stdDeviation="3.2" result="b"/>'+
  '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>';}

function buildStats(d){
  var f=d.facts||[]; var active=f.filter(function(x){return x.active;});
  var auto=active.filter(function(x){return x.source==="auto";});
  var tiles=[["facts",active.length,"facts known","active"],
             ["corrections",(d.conflicts||[]).length,"corrections","old \\u2192 new"],
             ["auto",auto.length,"auto-learned","green nodes"],
             ["episodes",(d.episodes||[]).length,"episodes","conversations"]];
  document.getElementById("stats").innerHTML=tiles.map(function(t){
    return '<div class="stat" data-filter="'+t[0]+'" onclick="toggleFilter(\\''+t[0]+'\\')">'+
      '<div class="v">'+t[1]+'</div><div class="l">'+t[2]+'</div><div class="hint">'+t[3]+'</div></div>';
  }).join("");
}

function buildGraph(d){
  var facts=d.facts||[]; var slots={}; var traits=[]; var episodes=d.episodes||[];
  facts.forEach(function(f){ if(f.attribute){(slots[f.attribute]=slots[f.attribute]||[]).push(f);}
    else if(f.active){traits.push(f);} });
  var spokes=[];
  Object.keys(slots).sort().forEach(function(k){
    var arr=slots[k]; var active=arr.filter(function(f){return f.active;})[0]||arr[arr.length-1];
    spokes.push({type:"slot",label:k.replace(/_/g," "),active:active,
      sup:arr.filter(function(f){return !f.active;})});
  });
  if(traits.length) spokes.push({type:"hub",label:"traits",
    leaves:traits.map(function(f){return {f:f,source:f.source,cls:"trait"};})});
  if(episodes.length) spokes.push({type:"hub",label:"episodes",
    leaves:episodes.map(function(e){return {f:e,source:"episode",cls:"episode"};})});

  var W=1000,H=740,cx=W/2,cy=H/2,R=210;
  var n=spokes.length||1, edges="", nodes="";
  spokes.forEach(function(sp,i){
    var ang=(-90+i*360/n)*Math.PI/180;
    var x=cx+R*Math.cos(ang), y=cy+R*Math.sin(ang);
    edges+=line(cx,cy,x,y,"#294f84",1.4);
    if(sp.type==="slot"){
      var expl=sp.active.source==="explicit";
      var col=expl?"#28b9e8":"#2ecc71";
      nodes+=grp("node active "+(expl?"explicit":"auto"), dataAttrs(sp.active,sp.label),
        circ(x,y,11,"#0e2f5e",col)+txt(x,y-18,sp.label,"#8fd0ff",13,"middle","bold")+
        txt(x,y+24,shorten(valText(sp.active),22),"#eaf2fb",12,anchor(x,cx)));
      sp.sup.forEach(function(sf,si){
        var rr=R+66+si*30, ox=cx+rr*Math.cos(ang), oy=cy+rr*Math.sin(ang);
        edges+=line(x,y,ox,oy,"#c0392b",1.2,"",true);
        nodes+=grp("node superseded", dataAttrs(sf,sp.label),
          circ(ox,oy,6,"#3a2030","#c0392b",true)+
          txt(ox,oy+(oy<cy?-9:16),"was: "+shorten(valText(sf),16),"#d9a",10,anchor(ox,cx)));
      });
    } else {
      nodes+=grp("node hub", "",
        circ(x,y,14,"#102a54","#8fd0ff")+txt(x,y-22,sp.label,"#8fd0ff",13,"middle","bold")+
        txt(x,y+5,sp.leaves.length,"#8fd0ff",12,"middle","bold"));
      var m=sp.leaves.length, spread=Math.min(150,24*m)*Math.PI/180;
      sp.leaves.forEach(function(lf,j){
        var a2=ang+(m>1?(j-(m-1)/2)*(spread/(m-1)):0), rr=R+128;
        var lx=cx+rr*Math.cos(a2), ly=cy+rr*Math.sin(a2);
        edges+=line(x,y,lx,ly,"#244a7c",1);
        var col=lf.cls==="episode"?"#b07cff":(lf.source==="explicit"?"#28b9e8":"#2ecc71");
        var extra=lf.cls==="episode"?"episode":(lf.source==="explicit"?"explicit":"auto");
        nodes+=grp("node active "+lf.cls+" "+extra, dataAttrs(lf.f,sp.label),
          circ(lx,ly,5,col,col)+txt(lx,ly+(ly<cy?-8:14),shorten(lf.f.content||lf.f.summary_text,18),
            "#cfe3f5",10,anchor(lx,cx)));
      });
    }
  });
  var svg='<svg id="svg" viewBox="0 0 '+W+' '+H+'" width="100%">'+defs()+edges+
    grp("node center","",circ(cx,cy,36,"url(#cg)","#0a2e5c")+txt(cx,cy+6,firstName,"#042038",16,"middle","bold"))+
    nodes+'</svg>';
  document.getElementById("graph").innerHTML=svg;
  document.getElementById("svg").addEventListener("click",onNodeClick);
}

function onNodeClick(e){
  var g=e.target.closest("g.node"); if(!g||g.classList.contains("center")||g.classList.contains("hub"))return;
  var det=document.getElementById("detail");
  det.style.display="block";
  det.innerHTML='<span class="x" onclick="document.getElementById(\\'detail\\').style.display=\\'none\\'">\\u2715</span>'+
    '<b>'+esc(g.dataset.slot||"fact")+'</b><br>'+esc(g.dataset.full||"")+
    '<div class="meta">'+(g.dataset.src?("source: "+g.dataset.src):"")+
    (g.dataset.when?(" \\u00b7 "+dt(+g.dataset.when)):"")+
    (g.classList.contains("superseded")?" \\u00b7 superseded":"")+'</div>';
}

function toggleFilter(f){
  var svg=document.getElementById("svg"); if(!svg)return;
  current = (current===f)? null : f;
  document.querySelectorAll(".stat").forEach(function(t){
    t.classList.toggle("on", t.dataset.filter===current); });
  svg.classList.remove("flt");
  svg.querySelectorAll("g.node").forEach(function(nd){nd.classList.remove("hit");});
  svg.querySelectorAll("circle[filter]").forEach(function(c){c.removeAttribute("filter");});
  if(!current) return;
  var sel={facts:"g.node.active",corrections:"g.node.superseded",
           auto:"g.node.auto",episodes:"g.node.episode"}[current];
  svg.classList.add("flt");
  svg.querySelectorAll(sel).forEach(function(nd){nd.classList.add("hit");
    nd.querySelectorAll("circle").forEach(function(c){c.setAttribute("filter","url(#glow)");});});
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
  (d.facts||[]).forEach(function(f){ ev.push({t:f.created_ts,type:"learned",text:f.content,active:f.active}); });
  (d.conflicts||[]).forEach(function(c){ ev.push({t:c.created_ts,type:"corrected",
    text:(c.attribute?c.attribute.replace(/_/g," ")+": ":"")+shorten(c.old_text,30)+" \\u2192 "+
    shorten(c.new_text,30)}); });
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
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code, html):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")   # always serve the current page, never a stale cache
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
