# group: network
"""Downloads land in videos / audio / photos inside the 'saving to' folder."""
import shutil, sys, tempfile, threading, time, types
from pathlib import Path
from helpers import WORK  # grabF on the path, settings in a temp folder
import app

ok = lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True)
d = Path(tempfile.mkdtemp())
base = d / "grabF"
app.CONFIG = d / "cfg" / "settings.json"
app.HISTORY = d / "cfg" / "history.json"
app.write_settings({**app.DEFAULTS, "out": str(base)})
app.WINDOW = types.SimpleNamespace(get_current_url=lambda: "http://127.0.0.1:1/i", real_url="http://127.0.0.1:1/i")
done = {}
app.push = lambda e, data: done.__setitem__(data["id"], data) if e in ("done", "failed") else None
api = app.Api()
U = "https://www.youtube.com/watch?v=jNQXAC9IVRw"   # 19 s
api.download.__wrapped__(api, "v1", {"url": U, "mode": "video", "quality": 240})
api.download.__wrapped__(api, "a1", {"url": U, "mode": "audio", "afmt": "mp3", "kbps": 128})
for _ in range(240):
    if "v1" in done and "a1" in done:
        break
    time.sleep(0.5)
v, a = done.get("v1", {}), done.get("a1", {})
ok(v.get("files") and Path(v["files"][0]).parent == base / "videos", f"video saved in videos: {v.get('files') or v.get('error')}")
ok(a.get("files") and Path(a["files"][0]).parent == base / "audio" and a["files"][0].endswith(".mp3"), f"mp3 saved in audio: {a.get('files') or a.get('error')}")

# photos: check which folder the photo job is given (no real download needed)
started = []
real_thread = app.threading.Thread
app.threading.Thread = lambda target, daemon: types.SimpleNamespace(start=lambda: started.append(target.__self__))
api.download.__wrapped__(api, "p1", {"url": "https://bsky.app/profile/x/post/1", "mode": "images", "count": 1})
app.threading.Thread = real_thread
ok(started and Path(started[-1].cfg["out"]) == base / "photos", f"photo posts go to photos: {started[-1].cfg['out'] if started else None}")
ok(Path(app.load_settings()["out"]) == base, "the 'saving to' setting itself is unchanged (still the main folder)")
app.JOBS.clear()
print("folders now:", sorted(p.name for p in base.iterdir()))
