# group: network
import shutil, sys, threading, time
from pathlib import Path
from helpers import WORK  # grabF on the path, settings in a temp folder
import app
SC = WORK
OUT = SC / "canceltest"
shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir()
(OUT / "keep me.mp4").write_bytes(b"an older download that must survive")
app.HISTORY = SC / "cfg12" / "history.json"
ev = []
app.push = lambda e, d: ev.append((e, d))
ok = lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True)

job = app.Job("c1", app.clean_request({"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mode": "video", "quality": 2160}),
              {**app.DEFAULTS, "out": str(OUT)})
t = threading.Thread(target=job.run)
t.start()
for _ in range(120):  # wait until real data is flowing
    time.sleep(0.25)
    if any(e == "progress" and (d.get("pct") or 0) > 1 for e, d in ev):
        break
mid = sorted(p.name for p in OUT.iterdir())
job.cancel()
t.join(60)
left = sorted(p.name for p in OUT.iterdir())
print("while downloading:", mid)
print("after cancel     :", left)
ok(ev[-1] == ("failed", {"id": "c1", "error": "cancelled"}), "reported as cancelled")
ok(left == ["keep me.mp4"], "no half-finished files left, older file untouched")
