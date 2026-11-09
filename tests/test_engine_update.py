# group: offline
import json, sys, time, types
from pathlib import Path
from helpers import WORK  # grabF on the path, settings in a temp folder
import app
SC = WORK
app.CONFIG = SC / "cfg10" / "settings.json"
ok = lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True)
runs = []
real_run = app.subprocess.run
app.subprocess.run = lambda cmd, **kw: (runs.append(cmd), types.SimpleNamespace(returncode=0))[1]

app.weekly_update()
ok(len(runs) == 1 and runs[0][1:4] == ["-m", "pip", "install"], "first start with no record: updates")
ok(time.time() - app.load_settings()["last_update"] < 5, "update time recorded")
app.weekly_update()
ok(len(runs) == 1, "a second start the same week: no update")
s = app.load_settings(); s["last_update"] = int(time.time()) - 8 * 86400; app.write_settings(s)
app.weekly_update()
ok(len(runs) == 2, "8 days later: updates again")
app.subprocess.run = lambda cmd, **kw: types.SimpleNamespace(returncode=1)
s = app.load_settings(); s["last_update"] = 0; app.write_settings(s)
app.weekly_update()
ok(app.load_settings()["last_update"] == 0, "offline/failed update: not recorded, retried next start")
app.WINDOW = types.SimpleNamespace(get_current_url=lambda: "http://127.0.0.1:1/i", real_url="http://127.0.0.1:1/i")
api = app.Api()
api.save.__wrapped__(api, {"last_update": 999, "kbps": 256})
ok(app.load_settings()["last_update"] == 0 and app.load_settings()["kbps"] == 256, "the page can't fake the update time")
app.subprocess.run = real_run
# a real update run against PyPI (already up to date -> quick)
t = time.time()
ok(app.update_engines(), f"real pip update works ({time.time() - t:.0f}s)")
