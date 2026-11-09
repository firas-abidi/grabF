# group: network
import json, subprocess, sys, threading, time
from pathlib import Path
from helpers import WORK  # grabF on the path, settings in a temp folder
import app

SC = WORK
OUT = SC / "featuretest"
OUT.mkdir(exist_ok=True)
app.CONFIG = SC / "cfg8" / "settings.json"
app.HISTORY = SC / "cfg8" / "history.json"
FFPROBE = Path(app.FFMPEG) / "ffprobe.exe"
ok = lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True)
events = []
app.push = lambda e, d: events.append((e, d))
cfg = {**app.DEFAULTS, "out": str(OUT)}


def run(req, cls=app.Job):
    events.clear()
    r = app.clean_request(req)
    cls("j" + str(time.time_ns())[-9:], r, cfg).run()
    return events[-1]


# names
ok(app.safe_name('a<b>:"c/d\\e|f?g*h. ') == "a b c d e f g h", "unsafe characters removed")
ok(app.safe_name("con") == "_con" and app.safe_name("") == "download", "Windows reserved names handled")
(OUT / "Mr. Bean.mp4").write_bytes(b"x")
ok(app.unique_base(OUT, "Mr. Bean") == "Mr. Bean (2)" and app.unique_base(OUT, "Mr") == "Mr", "titles with dots numbered correctly")
(OUT / "Mr. Bean.mp4").unlink()

# a video twice: clean name, then (2)
zoo = {"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "mode": "video", "quality": 144}
e1, e2 = run(zoo), run(zoo)
n1, n2 = e1[1].get("name"), e2[1].get("name")
ok(n1 == "Me at the zoo.mkv" or n1 == "Me at the zoo.mp4", f"clean video name: {n1}")
ok(n2 and n2.startswith("Me at the zoo (2)."), f"second copy numbered, not skipped: {n2}")

# just a part, as mp3
e = run({"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mode": "audio", "afmt": "mp3", "kbps": 192, "clip": [10, 20]})
f = e[1].get("files", [""])[0]
d = float(json.loads(subprocess.run([str(FFPROBE), "-v", "quiet", "-print_format", "json", "-show_format", f],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace").stdout)["format"]["duration"]) if f else 0
ok(e[0] == "done" and 9.5 <= d <= 10.6, f"clip 0:10-0:20 -> {Path(f).name}, {d:.2f}s long")

# a photo post named from its caption
e = run({"url": "https://bsky.app/profile/bsky.app/post/3mzhhvhjfwk22", "mode": "images", "count": 1,
         "title": "We're seeing 200% of our usual data traffic—and climbing!"}, app.GalleryJob)
ok(e[0] == "done" and e[1]["name"].startswith("We're seeing 200% of our usual data traffic—and climbing!"), f"photo named from caption: {e[1].get('name')}")
ok(not any(p.name.startswith(".grab-") for p in OUT.iterdir()), "temp folder cleaned up")

# history
h = json.loads(app.HISTORY.read_text(encoding="utf-8"))
ok(len(h) == 4 and h[0]["mode"] == "images", f"history has all 4 downloads, newest first ({len(h)})")
app.WINDOW = __import__("types").SimpleNamespace(get_current_url=lambda: "http://127.0.0.1:1/i", real_url="http://127.0.0.1:1/i")
api = app.Api()
lst = api.history.__wrapped__(api)
ok(all(x["files"] and not x["gone"] for x in lst), "history entries point at files that exist")
Path(lst[-1]["files"][0]).unlink()
ok(api.history.__wrapped__(api)[-1]["gone"], "a deleted file shows as moved or deleted")
api.forget.__wrapped__(api, lst[0]["id"])
ok(len(api.history.__wrapped__(api)) == 3, "remove one entry")
api.clear_history.__wrapped__(api)
ok(api.history.__wrapped__(api) == [] and any(OUT.iterdir()), "clear history keeps the files")

# at most 3 at once
peak, now = 0, 0
lock = threading.Lock()
class Slow(app.Job):
    def opts(self):
        return {}
real_ydl = app.yt_dlp.YoutubeDL
class FakeYDL:
    def __init__(self, *a, **k): self.params = {"outtmpl": {"default": "x"}}
    def __enter__(self):
        global peak, now
        with lock:
            now += 1; peak = max(peak, now)
        return self
    def __exit__(self, *a):
        global now
        with lock: now -= 1
    def extract_info(self, *a, **k): time.sleep(0.4); return {"_type": "playlist", "entries": []}
    def process_ie_result(self, i, **k): return i
app.yt_dlp.YoutubeDL = FakeYDL
ts = [threading.Thread(target=Slow(f"s{i}", app.clean_request(zoo), cfg).run) for i in range(7)]
[t.start() for t in ts]; [t.join() for t in ts]
app.yt_dlp.YoutubeDL = real_ydl
ok(peak == 3, f"7 downloads started, at most {peak} ran at once")
