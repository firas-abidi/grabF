# group: network
"""Blocking checks. Prints counts and PASS/FAIL only - never a site name."""
import sys, types
from helpers import WORK  # grabF on the path, settings in a temp folder
import app
from gallery_dl import extractor as gx

ok = lambda c, m: print(("PASS " if c else "FAIL ") + m)
app.WINDOW = types.SimpleNamespace(get_current_url=lambda: "http://127.0.0.1:1/i", real_url="http://127.0.0.1:1/i")
app.load_settings = lambda: dict(app.DEFAULTS)
api = app.Api()

# 1. mainstream links are not touched
fine = ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ", "https://www.tiktok.com/@a/video/123",
        "https://www.instagram.com/p/abc/", "https://bsky.app/profile/bsky.app/post/3mzhhvhjfwk22",
        "https://www.pinterest.com/pin/664281013778109217/", "https://x.com/a/status/1", "https://www.reddit.com/r/a/comments/b/c/",
        "https://soundcloud.com/a/b", "https://www.dailymotion.com/video/x5kesuj", "https://imgur.com/A61SaA1",
        "https://www.deviantart.com/a/art/b-1", "https://www.webtoons.com/en/x/y/list?title_no=1"]
ok(all(app.refused(u) == "" for u in fine), f"{len(fine)} mainstream links allowed")

# 2. every blocked photo site's own example link is refused (counted, not named)
examples = {}
for cls in gx._list_classes():
    if app.site_blocked(getattr(cls, "category", "")) and (getattr(cls, "example", "") or "").startswith("http"):
        examples.setdefault(cls.category, cls.example)
hit = sum(1 for u in examples.values() if app.refused(u))
ok(hit == len(examples), f"blocked sites refused: {hit}/{len(examples)}")

# 3. ...including on a mirror subdomain and with the www. prefix
one = next(iter(examples.values()))
from urllib.parse import urlsplit
host = urlsplit(one).hostname.removeprefix("www.")
ok(app.host_blocked("www." + host) and app.host_blocked("cdn2.media." + host), "subdomains of a blocked host are refused")

# 4. probe, download and clipboard all refuse, without fetching anything
calls = []
real_read = app.Api.__dict__["_read"]  # keep the staticmethod wrapper itself
app.Api._read = staticmethod(lambda *a: calls.append(a) or {"ok": False, "error": "fetched!"})
r = api.probe.__wrapped__(api, one)
ok(r == {"ok": False, "error": app.BLOCK_MSG} and not calls, "probe refuses before any network request")
app.Api._read = real_read
events = []
app.push = lambda e, d: events.append((e, d))
api.download.__wrapped__(api, "j1", {"url": one, "mode": "images"})
ok(events and events[-1][1]["error"] == app.BLOCK_MSG and "j1" not in app.JOBS, "download refuses too")
app.read_clipboard = lambda: one
ok(api.clipboard.__wrapped__(api) == {"url": ""}, "a copied blocked link is ignored, not even filled in")

# 5. anything marked 18+ is refused on any site, listed or not
class FakeYDL:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def extract_info(self, *a, **k): return {"id": "x", "title": "t", "age_limit": 18, "formats": []}
    def sanitize_info(self, i): return i
real = app.yt_dlp.YoutubeDL
app.yt_dlp.YoutubeDL = FakeYDL
r = app.Api._read("https://some-unlisted-site.example/video/1", False, "")
app.yt_dlp.YoutubeDL = real
ok(r == {"ok": False, "error": app.ADULT_MSG}, "18+ content refused on an unlisted site")
ok(app.base_opts("")["age_limit"] == 17, "yt-dlp told to skip 18+ entries (playlists, downloads)")

# 6. normal use still works
r = api.probe.__wrapped__(api, "https://www.youtube.com/watch?v=jNQXAC9IVRw")
ok(r["ok"] and r["info"]["title"] == "Me at the zoo", "a normal YouTube link still reads fine")
