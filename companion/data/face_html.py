"""The on-screen face's HTML/CSS/JS, written to disk on first run and served
as a static file by the face web server.
"""

FACE_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Unplugged AI</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{background:#0a2e5c;color:#eaf2fb;font-family:Arial,sans-serif;height:100vh;
display:flex;flex-direction:column;align-items:center;justify-content:center;
text-align:center;padding:20px;overflow:hidden;position:relative;}
#title{font-size:22px;letter-spacing:3px;color:#28b9e8;text-transform:uppercase;margin-bottom:6px;}
#sub{font-size:14px;color:#9fc4e8;margin-bottom:24px;}
#orb{width:200px;height:200px;border-radius:50%;background:#39506e;margin-bottom:26px;
position:relative;transition:background .4s,box-shadow .4s;}
.eye{position:absolute;top:70px;width:26px;height:26px;border-radius:50%;background:#0a2e5c;
transition:height .2s,top .2s;}
.eye.l{left:52px;} .eye.r{right:52px;}
#mouth{position:absolute;bottom:46px;left:50%;transform:translateX(-50%);width:60px;height:10px;
border-radius:0 0 30px 30px;background:#0a2e5c;transition:height .15s,width .15s,border-radius .15s;}
#shield{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:96px;display:none;}
#state{font-size:16px;letter-spacing:2px;text-transform:uppercase;color:#28b9e8;margin-bottom:16px;min-height:20px;}
#heard{font-size:20px;color:#cfe3f5;margin-bottom:12px;min-height:24px;max-width:90%;}
#reply{font-size:26px;line-height:1.4;color:#fff;max-width:90%;min-height:32px;}
#egress{position:absolute;top:16px;left:50%;transform:translateX(-50%);background:#e67e22;
color:#1a1200;padding:8px 18px;border-radius:20px;font-size:15px;font-weight:bold;
opacity:0;transition:opacity .3s;max-width:90%;}
#health{position:absolute;top:16px;right:16px;background:#c0392b;color:#fff;
padding:6px 12px;border-radius:14px;font-size:13px;display:none;}
#qr{position:absolute;bottom:14px;right:14px;text-align:center;}
#qrimg{width:120px;height:120px;border:4px solid #fff;border-radius:8px;display:none;background:#fff;}
#qrurl{font-size:12px;color:#9fc4e8;margin-top:4px;max-width:150px;word-break:break-all;}
.listening #orb{background:#2ecc71;box-shadow:0 0 70px #2ecc71;}
.listening .eye{height:30px;top:66px;}
.thinking #orb{background:#28b9e8;box-shadow:0 0 65px #28b9e8;}
.thinking .eye{height:6px;top:80px;}
.speaking #orb{background:#9b59b6;box-shadow:0 0 70px #9b59b6;}
.speaking #mouth{animation:talk .3s infinite;}
.fetching #orb{background:#e67e22;box-shadow:0 0 70px #e67e22;}
.muted #orb{background:#555;box-shadow:none;}
.muted #mouth{width:40px;height:4px;border-radius:4px;}
.idle #orb{background:#39506e;box-shadow:none;}
.blocked #orb{background:#c0392b;box-shadow:0 0 90px #c0392b;}
.blocked .eye,.blocked #mouth{display:none;}
.blocked #shield{display:block;}
@keyframes talk{0%,100%{height:10px;}50%{height:34px;border-radius:30px;}}
@keyframes pulseorb{0%,100%{transform:scale(1);}50%{transform:scale(1.06);}}
.listening #orb,.speaking #orb,.fetching #orb{animation:pulseorb 1s infinite;}
</style></head>
<body class="idle">
<div id="title">Unplugged AI</div>
<div id="sub">Private, on-device companion</div>
<div id="egress"></div>
<div id="health"></div>
<div id="orb">
  <div class="eye l"></div><div class="eye r"></div>
  <div id="mouth"></div><div id="shield">&#128737;</div>
</div>
<div id="state">Idle</div>
<div id="heard"></div>
<div id="reply">Press the button to talk</div>
<div id="qr">
  <img id="qrimg" src="qr.png" onload="this.style.display='block'" onerror="this.style.display='none'">
  <div id="qrurl"></div>
</div>
<script>
var lastEg="",egTimer=null;
async function update(){
 try{
  var r=await fetch("status.json?t="+Date.now());var d=await r.json();
  var s=d.state||"idle";document.body.className=s;
  document.getElementById("state").textContent=s;
  document.getElementById("heard").textContent=d.heard?("You: "+d.heard):"";
  if(d.reply){document.getElementById("reply").textContent=d.reply;}
  else if(s==="listening"){document.getElementById("reply").textContent="Listening...";}
  else if(s==="thinking"){document.getElementById("reply").textContent="Thinking...";}
  else if(s==="fetching"){document.getElementById("reply").textContent="Fetching live data...";}
  else if(s==="muted"){document.getElementById("reply").textContent="Microphone muted";}
  if(d.egress && d.egress!==lastEg){
    lastEg=d.egress;var e=document.getElementById("egress");
    e.textContent=d.egress;e.style.opacity=1;
    if(egTimer)clearTimeout(egTimer);
    egTimer=setTimeout(function(){e.style.opacity=0;},3000);
  }
  var h=document.getElementById("health");
  if(d.health && d.health.length){h.style.display="block";h.textContent="Offline: "+d.health.join(", ");}
  else{h.style.display="none";}
  if(d.url){document.getElementById("qrurl").textContent=d.url;}
 }catch(e){}
}
setInterval(update,350);update();
</script></body></html>
"""
