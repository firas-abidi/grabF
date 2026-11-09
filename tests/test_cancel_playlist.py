# group: network
import shutil, sys, threading, time
from pathlib import Path
from helpers import WORK  # grabF on the path, settings in a temp folder
import app
SC = WORK
OUT = SC / "cancelpl"
shutil.rmtree(OUT, ignore_errors=True)
app.HISTORY = SC / "cfg12" / "history.json"
ev = []
app.push = lambda e, d: ev.append((e, d))
ok = lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True)
U = "https://www.youtube.com/playlist?list=PLx-_-Kk4c89oOHEDQAojOXzEzemXxoqx6"
job = app.Job("c2", app.clean_request({"url": U, "mode": "video", "quality": 720, "playlist": True, "count": 2}),
              {**app.DEFAULTS, "out": str(OUT)})
t = threading.Thread(target=job.run)
t.start()
for _ in range(600):
    time.sleep(0.25)
    if any(e == "progress" and d.get("item") == 2 and (d.get("pct") or 0) > 1 for e, d in ev):
        break
job.cancel()
t.join(90)
left = sorted(p.name for p in (OUT.rglob("*")) if p.is_file())
print("left:", left)
ok(ev[-1][1].get("error") == "cancelled", "reported as cancelled")
ok(len(left) == 1 and left[0].startswith("001 - ") and not left[0].endswith(".part"), "finished first video kept, half of the second removed")
