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


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """A browser navigating away mid-response raises BrokenPipeError, which
    the default handler dumps as a full traceback -- pure noise that buries
    the logs that matter."""

    def log_message(self, *args, **kwargs):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True


def start_face_server():
    try:
        handler = functools.partial(_QuietHandler, directory=config.WORK)
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
                       "speaker": (state.current_user["display_name"]
                                  if state.current_user else "guest"),
                       "match_score": round(state.last_match_score, 3),
                       "match_threshold": config.SPEAKER_MATCH_THRESHOLD,
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
