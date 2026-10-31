"""Admin web UI: account management only.

This module deliberately only ever imports `companion.identity.repository`
(account metadata) and the audio/enrollment plumbing needed to re-enroll a
voice. It never imports `companion.skills.memory_notes`, so there is no code
path here that can read another user's notes/transcripts -- admins manage
accounts, not content.
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
        if path == "/admin/users":
            if not self._authed():
                return self._send_json(401, {"error": "unauthorized"})
            return self._send_json(200, {"users": repository.list_users()})
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
        from ..audio.capture import record
        from ..audio.playback import speak
        from ..identity.enrollment import enroll_user

        def record_fn(seconds, tag):
            return record(seconds, denoise=False, tag=tag)

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


def start_admin_server():
    if not os.path.exists(config.ADMIN_PASSWORD_FILE):
        print("admin UI: no admin password set -- run `talk.py set-admin-password <password>`")
        return
    try:
        httpd = socketserver.TCPServer(("", config.ADMIN_PORT), AdminHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        print(f"admin UI: http://localhost:{config.ADMIN_PORT}/admin")
    except Exception as exc:
        print(f"admin server not started ({exc})")
