"""The on-screen face's HTML/CSS/JS, written to disk on first run and served
by the face web server. Updates arrive instantly over Server-Sent Events
(/events) with a polling fallback, and the page surfaces live context: who is
recognized (with confidence), the model + ASR path, last-turn latency, and a
rolling conversation log.
"""

FACE_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Unplugged AI</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{background:#0a2e5c;color:#eaf2fb;font-family:Arial,Helvetica,sans-serif;min-height:100vh;
display:flex;flex-direction:column;align-items:center;padding:18px;overflow-x:hidden;position:relative;}
#title{font-size:20px;letter-spacing:3px;color:#28b9e8;text-transform:uppercase;margin-top:6px;}
#sub{font-size:13px;color:#9fc4e8;margin-bottom:10px;}
#orb{width:168px;height:168px;border-radius:50%;background:#39506e;margin:8px 0 14px;
position:relative;transition:background .4s,box-shadow .4s;flex:0 0 auto;}
.eye{position:absolute;top:58px;width:22px;height:22px;border-radius:50%;background:#0a2e5c;
transition:height .2s,top .2s;}
.eye.l{left:44px;} .eye.r{right:44px;}
#mouth{position:absolute;bottom:40px;left:50%;transform:translateX(-50%);width:52px;height:9px;
border-radius:0 0 26px 26px;background:#0a2e5c;transition:height .15s,width .15s,border-radius .15s;}
#shield{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:82px;display:none;}
#state{font-size:15px;letter-spacing:2px;text-transform:uppercase;color:#28b9e8;min-height:18px;}
#heard{font-size:18px;color:#cfe3f5;margin-top:8px;min-height:22px;max-width:92%;text-align:center;}
#reply{font-size:23px;line-height:1.35;color:#fff;max-width:92%;min-height:30px;margin-top:6px;text-align:center;}
#badges{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-top:16px;max-width:680px;}
.badge{background:#13396b;border:1px solid #2a4a7a;border-radius:16px;padding:6px 12px;font-size:13px;
color:#cfe3f5;display:flex;align-items:center;gap:6px;}
.badge b{color:#28b9e8;font-weight:bold;}
.dot{width:8px;height:8px;border-radius:50%;background:#2ecc71;}
.dot.warn{background:#e67e22;} .dot.bad{background:#c0392b;}
#confwrap{width:90px;height:7px;background:#0a2e5c;border-radius:4px;overflow:hidden;}
#confbar{height:100%;width:0;background:#2ecc71;transition:width .3s;}
#log{width:100%;max-width:680px;margin-top:18px;}
#log h2{font-size:12px;letter-spacing:2px;color:#9fc4e8;text-transform:uppercase;margin-bottom:8px;}
.turn{background:#0d336b;border:1px solid #244a7c;border-radius:10px;padding:10px 12px;margin-bottom:8px;}
.turn .u{color:#8fd0ff;font-size:14px;} .turn .a{color:#fff;font-size:15px;margin-top:3px;}
#egress{position:fixed;top:14px;left:50%;transform:translateX(-50%);background:#e67e22;
color:#1a1200;padding:8px 18px;border-radius:20px;font-size:14px;font-weight:bold;
opacity:0;transition:opacity .3s;max-width:90%;z-index:5;}
#conn{position:fixed;top:12px;right:14px;font-size:11px;color:#6f93bd;}
#qr{position:fixed;bottom:12px;right:12px;text-align:center;}
#qrimg{width:96px;height:96px;border:4px solid #fff;border-radius:8px;display:none;background:#fff;}
#qrurl{font-size:11px;color:#9fc4e8;margin-top:3px;max-width:130px;word-break:break-all;}
.listening #orb{background:#2ecc71;box-shadow:0 0 60px #2ecc71;}
.listening .eye{height:26px;top:56px;}
.thinking #orb{background:#28b9e8;box-shadow:0 0 55px #28b9e8;}
.thinking .eye{height:6px;top:68px;}
.speaking #orb{background:#9b59b6;box-shadow:0 0 60px #9b59b6;}
.speaking #mouth{animation:talk .3s infinite;}
.fetching #orb{background:#e67e22;box-shadow:0 0 60px #e67e22;}
.muted #orb{background:#555;box-shadow:none;}
.muted #mouth{width:34px;height:4px;border-radius:4px;}
.idle #orb{background:#39506e;box-shadow:none;}
.blocked #orb{background:#c0392b;box-shadow:0 0 80px #c0392b;}
.blocked .eye,.blocked #mouth{display:none;}
.blocked #shield{display:block;}
@keyframes talk{0%,100%{height:9px;}50%{height:30px;border-radius:26px;}}
@keyframes pulseorb{0%,100%{transform:scale(1);}50%{transform:scale(1.06);}}
.listening #orb,.speaking #orb,.fetching #orb{animation:pulseorb 1s infinite;}
</style></head>
<body class="idle">
<div id="egress"></div>
<div id="conn">connecting...</div>
<div id="title">Unplugged AI</div>
<div id="sub">Private, on-device companion</div>
<div id="orb">
  <div class="eye l"></div><div class="eye r"></div>
  <div id="mouth"></div><div id="shield">&#128737;</div>
</div>
<div id="state">Idle</div>
<div id="heard"></div>
<div id="reply">Say "hi companion" or press the button</div>

<div id="badges">
  <div class="badge"><span class="dot" id="spkdot"></span>Speaker: <b id="speaker">-</b>
    <span id="confwrap"><span id="confbar"></span></span></div>
  <div class="badge">Model: <b id="model">-</b></div>
  <div class="badge">ASR: <b id="asr">-</b></div>
  <div class="badge">Last reply: <b id="latency">-</b></div>
  <div class="badge"><span class="dot" id="healthdot"></span><b id="health">healthy</b></div>
</div>

<div id="log"><h2>Conversation</h2><div id="turns"></div></div>

<div id="qr">
  <img id="qrimg" src="qr.png" onload="this.style.display='block'" onerror="this.style.display='none'">
  <div id="qrurl"></div>
</div>

<script>
var lastEg="", egTimer=null, lastLogged="", turns=[];
function el(id){return document.getElementById(id);}

function render(d){
  var s=d.state||"idle";
  document.body.className=s;
  el("state").textContent=s;
  el("heard").textContent=d.heard?("You: "+d.heard):"";
  if(d.reply){el("reply").textContent=d.reply;}
  else if(s==="listening"){el("reply").textContent="Listening...";}
  else if(s==="thinking"){el("reply").textContent="Thinking...";}
  else if(s==="fetching"){el("reply").textContent="Fetching live data...";}
  else if(s==="muted"){el("reply").textContent="Microphone muted";}

  // speaker + confidence
  var spk=d.speaker||"guest";
  el("speaker").textContent=spk;
  var score=d.match_score||0, thr=d.match_threshold||0.65;
  var pct=Math.max(0,Math.min(100,Math.round((score/ (thr*1.3||1))*100)));
  el("confbar").style.width=pct+"%";
  var dot=el("spkdot");
  dot.className="dot"+(spk==="guest"?" warn":"")+(score&&score<thr?" warn":"");
  el("model").textContent=d.model||"-";
  el("asr").textContent=d.asr||"-";
  el("latency").textContent=d.latency_ms?(d.latency_ms+" ms"):"-";

  // health
  var hd=el("healthdot"), ht=el("health");
  if(d.health && d.health.length){hd.className="dot bad";ht.textContent="Offline: "+d.health.join(", ");}
  else{hd.className="dot";ht.textContent="healthy";}

  // egress banner
  if(d.egress && d.egress!==lastEg){
    lastEg=d.egress;var e=el("egress");e.textContent=d.egress;e.style.opacity=1;
    if(egTimer)clearTimeout(egTimer);
    egTimer=setTimeout(function(){e.style.opacity=0;},3000);
  }
  if(d.url){el("qrurl").textContent=d.url;}

  // conversation log: append a completed turn once (heard + reply, on idle/speaking)
  if(d.heard && d.reply){
    var key=d.heard+"||"+d.reply;
    if(key!==lastLogged){
      lastLogged=key;
      turns.unshift({u:d.heard, a:d.reply});
      turns=turns.slice(0,6);
      el("turns").innerHTML=turns.map(function(t){
        return '<div class="turn"><div class="u">You: '+esc(t.u)+'</div>'+
               '<div class="a">'+esc(t.a)+'</div></div>';}).join("");
    }
  }
}
function esc(s){return (s||"").replace(/[&<>]/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;"}[c];});}

// Instant updates via SSE, with a polling fallback if it drops/unsupported.
var pollTimer=null;
function startPolling(){
  if(pollTimer)return;
  pollTimer=setInterval(function(){
    fetch("status.json?t="+Date.now()).then(function(r){return r.json();})
      .then(render).catch(function(){});
  },500);
}
function stopPolling(){ if(pollTimer){clearInterval(pollTimer);pollTimer=null;} }
function connectSSE(){
  if(!window.EventSource){ el("conn").textContent="polling"; startPolling(); return; }
  var es=new EventSource("/events");
  es.onopen=function(){ el("conn").textContent="live"; stopPolling(); };
  es.onmessage=function(ev){ try{ render(JSON.parse(ev.data)); }catch(e){} };
  es.onerror=function(){ el("conn").textContent="reconnecting"; startPolling(); };
}
// prime from the file immediately, then go live
fetch("status.json?t="+Date.now()).then(function(r){return r.json();}).then(render).catch(function(){});
connectSSE();
</script></body></html>
"""
