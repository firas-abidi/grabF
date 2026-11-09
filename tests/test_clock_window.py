# group: window
"""Real window: a wrong-clock error shows the plain message and a button that opens Windows' date & time page."""
import os, sys, threading, time, types
from helpers import WORK  # grabF on the path, settings in a temp folder
import app

ok = lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True)
opened = []
app.os.startfile = lambda p: opened.append(p)
CERT = ("ERROR: [youtube] x: Unable to download API page: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "certificate is not yet valid (_ssl.c:1006); please report this issue on https://github.com/yt-dlp/yt-dlp/issues?q= ")
app.Api._read = staticmethod(lambda url, playlist, browser, video=True, photos=True:
                             {"ok": False, "error": app.clean_error(Exception(CERT))})
app.weekly_update = lambda: None
app.check_app_update = lambda: None
app.warm_up = lambda: None

# the button's own check: unknown names do nothing
api = app.Api()
app.WINDOW = types.SimpleNamespace(get_current_url=lambda: "http://127.0.0.1:1/i", real_url="http://127.0.0.1:1/i")
api.open_fix.__wrapped__(api, "calc.exe")
api.open_fix.__wrapped__(api, None)
ok(opened == [], "open_fix ignores anything but 'clock'")
r = api.probe.__wrapped__(api, "https://www.youtube.com/watch?v=abc")
ok(r.get("fix") == "clock" and r["error"] == app.CLOCK_MSG and "github" not in r["error"], "probe says it plainly and flags the fix")
app.WINDOW = None


def drive():
    time.sleep(7)
    w = app.WINDOW
    w.evaluate_js('document.querySelector("#url").value="https://www.youtube.com/watch?v=abc"; probe("https://www.youtube.com/watch?v=abc")')
    time.sleep(2)
    txt = w.evaluate_js('document.querySelector(".empty .err") ? document.querySelector(".empty .err").innerText : ""')
    ok("date and time look wrong" in txt, "the window shows the clock message")
    ok("open date & time settings" in txt, "and the button under it")
    w.evaluate_js('document.querySelector(".err .fix").click()')
    time.sleep(1.5)
    ok(opened == ["ms-settings:dateandtime"], f"clicking it opens Windows' date & time page ({opened})")
    w.destroy()


threading.Thread(target=drive, daemon=True).start()
app.main()
