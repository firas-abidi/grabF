# group: offline
"""Instagram auto-login: every case, with Firefox and logins simulated (nothing is read from real browsers)."""
import sys, tempfile, types
from pathlib import Path
from helpers import WORK  # grabF on the path, settings in a temp folder
import app

ok = lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True)
d = Path(tempfile.mkdtemp())
app.CONFIG = d / "settings.json"
app.HISTORY = d / "history.json"
app.WINDOW = types.SimpleNamespace(get_current_url=lambda: "http://127.0.0.1:1/i", real_url="http://127.0.0.1:1/i")
app.refused = lambda u: ""
api = app.Api()
IG = "https://www.instagram.com/p/ABC123/"
OTHER = "https://www.example-video-site.com/members/42"
calls = []


def scenario(chosen, firefox_installed, firefox_has_insta=True, chosen_readable=False, anon_ok=False):
    calls.clear()
    app.write_settings({**app.DEFAULTS, "cookies": chosen})
    app.browsers_on_pc = lambda: ([{"id": "firefox", "locked": False}] if firefox_installed else []) + \
        [{"id": "chrome", "locked": True}]
    app.cookie_problem = lambda b: "" if (b == "firefox" or (b == chosen and chosen_readable)) else f"{b} locks its logins"

    def read(url, playlist, browser, video=True, photos=True):
        calls.append(browser)
        if anon_ok and not browser:
            return {"ok": True, "info": {"kind": "images"}}
        if browser == "firefox" and firefox_has_insta:
            return {"ok": True, "info": {"kind": "images"}}
        return {"ok": False, "error": "this site wants you signed in"}
    api._read = read
    return api.probe.__wrapped__(api, scenario.url)


scenario.url = IG
r = scenario("", firefox_installed=True)
ok(r["ok"] and r["info"]["login_with"] == "firefox" and r["info"]["login_auto"] and calls == ["", "firefox"],
   f"Instagram, nothing picked, Firefox signed in: tried without login first, then Firefox ({calls})")
r = scenario("chrome", firefox_installed=True)
ok(r["ok"] and r["info"]["login_with"] == "firefox" and calls == ["", "firefox"], "Instagram, locked Chrome picked: falls back to Firefox")
r = scenario("", firefox_installed=False)
ok(not r["ok"] and r["error"] == app.INSTA_LOGIN_MSG and calls == [""], "Instagram, no Firefox: clear 'sign in with Firefox' message")
r = scenario("", firefox_installed=True, firefox_has_insta=False)
ok(not r["ok"] and "Sign in to instagram.com in firefox" in r["error"], "Instagram, Firefox not signed in to Instagram: says so")
r = scenario("", firefox_installed=True, anon_ok=True)
ok(r["ok"] and calls == [""] and not r["info"].get("login"), "works without a login: no login is used at all")
r = scenario("brave", firefox_installed=True, chosen_readable=True, firefox_has_insta=False)
ok(calls == ["", "brave"], "a readable browser you picked yourself is used, not swapped for Firefox")
scenario.url = OTHER
r = scenario("", firefox_installed=True)
ok(not r["ok"] and "Choose a browser" in r["error"] and calls == [""], "other sites: Firefox is NOT used without you picking it")

# the download uses the browser that read the link
started = []
app.threading = types.SimpleNamespace(Thread=lambda target, daemon: types.SimpleNamespace(start=lambda: started.append(target.__self__)),
                                      Lock=app.threading.Lock, RLock=app.threading.RLock, Semaphore=app.threading.Semaphore,
                                      get_ident=app.threading.get_ident, Event=app.threading.Event, Timer=app.threading.Timer)
app.write_settings({**app.DEFAULTS, "cookies": ""})
api.download.__wrapped__(api, "j1", {"url": IG, "mode": "images", "login": True, "login_with": "firefox"})
ok(started and started[-1].cfg["cookies"] == "firefox", "download uses the Firefox login that read the link")
api.download.__wrapped__(api, "j2", {"url": IG, "mode": "images", "login": False, "login_with": "firefox"})
ok(started[-1].cfg["cookies"] == "", "a link read without a login downloads without one")
ok(app.clean_request({"url": IG, "login_with": "evil --exec"})["login_with"] == "", "unknown browser names are dropped")
app.JOBS.clear()
