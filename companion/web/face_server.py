"""Serves face.html + status.json over HTTP, and the small helpers (LAN IP,
QR code) used to point a phone at the face.
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


def start_face_server():
    try:
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=config.WORK)
        handler.log_message = lambda *a, **k: None
        httpd = socketserver.TCPServer(("", config.FACE_PORT), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        print(f"face: http://localhost:{config.FACE_PORT}/face.html")
    except Exception as exc:
        print(f"face server not started ({exc})")


def set_status(state_name, heard="", reply="", egress=""):
    try:
        with open(config.STATUS_FILE, "w") as f:
            json.dump({"state": state_name, "heard": heard, "reply": reply,
                       "egress": egress, "health": state.offline_parts, "url": state.pi_url,
                       "time": time.time()}, f)
    except Exception:
        pass


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
