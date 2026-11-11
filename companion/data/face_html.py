"""The on-screen face's HTML/CSS/JS, written to disk on first run and served by
the face web server. Updates arrive instantly over Server-Sent Events (/events)
with a polling fallback. The page pairs an animated orb (the "face") with a live,
chat-style conversation feed that streams the reply in as it is spoken, plus
live context badges (speaker + confidence, model, ASR, last-turn latency).
"""

FACE_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Unplugged AI</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
:root{--bg0:#06183a;--bg1:#0a2e5c;--accent:#28b9e8;--accent2:#5ad1ff;--purple:#9b59b6;--green:#2ecc71;}
html,body{height:100%;}
body{font-family:-apple-system,"Segoe UI",Roboto,Arial,sans-serif;color:#eaf2fb;overflow:hidden;
background:radial-gradient(1200px 700px at 20% -10%,#123a73,transparent),
radial-gradient(900px 600px at 110% 110%,#13294f,transparent),linear-gradient(160deg,#06183a,#0a2340);}
#conn{position:fixed;top:12px;right:16px;font-size:11px;color:#8fb5e0;display:flex;align-items:center;gap:6px;z-index:9;}
#conn .d{width:8px;height:8px;border-radius:50%;background:#e67e22;box-shadow:0 0 8px #e67e22;}
#conn.live .d{background:var(--green);box-shadow:0 0 8px var(--green);}
#egress{position:fixed;top:12px;left:50%;transform:translateX(-50%);background:linear-gradient(90deg,#e67e22,#f0932b);
color:#2a1500;padding:8px 18px;border-radius:20px;font-size:14px;font-weight:700;opacity:0;
transition:opacity .3s;max-width:90%;z-index:9;box-shadow:0 6px 18px rgba(0,0,0,.3);}
.app{height:100%;display:flex;gap:26px;padding:26px;align-items:stretch;}
/* ---- left: the face ---- */
.face{flex:0 0 340px;display:flex;flex-direction:column;align-items:center;justify-content:center;
background:rgba(255,255,255,.04);border:1px solid rgba(120,170,230,.18);border-radius:24px;padding:26px;
backdrop-filter:blur(6px);box-shadow:0 10px 40px rgba(0,0,0,.3);}
.brand{font-size:15px;letter-spacing:4px;color:var(--accent);text-transform:uppercase;}
.brand small{display:block;font-size:11px;letter-spacing:1px;color:#9fc4e8;margin-top:4px;text-transform:none;}
#orb{width:180px;height:180px;border-radius:50%;background:#39506e;margin:26px 0 20px;position:relative;
transition:background .4s,box-shadow .4s;}
.eye{position:absolute;top:62px;width:24px;height:24px;border-radius:50%;background:#06183a;transition:height .2s,top .2s;}
.eye.l{left:48px;}.eye.r{right:48px;}
#mouth{position:absolute;bottom:44px;left:50%;transform:translateX(-50%);width:56px;height:10px;
border-radius:0 0 28px 28px;background:#06183a;transition:height .15s,width .15s,border-radius .15s;}
#shield{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:88px;display:none;}
#state{font-size:14px;letter-spacing:3px;text-transform:uppercase;color:var(--accent);min-height:18px;font-weight:700;}
.badges{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-top:18px;}
.badge{background:rgba(19,57,107,.7);border:1px solid rgba(120,170,230,.2);border-radius:14px;
padding:6px 11px;font-size:12px;color:#cfe3f5;display:flex;align-items:center;gap:6px;}
.badge b{color:var(--accent);} .dot{width:8px;height:8px;border-radius:50%;background:var(--green);}
.dot.warn{background:#e67e22;}.dot.bad{background:#c0392b;}
#confwrap{width:64px;height:6px;background:#06183a;border-radius:4px;overflow:hidden;}
#confbar{height:100%;width:0;background:linear-gradient(90deg,var(--green),var(--accent2));transition:width .3s;}
/* ---- right: live conversation ---- */
.chat{flex:1;display:flex;flex-direction:column;background:rgba(255,255,255,.04);
border:1px solid rgba(120,170,230,.18);border-radius:24px;overflow:hidden;backdrop-filter:blur(6px);
box-shadow:0 10px 40px rgba(0,0,0,.3);min-width:0;}
.chat h3{padding:16px 22px;font-size:13px;letter-spacing:2px;text-transform:uppercase;color:#9fc4e8;
border-bottom:1px solid rgba(120,170,230,.15);display:flex;align-items:center;gap:8px;}
.chat h3 .live{width:8px;height:8px;border-radius:50%;background:var(--green);animation:blink 1.4s infinite;}
#feed{flex:1;overflow-y:auto;padding:22px;display:flex;flex-direction:column;gap:14px;scroll-behavior:smooth;}
#feed::-webkit-scrollbar{width:8px;}#feed::-webkit-scrollbar-thumb{background:#1d3f70;border-radius:4px;}
.row{display:flex;gap:10px;max-width:84%;animation:popin .28s ease;}
.row.user{align-self:flex-end;flex-direction:row-reverse;}
.av{flex:0 0 32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;
font-size:15px;}
.av.bot{background:radial-gradient(circle,#5ad1ff,#1b86c9);}
.av.me{background:#1d3f70;color:#bfe0ff;font-weight:700;font-size:13px;}
.bub{padding:11px 15px;border-radius:16px;font-size:16px;line-height:1.45;position:relative;}
.user .bub{background:linear-gradient(135deg,#1b86c9,#1667a8);color:#fff;border-bottom-right-radius:5px;}
.bot .bub{background:rgba(13,51,107,.85);color:#eef6ff;border:1px solid rgba(120,170,230,.18);
border-bottom-left-radius:5px;}
.time{font-size:10px;color:#7aa0cc;margin-top:5px;}
.cursor{display:inline-block;width:8px;height:16px;background:var(--accent2);margin-left:3px;
vertical-align:-2px;animation:blink .9s infinite;border-radius:1px;}
.typing{display:inline-flex;gap:4px;padding:2px 0;}
.typing span{width:7px;height:7px;border-radius:50%;background:#7fb6e8;animation:bounce 1.2s infinite;}
.typing span:nth-child(2){animation-delay:.2s;}.typing span:nth-child(3){animation-delay:.4s;}
.empty{margin:auto;color:#6f93bd;font-size:15px;text-align:center;}
#health{position:fixed;bottom:14px;left:16px;background:#c0392b;color:#fff;padding:6px 12px;
border-radius:12px;font-size:12px;display:none;z-index:9;}
#qr{position:fixed;bottom:12px;right:14px;text-align:center;z-index:9;}
#qrimg{width:92px;height:92px;border:4px solid #fff;border-radius:10px;display:none;background:#fff;}
#qrurl{font-size:11px;color:#9fc4e8;margin-top:3px;max-width:130px;word-break:break-all;}
/* orb state */
.listening #orb{background:var(--green);box-shadow:0 0 70px var(--green);}
.listening .eye{height:28px;top:60px;}
.thinking #orb{background:var(--accent);box-shadow:0 0 65px var(--accent);}
.thinking .eye{height:6px;top:72px;}
.speaking #orb{background:var(--purple);box-shadow:0 0 70px var(--purple);}
.speaking #mouth{animation:talk .3s infinite;}
.fetching #orb{background:#e67e22;box-shadow:0 0 70px #e67e22;}
.muted #orb{background:#555;box-shadow:none;}.muted #mouth{width:38px;height:4px;border-radius:4px;}
.idle #orb{background:#39506e;}
.blocked #orb{background:#c0392b;box-shadow:0 0 90px #c0392b;}
.blocked .eye,.blocked #mouth{display:none;}.blocked #shield{display:block;}
@keyframes talk{0%,100%{height:10px;}50%{height:32px;border-radius:28px;}}
@keyframes pulseorb{0%,100%{transform:scale(1);}50%{transform:scale(1.05);}}
.listening #orb,.speaking #orb,.fetching #orb{animation:pulseorb 1s infinite;}
@keyframes popin{from{opacity:0;transform:translateY(10px) scale(.98);}to{opacity:1;transform:none;}}
@keyframes blink{0%,100%{opacity:1;}50%{opacity:.2;}}
@keyframes bounce{0%,60%,100%{transform:translateY(0);opacity:.6;}30%{transform:translateY(-5px);opacity:1;}}
@media(max-width:820px){.app{flex-direction:column;padding:16px;gap:16px;overflow-y:auto;}
.face{flex:0 0 auto;}#orb{width:130px;height:130px;}.eye{top:46px;}.eye.l{left:34px;}.eye.r{right:34px;}
#mouth{bottom:32px;}.chat{min-height:52vh;}}
</style></head>
<body class="idle">
<div id="egress"></div>
<div id="conn"><span class="d"></span><span id="connt">connecting</span></div>

<div class="app">
  <div class="face">
    <div class="brand">Unplugged AI<small>Private, on-device companion</small></div>
    <div id="orb"><div class="eye l"></div><div class="eye r"></div><div id="mouth"></div>
      <div id="shield">&#128737;</div></div>
    <div id="state">Idle</div>
    <div class="badges">
      <div class="badge"><span class="dot" id="spkdot"></span><b id="speaker">-</b>
        <span id="confwrap"><span id="confbar"></span></span></div>
      <div class="badge">Model <b id="model">-</b></div>
      <div class="badge">ASR <b id="asr">-</b></div>
      <div class="badge">Reply <b id="latency">-</b></div>
    </div>
  </div>

  <div class="chat">
    <h3><span class="live"></span>Live conversation</h3>
    <div id="feed"><div class="empty">Say &ldquo;hi companion&rdquo; or press the button to start.</div></div>
  </div>
</div>

<div id="health"></div>
<div id="qr">
  <img id="qrimg" src="qr.png" onload="this.style.display='block'" onerror="this.style.display='none'">
  <div id="qrurl"></div>
</div>

<script>
var lastEg="", egTimer=null, curQ=null, activeBot=null, emptyShown=true;
function el(id){return document.getElementById(id);}
function esc(s){return String(s==null?"":s).replace(/[&<>]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;"}[c];});}
function now(){return new Date().toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"});}
function feed(){return el("feed");}
function scrollDown(){var f=feed();f.scrollTop=f.scrollHeight;}
function clearEmpty(){if(emptyShown){feed().innerHTML="";emptyShown=false;}}
function trim(){var f=feed();while(f.children.length>40)f.removeChild(f.firstChild);}

function addRow(who, html){
  clearEmpty();
  var row=document.createElement("div");
  row.className="row "+(who==="me"?"user":"bot");
  row.innerHTML='<div class="av '+(who==="me"?"me":"bot")+'">'+(who==="me"?"You":"&#129504;")+'</div>'+
    '<div><div class="bub">'+html+'</div><div class="time">'+now()+'</div></div>';
  feed().appendChild(row); trim(); scrollDown();
  return row;
}
function startTurn(q){
  addRow("me", esc(q));
  activeBot=addRow("bot", '<span class="typing"><span></span><span></span><span></span></span>');
}
function setReply(text, live){
  if(!activeBot) startTurn(curQ||"");
  activeBot.querySelector(".bub").innerHTML=esc(text)+(live?'<span class="cursor"></span>':'');
  scrollDown();
}
function setTyping(){
  if(!activeBot) startTurn(curQ||"");
}
function finalize(text){
  if(activeBot) activeBot.querySelector(".bub").innerHTML=esc(text);
}

function render(d){
  var s=d.state||"idle";
  document.body.className=s;
  el("state").textContent=s;
  // badges
  var spk=d.speaker||"guest"; el("speaker").textContent=spk;
  var score=d.match_score||0, thr=d.match_threshold||0.65;
  el("confbar").style.width=Math.max(0,Math.min(100,Math.round((score/(thr*1.3||1))*100)))+"%";
  el("spkdot").className="dot"+((spk==="guest"||(score&&score<thr))?" warn":"");
  el("model").textContent=d.model||"-";
  el("asr").textContent=d.asr||"-";
  el("latency").textContent=d.latency_ms?(d.latency_ms+" ms"):"-";
  // health
  var h=el("health");
  if(d.health&&d.health.length){h.style.display="block";h.textContent="Offline: "+d.health.join(", ");}
  else h.style.display="none";
  // egress
  if(d.egress&&d.egress!==lastEg){lastEg=d.egress;var e=el("egress");e.textContent=d.egress;e.style.opacity=1;
    if(egTimer)clearTimeout(egTimer);egTimer=setTimeout(function(){e.style.opacity=0;},3000);}
  if(d.url)el("qrurl").textContent=d.url;
  // live conversation feed
  if(d.heard){
    if(d.heard!==curQ){ curQ=d.heard; startTurn(d.heard); }
    if(d.reply){ if(s==="idle"){ finalize(d.reply); } else { setReply(d.reply, true); } }
    else if(s==="thinking"||s==="fetching"){ setTyping(); }
  }
}

var pollTimer=null;
function startPolling(){ if(pollTimer)return;
  pollTimer=setInterval(function(){fetch("status.json?t="+Date.now())
    .then(function(r){return r.json();}).then(render).catch(function(){});},600); }
function stopPolling(){ if(pollTimer){clearInterval(pollTimer);pollTimer=null;} }
function setConn(t,live){el("connt").textContent=t;el("conn").classList.toggle("live",!!live);}
function connectSSE(){
  if(!window.EventSource){setConn("polling");startPolling();return;}
  var es=new EventSource("/events");
  es.onopen=function(){setConn("live",true);stopPolling();};
  es.onmessage=function(ev){try{render(JSON.parse(ev.data));}catch(e){}};
  es.onerror=function(){setConn("reconnecting");startPolling();};
}
fetch("status.json?t="+Date.now()).then(function(r){return r.json();}).then(render).catch(function(){});
connectSSE();
</script></body></html>
"""
