# group: network
import shutil, sys, threading, time
from pathlib import Path
from helpers import WORK  # grabF on the path, settings in a temp folder
import app
SC = WORK
OUT = SC / "burncancel"
shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir()
(OUT / "older.mp4").write_bytes(b"keep")
app.HISTORY = SC / "cfg20" / "history.json"
ok = lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True)
ev = []
app.push = lambda e, d: ev.append((time.time(), e, d))
job = app.Job("bc", app.clean_request({"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mode": "video", "quality": 1080,
                                       "subs": "en", "subs_mode": "burn", "sub_style": "clean"}), {**app.DEFAULTS, "out": str(OUT)})
th = threading.Thread(target=job.run)
th.start()
for _ in range(1200):
    time.sleep(0.1)
    if any(e == "progress" and str(d.get("stage", "")).startswith("burning") and (d.get("pct") or 0) > 5 for _, e, d in ev):
        break
t0 = time.time()
job.cancel()
th.join(60)
left = sorted(p.name for p in OUT.iterdir())
ok(time.time() - t0 < 3, f"burn stopped {time.time() - t0:.1f}s after cancel")
ok([d.get("error") for _, e, d in ev if e == "failed"] == ["cancelled"], "reported cancelled once")
ok(left == ["older.mp4"], f"nothing left behind: {left}")
