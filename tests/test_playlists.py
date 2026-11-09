# group: network
import json, sys, types
from pathlib import Path
from helpers import WORK  # grabF on the path, settings in a temp folder
import app
SC = WORK
OUT = SC / "pltest"
app.HISTORY = SC / "cfg11" / "history.json"
ok = lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True)
U = "https://www.youtube.com/playlist?list=PLx-_-Kk4c89oOHEDQAojOXzEzemXxoqx6"

app.WINDOW = types.SimpleNamespace(get_current_url=lambda: "http://127.0.0.1:1/i", real_url="http://127.0.0.1:1/i")
app.load_settings = lambda: dict(app.DEFAULTS)
api = app.Api()
r = api.probe.__wrapped__(api, U, True)
ok(r["ok"] and r["info"]["kind"] == "playlist" and r["info"]["count"] == 2, f"playlist read: {r['info'].get('title')}, {r['info'].get('count')} videos")

ev = []
app.push = lambda e, d: ev.append((e, d))
req = app.clean_request({"url": U, "mode": "audio", "afmt": "original", "playlist": True, "count": 2, "title": r["info"]["title"]})
app.Job("pl1", req, {**app.DEFAULTS, "out": str(OUT)}).run()
e, d = ev[-1]
ok(e == "done" and len(d["files"]) == 2, f"both downloaded: {e} {d.get('name') or d.get('error')}")
items = sorted({(x[1].get("item"), x[1].get("of")) for x in ev if x[0] == "progress" and x[1].get("of")})
ok(items == [(1, 2), (2, 2)], f"progress said which video: {items}")
files = [Path(f) for f in d.get("files", [])]
ok(all(f.parent.name == "yt-dlp test - collaborative playlist" for f in files), f"saved in a folder named after the playlist: {files[0].parent.name if files else '-'}")
ok([f.name[:6] for f in sorted(files)] == ["001 - ", "002 - "], f"numbered in playlist order: {[f.name for f in sorted(files)]}")
h = json.loads(app.HISTORY.read_text(encoding="utf-8"))
ok(h[0]["name"] == "2 files" and h[0]["title"] == "yt-dlp test - collaborative playlist", f"history: {h[0]['title']} / {h[0]['name']}")
