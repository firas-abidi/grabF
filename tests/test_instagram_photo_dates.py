# group: login
import os, sys, time, tempfile, shutil
from pathlib import Path
from helpers import WORK  # grabF on the path, settings in a temp folder
import app
d = Path(tempfile.mkdtemp())
app.HISTORY = d / "history.json"
ev = []
app.push = lambda e, data: ev.append((e, data))
t0 = time.time()
req = app.clean_request({"url": os.environ["GRABF_TEST_INSTAGRAM_POST"], "mode": "images", "count": 1, "login": True,
                         "login_with": "firefox", "title": "test post"})
app.GalleryJob("g1", req, {**app.DEFAULTS, "out": str(d), "cookies": "firefox"}).run()
last = ev[-1]
print(last[0], str(last[1].get("files") or last[1].get("error"))[:150])
for f in (last[1].get("files") or []):
    age = time.time() - Path(f).stat().st_mtime
    print(("PASS" if age < 120 else "FAIL"), f"file dated {age:.0f} s ago (the download time, not the post's date): {Path(f).name}")
shutil.rmtree(d, ignore_errors=True)
