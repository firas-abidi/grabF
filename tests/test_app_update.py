# group: offline
import http.server, json, sys, threading
from helpers import WORK  # grabF on the path, settings in a temp folder
import app
ok = lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True)
answer = {}
hits = []


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        hits.append(self.path)
        body = json.dumps(answer).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass


srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
app.API_BASE = f"http://127.0.0.1:{srv.server_port}"
ev = []
app.push = lambda e, d: ev.append((e, d))


def check(repo, tag, url):
    ev.clear(); app._release_url = ""
    app.UPDATE_REPO = repo
    answer.update(tag_name=tag, html_url=url)
    app.check_app_update()
    return ev


ok(not check("", "v9.0.0", "https://github.com/x/y/releases/tag/v9") and not hits, "no repo set: no request made at all")
r = check("me/grab", "v1.1.0", "https://github.com/me/grab/releases/tag/v1.1.0")
ok(r == [("appupdate", {"version": "1.1.0"})] and app._release_url.endswith("v1.1.0"), "newer release: note shown")
ok(not check("me/grab", "v1.0.0", "https://github.com/me/grab/releases/tag/v1.0.0"), "same version: nothing shown")
ok(not check("me/grab", "v0.9.0", "https://github.com/me/grab/releases/tag/v0.9.0"), "older version: nothing shown")
ok(not check("me/grab", "v2.0.0", "https://evil.example/grab-setup.exe") and not app._release_url,
   "a release pointing anywhere but this repo's releases is ignored")
ok(not check("../../evil", "v9", "https://github.com/../../evil/releases/x") , "a malformed repo name is never requested")
ok(app.version_tuple("v1.10.0") > app.version_tuple("v1.9.3"), "1.10 counts as newer than 1.9")
