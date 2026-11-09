# group: offline
"""Attack-style checks against grabF's Python side."""
import sys, os, tempfile, types
from pathlib import Path
from helpers import WORK  # grabF on the path, settings in a temp folder
import app

ok = lambda c, m: print(("PASS " if c else "FAIL ") + m)


def raises(f, *a):
    try:
        f(*a)
        return False
    except Exception:
        return True


# links
ok(app.check_url(" https://youtu.be/x ") == "https://youtu.be/x", "normal link accepted")
for bad in ["--exec calc", "file:///C:/Windows/System32/calc.exe", "javascript:alert(1)", "https://a.com/x y",
            "-o cache.file=x", "", None, 5, "http://", "https://a.com/" + "x" * 5000]:
    ok(raises(app.check_url, bad), f"rejected link {str(bad)[:40]!r}")

# gallery-dl command: link always after "--", never parsed as an option
cmd = app.gallery_cmd("firefox", "https://x.com/a/status/1", "-j")
ok(cmd[-2:] == ["--", "https://x.com/a/status/1"] and "cache.file=null" in cmd and "--config-ignore" in cmd, "gallery-dl args safe")
ok("--cookies-from-browser" not in app.gallery_cmd("chrome --exec evil", "https://a.com/b"), "bogus browser name ignored")

# download requests get rebuilt from known values
r = app.clean_request({"url": "https://youtu.be/x", "mode": "rm -rf", "quality": "1080]+ba/best[", "afmt": "exe",
                       "kbps": 9999, "subs": "en.*,--exec", "playlist": "yes", "count": -4, "evil": 1})
ok(r == {"url": "https://youtu.be/x", "mode": "video", "quality": "best", "compat": False, "afmt": "mp3",
         "kbps": 320, "subs": "", "playlist": False, "count": 0, "login": False, "login_with": "", "clip": None, "title": "", "items": None,
         "subs_mode": "embed", "sub_style": "clean", "sub_size": 100, "sub_outline": True},
   "hostile request cleaned to defaults")
r2 = app.clean_request({"url": "https://youtu.be/x", "clip": [5, "rm"], "title": 7})
ok(r2["clip"] is None and r2["title"] == "", "bad clip and title rejected")
ok(app.clean_request({"url": "https://youtu.be/x", "clip": [90, 30]})["clip"] is None, "clip ending before it starts rejected")
ok(app.clean_request({"url": "https://youtu.be/x", "items": [3, 1, "2,5", -1, True, 3, 0, 10**9]})["items"] == [1, 3],
   "chosen videos: only real positions kept, no text can sneak into the engine's item list")
ok(app.clean_request({"url": "https://youtu.be/x", "subs": "auto:en-orig"})["subs"] == "auto:en-orig"
   and app.clean_request({"url": "https://youtu.be/x", "subs": "auto:en;rm -rf"})["subs"] == ""
   and app.clean_request({"url": "https://youtu.be/x", "subs": "auto:../../x"})["subs"] == "",
   "auto-caption choice accepted only in its exact form")
ok([app.clean_request({"url": "https://youtu.be/x", "sub_size": v})["sub_size"] for v in (150, 9999, "150", 10, True)]
   == [150, 100, 100, 100, 100], "subtitle size: only a whole number from 50 to 250")
ok("FontSize=25.50" in app.style_string("clean", 1.5) and "Outline=1.65" in app.style_string("clean", 1.5),
   "size scales text, outline and shadow together")
r3 = app.clean_request({"url": "https://youtu.be/x", "subs_mode": "burn", "sub_style": "x',force_style='evil"})
ok(r3["subs_mode"] == "burn" and r3["sub_style"] == "clean", "subtitle style can only be one of the five - nothing reaches ffmpeg's filter")
s = app.shift_srt("1\n00:00:40,000 --> 00:00:45,500\nbefore\n\n2\n00:00:47,000 --> 00:00:49,000\ninside\n\n3\n00:01:10,000 --> 00:01:12,000\nafter\n", 43, 53)
ok(s == "1\n00:00:00,000 --> 00:00:02,500\nbefore\n\n2\n00:00:04,000 --> 00:00:06,000\ninside\n", "clip subtitles shifted and trimmed: " + repr(s[:60]))

# settings: the page can't move the download folder or store junk
app.CONFIG = Path(tempfile.mkdtemp()) / "settings.json"
app.WINDOW = types.SimpleNamespace(get_current_url=lambda: "http://127.0.0.1:5555/index.html",
                                   real_url="http://127.0.0.1:5555/index.html")
api = app.Api()
s = api.save({"out": r"C:\Users\someone\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup",
              "cookies": "chrome", "kbps": 192, "afmt": "<script>", "junk": 1})
ok(s["out"] == app.DEFAULTS["out"] and s["cookies"] == "chrome" and s["kbps"] == 192 and s["afmt"] == "mp3" and "junk" not in s,
   "save() refuses folder change + junk, keeps valid values")

# open/show only for files grab downloaded
d = Path(tempfile.mkdtemp())
(d / "song.mp3").write_bytes(b"x")
(d / "payload.bat").write_text("calc")
(d / "invoice.pdf.exe").write_bytes(b"MZ")
kept, blocked = app.quarantine([str(d / "song.mp3"), str(d / "payload.bat"), str(d / "invoice.pdf.exe")])
ok(len(kept) == 1 and blocked == 2 and (d / "payload.bat.blocked").exists() and not (d / "payload.bat").exists(),
   "non-media downloads renamed .blocked")
opened = []
app.os.startfile = lambda p: opened.append(str(p))
api.open_file(r"C:\Windows\System32\calc.exe")
api.open_file(str(d / "payload.bat.blocked"))
api.open_file(str(d / "song.mp3"))
ok(opened == [str((d / "song.mp3").resolve())], "open_file only opens downloaded media")

# calls refused once the window is on any other page
app.WINDOW = types.SimpleNamespace(get_current_url=lambda: "https://evil.example/", real_url="http://127.0.0.1:5555/index.html")
ok(raises(api.clipboard) and raises(api.open_folder) and raises(api.download, "j1", {"url": "https://a.com"}),
   "API refuses calls from a foreign page")
app.WINDOW = types.SimpleNamespace(get_current_url=lambda: "http://127.0.0.1:9999/index.html", real_url="http://127.0.0.1:5555/index.html")
ok(raises(api.init), "API refuses another local port")

# clipboard: only a single link ever comes back
app.WINDOW = types.SimpleNamespace(get_current_url=lambda: "http://127.0.0.1:5555/index.html", real_url="http://127.0.0.1:5555/index.html")
for text, want in [("hunter2 my password", ""), ("see https://youtu.be/dQw4w9WgXcQ later", ""),
                   ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")]:
    app.read_clipboard = lambda t=text: t
    c = api.clipboard()
    ok(c["url"] == want, f"clipboard {text[:30]!r} -> {c}")
ok(not hasattr(app, "is_media_link"), "no automatic link reading left in grab")
