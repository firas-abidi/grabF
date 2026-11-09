# group: network
import shutil, sys, types
from pathlib import Path
from helpers import WORK  # grabF on the path, settings in a temp folder
import app
SC = WORK
app.HISTORY = SC / "cfg13" / "history.json"
app.CONFIG = SC / "cfg13" / "settings.json"
ok = lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True)
ev = []
app.push = lambda e, d: ev.append((e, d))

# exact texts the engines and Windows produce for things we can't safely cause here
samples = {
    "ERROR: unable to write data: [Errno 28] No space left on device": "disk is full",
    "[WinError 112] There is not enough space on the disk": "disk is full",
    "ERROR: Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>": "internet connection",
    "ERROR: [youtube] x: Unable to download API page: The read operation timed out": "internet connection",
    "ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader": "isn't available anymore",
    "ERROR: unable to download video data: HTTP Error 429: Too Many Requests": "limiting downloads",
    "ERROR: [youtube] abc: Requested format is not available. Use --list-formats": "quality isn't available",
    "ERROR: unable to download video data: HTTP Error 403: Forbidden": "refused the download",
    "[WinError 3] The system cannot find the path specified: 'E:\\\\grab'": "isn't there anymore",
}
for text, want in samples.items():
    got = app.clean_error(Exception(text))
    ok(want in got, f"{text[:58]:58} -> {got}")
ok("Sign in" in app.clean_error(Exception("ERROR: [youtube] x: Sign in to confirm your age")), "login errors keep their own wording")

# real failures
app.WINDOW = types.SimpleNamespace(get_current_url=lambda: "http://127.0.0.1:1/i", real_url="http://127.0.0.1:1/i")
app.load_settings = lambda: dict(app.DEFAULTS)
api = app.Api()
r = api.probe.__wrapped__(api, "https://no-such-host-for-grab-test.invalid/video/1")
ok(not r["ok"] and "internet connection" in r["error"], f"unreachable site: {r.get('error')}")

zoo = app.clean_request({"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "mode": "video", "quality": 144})
app.Job("e1", zoo, {**app.DEFAULTS, "out": r"C:\Windows\System32\grab-test"}).run()
ok(ev[-1][0] == "failed" and "can't save into that folder" in ev[-1][1]["error"], f"protected folder: {ev[-1][1].get('error')}")

gone = SC / "gonefolder"
shutil.rmtree(gone, ignore_errors=True)
app.Job("e2", zoo, {**app.DEFAULTS, "out": str(gone)}).run()
ok(ev[-1][0] == "done" and gone.is_dir(), "download folder deleted since last time: recreated and saved")
