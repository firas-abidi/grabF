# group: offline
import sys, types, threading, tempfile, json
from pathlib import Path
from helpers import WORK  # grabF on the path, settings in a temp folder
import app
ok = lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True)
d = Path(tempfile.mkdtemp())
app.CONFIG = d / "settings.json"; app.HISTORY = d / "history.json"
app.WINDOW = types.SimpleNamespace(get_current_url=lambda: "http://127.0.0.1:1/i", real_url="http://127.0.0.1:1/i")
api = app.Api()
errors = []
def hammer(k, vals):
    for v in vals:
        try:
            api.save.__wrapped__(api, {k: v}); app.load_settings()
        except Exception as e:
            errors.append(e)
ts = [threading.Thread(target=hammer, args=("sub_size", list(range(50, 250)))),
      threading.Thread(target=hammer, args=("kbps", [128, 192, 256, 320] * 50)),
      threading.Thread(target=hammer, args=("sub_outline", [True, False] * 100)),
      threading.Thread(target=hammer, args=("mode", ["video", "audio"] * 100))]
for t in ts: t.start()
for t in ts: t.join()
s = json.loads(app.CONFIG.read_text())
ok(not errors, f"800 saves from 4 threads at once: {len(errors)} errors")
ok(s["sub_size"] == 249 and s["kbps"] == 320 and s["sub_outline"] is False and s["mode"] == "audio",
   f"every thread's last change kept: {s}")
ok(not list(d.glob("*.tmp")), "no temp files left behind")
def hist(n):
    for i in range(n):
        app.add_history({"id": f"{threading.get_ident()}-{i}", "title": "t", "files": [], "when": 1}); api.history.__wrapped__(api)
ts = [threading.Thread(target=hist, args=(60,)) for _ in range(4)]
for t in ts: t.start()
for t in ts: t.join()
ok(len(app.load_history()) == 240, f"history: 240 entries from 4 threads, got {len(app.load_history())}")
