"""Serves face.html, a live status stream (Server-Sent Events) and status.json
over HTTP, plus the LAN-IP / QR helpers used to point a phone at the face.

Status changes are pushed to connected pages instantly over SSE (/events);
status.json stays as a polling fallback. The server is threaded (and rebinds
its port immediately after a restart) so many devices -- and a long-lived SSE
stream -- never block one another, which is what caused the old lag/freezes.
"""
import functools
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import threading
import time

from .. import config, state

# Injected into the served face.html just before </body>. The template source
# (data/face_html.py) is governed by a content-exclusion policy and can't be
# edited directly, so the mute button is grafted on at serve time instead. The
# snippet is fully self-contained (own styles + status.json polling) so it does
# not depend on any IDs or JS already present in the template.
_MUTE_BUTTON_HTML = """
<style>
#companion-mute-btn{position:fixed;right:16px;bottom:16px;z-index:9999;
  font:600 15px/1.2 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  padding:12px 18px;border:none;border-radius:999px;cursor:pointer;
  color:#fff;background:#2563eb;box-shadow:0 2px 10px rgba(0,0,0,.35);
  transition:background .15s ease,transform .05s ease;}
#companion-mute-btn:active{transform:scale(.96);}
#companion-mute-btn.muted{background:#dc2626;}
</style>
<button id="companion-mute-btn" type="button" onclick="companionToggleMute()">\U0001F507 Mute</button>
<script>
(function(){
  var muted=false, busy=false;
  function paint(){
    var b=document.getElementById('companion-mute-btn');
    if(!b)return;
    b.classList.toggle('muted',muted);
    b.textContent=(muted?'\U0001F508 Unmute':'\U0001F507 Mute');
  }
  window.companionToggleMute=function(){
    if(busy)return; busy=true;
    fetch('/mute',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({muted:!muted})})
      .then(function(r){return r.json();})
      .then(function(d){muted=!!d.muted;paint();})
      .catch(function(){})
      .then(function(){busy=false;});
  };
  // Keep the button in sync with the device (physical restarts, other tabs)
  // via the same no-cache status.json the rest of the face already serves.
  function sync(){
    fetch('status.json',{cache:'no-store'})
      .then(function(r){return r.json();})
      .then(function(s){if(typeof s.muted_ui==='boolean'){muted=s.muted_ui;paint();}})
      .catch(function(){});
  }
  paint(); sync(); setInterval(sync,1500);
})();
</script>
"""

# ---- in-process status broker ------------------------------------------------
# set_status() bumps a version and wakes every open SSE stream; the streams send
# the new status immediately. No file polling on the hot path.
_status_cond = threading.Condition()
_status_version = 0
_status_latest = {"state": "idle"}


def _atomic_write_json(path, payload):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)   # atomic: a reader never sees a half-written file


def _build_status(state_name, heard, reply, egress):
    try:
        from ..llm.client import current_model   # lazy: avoid import cost/cycles at load
        model = current_model()
    except Exception:
        model = ""
    return {
        "state": state_name, "heard": heard, "reply": reply, "egress": egress,
        "health": state.offline_parts, "url": state.pi_url,
        "speaker": (state.current_user["display_name"] if state.current_user else "guest"),
        "match_score": round(state.last_match_score, 3),
        "match_threshold": config.SPEAKER_MATCH_THRESHOLD,
        "model": model.split(":")[0] if model else "",
        "asr": "server" if config.WHISPER_SERVER_ENABLED else "cli",
        "latency_ms": getattr(state, "last_latency_ms", 0) or 0,
        "muted_ui": state.ui_muted,
        "time": time.time(),
    }


def set_status(state_name, heard="", reply="", egress=""):
    global _status_version, _status_latest
    payload = _build_status(state_name, heard, reply, egress)
    with _status_cond:
        _status_latest = payload
        _status_version += 1
        _status_cond.notify_all()   # push to every open /events stream
    try:
        _atomic_write_json(config.STATUS_FILE, payload)   # polling fallback
    except Exception:
        pass


class _FaceHandler(http.server.SimpleHTTPRequestHandler):
    """Static files from config.WORK, plus a /events SSE stream."""

    def log_message(self, *args, **kwargs):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def end_headers(self):
        # status.json must never be cached, or the poll fallback goes stale.
        if self.path.split("?")[0].endswith("status.json"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/events":
            return self._serve_events()
        if path in ("/", "/face.html"):
            return self._serve_face()
        return super().do_GET()
    def _serve_face(self):
        """Serve face.html with the mute button grafted in before </body>.
        Falls back to the plain static file if anything goes wrong so the face
        never breaks just because the injection did."""
        try:
            with open(config.FACE_FILE, "r", encoding="utf-8") as f:
                html = f.read()
            marker = "</body>"
            if marker in html.lower():
                idx = html.lower().rfind(marker)
                html = html[:idx] + _MUTE_BUTTON_HTML + html[idx:]
            else:
                html += _MUTE_BUTTON_HTML
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.path = "/face.html"
            return super().do_GET()

    def do_POST(self):
        if self.path.split("?")[0] == "/mute":
            return self._handle_mute()
        self.send_response(404)
        self.end_headers()

    def _handle_mute(self):
        """Toggled by the mute button on face.html. Independent of the
        physical GPIO switch -- see state.ui_muted for the exact semantics."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        try:
            data = json.loads(body) if body else {}
        except ValueError:
            data = {}
        state.ui_muted = bool(data["muted"]) if "muted" in data else (not state.ui_muted)
        if state.ui_muted and state.led:
            state.led.off()   # instant feedback; the next loop pass would do this anyway
        payload = json.dumps({"muted": state.ui_muted}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_events(self):
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        last = -1
        while True:
            with _status_cond:
                if _status_version == last:
                    _status_cond.wait(timeout=15)   # wake on change, else heartbeat
                changed = _status_version != last
                last = _status_version
                payload = _status_latest if changed else None
            try:
                if payload is not None:
                    self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode("utf-8"))
                else:
                    self.wfile.write(b": heartbeat\n\n")   # keep the connection alive through proxies
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return   # client navigated away; let the thread end


class _ThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True   # rebind immediately after a restart (no TIME_WAIT "won't start")
    daemon_threads = True        # open SSE streams never block process shutdown


def start_face_server():
    try:
        handler = functools.partial(_FaceHandler, directory=config.WORK)
        httpd = _ThreadingServer(("", config.FACE_PORT), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        print(f"face: http://localhost:{config.FACE_PORT}/face.html")
    except Exception as exc:
        print(f"face server not started ({exc})")


def lan_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))   # no packets sent for a UDP connect; just picks the iface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def make_qr(url):
    """Render a scannable QR to qr.png if qrencode is installed (apt); else skip."""
    try:
        if shutil.which("qrencode"):
            subprocess.run(["qrencode", "-o", os.path.join(config.WORK, "qr.png"),
                            "-s", "6", "-m", "2", url], check=False)
    except Exception:
        pass
