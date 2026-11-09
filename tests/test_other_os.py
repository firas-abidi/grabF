# group: offline
"""Switch grabF into 'Mac' and 'Linux' mode on this PC and check every non-Windows branch does the right thing
(commands are captured, never run)."""
import sys, types, tempfile
from pathlib import Path
from helpers import WORK  # grabF on the path, settings in a temp folder
import app

ok = lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True)
ran = []


class FakePopen:
    def __init__(self, cmd, **kw):
        ran.append(cmd)


real = dict(WINDOWS=app.WINDOWS, MAC=app.MAC, Popen=app.subprocess.Popen, run=app.subprocess.run, which=app.shutil.which,
            home=Path.home, FFMPEG=app.FFMPEG)
app.subprocess.Popen = FakePopen
tmp = Path(tempfile.mkdtemp())

for mode in ("mac", "linux"):
    app.WINDOWS, app.MAC = False, mode == "mac"
    Path.home = staticmethod(lambda: tmp / mode)
    ran.clear()

    # settings folder
    d = app._config_dir()
    want = tmp / mode / "Library" / "Application Support" / "grabF" if mode == "mac" else None
    ok((d == want) if mode == "mac" else d.name == "grabF" and ".config" in str(d) or "XDG" in str(d), f"{mode}: settings in {d}")

    # ffmpeg path without .exe
    app.FFMPEG = "/opt/homebrew/bin"
    ok(app.ffmpeg_exe().replace("\\", "/").endswith("/opt/homebrew/bin/ffmpeg"), f"{mode}: ffmpeg -> {app.ffmpeg_exe()}")
    app.FFMPEG = real["FFMPEG"]

    # open a file / folder
    app.open_with_system("/x/song.mp3")
    ok(ran[-1] == ["open" if mode == "mac" else "xdg-open", "/x/song.mp3"], f"{mode}: open file -> {ran[-1]}")

    # show in folder
    f = tmp / "clip.mp4"; f.write_bytes(b"x")
    app.KNOWN_FILES.add(str(f.resolve()))
    app.WINDOW = types.SimpleNamespace(get_current_url=lambda: "http://127.0.0.1:1/i", real_url="http://127.0.0.1:1/i")
    api = app.Api()
    api.reveal.__wrapped__(api, str(f))
    exp = ["open", "-R", str(f.resolve())] if mode == "mac" else ["xdg-open", str(f.resolve().parent)]
    ok(ran[-1] == exp, f"{mode}: show in folder -> {ran[-1]}")

    # notification: words passed as arguments, not inside the script
    app.shutil.which = lambda c: "/usr/bin/" + c
    app.notify("Done", 'evil" & do shell script "x')
    cmd = ran[-1]
    if mode == "mac":
        ok(cmd[0] == "osascript" and cmd[-1] == 'evil" & do shell script "x' and all('evil' not in c for c in cmd[:-1]),
           f"{mode}: notification text only as an argument")
    else:
        ok(cmd[:3] == ["notify-send", "--app-name=grabF", "--"] and cmd[-1].startswith("evil"), f"{mode}: notify-send used")

    # clipboard: only on 'paste'; reads the system tool's output
    app.subprocess.run = lambda cmd, **kw: (ran.append(cmd), types.SimpleNamespace(stdout="https://youtu.be/dQw4w9WgXcQ"))[1]
    got = app.read_clipboard()
    ok(got == "https://youtu.be/dQw4w9WgXcQ" and ran[-1][0] == ("pbpaste" if mode == "mac" else "wl-paste"), f"{mode}: clipboard via {ran[-1][0]}")
    r = api.clipboard.__wrapped__(api)
    ok(r["url"] == "https://youtu.be/dQw4w9WgXcQ", f"{mode}: paste button gets the link")
    app.subprocess.run = real["run"]
    app.shutil.which = lambda c: None
    ok(app.read_clipboard() == "", f"{mode}: no clipboard tool -> nothing, no crash")
    app.shutil.which = real["which"]

    # browsers with logins
    base = tmp / mode
    (base / ("Library/Application Support/Firefox/Profiles" if mode == "mac" else ".mozilla/firefox")).mkdir(parents=True, exist_ok=True)
    (base / ("Library/Application Support/Google/Chrome" if mode == "mac" else ".config/google-chrome")).mkdir(parents=True, exist_ok=True)
    b = app.browsers_on_pc()
    ok({x["id"] for x in b} == {"firefox", "chrome"} and not any(x["locked"] for x in b), f"{mode}: browsers found {b}")

    # window: resize from an edge, bigger / back
    moves = []
    W = types.SimpleNamespace(x=100, y=50, width=620, height=820, get_current_url=lambda: "http://127.0.0.1:1/i",
                              real_url="http://127.0.0.1:1/i", resize=lambda w, h: moves.append(("size", w, h)),
                              move=lambda x, y: moves.append(("move", x, y)), maximize=lambda: moves.append("max"),
                              restore=lambda: moves.append("restore"))
    app.WINDOW = W
    api.resize_start.__wrapped__(api)
    api.resize_to.__wrapped__(api, "se", 80, -1000)
    ok(moves[-2:] == [("size", 700, app.MIN_H), ("move", 100, 50)], f"{mode}: corner drag, never below the minimum: {moves[-2:]}")
    api.resize_to.__wrapped__(api, "w", 50, 0)
    ok(moves[-2:] == [("size", 570, 820), ("move", 150, 50)], f"{mode}: left edge drag {moves[-2:]}")
    app._before_big = None
    ok(app.toggle_big() is True and moves[-1] == "max", f"{mode}: bigger = maximise")
    ok(app.toggle_big() is False and moves[-1] == "restore", f"{mode}: back = restore")

    # start size / placing: no Windows calls
    ok(app.start_geometry() == {"width": app.BASE_W, "height": app.BASE_H}, f"{mode}: start size")
    shown = []
    W.show = lambda: shown.append(1)
    app.warm_up = app.weekly_update = app.check_app_update = lambda: None
    app.place_and_show()
    ok(shown == [1], f"{mode}: window shows without Windows calls")

app.WINDOWS, app.MAC = real["WINDOWS"], real["MAC"]
app.subprocess.Popen = real["Popen"]
Path.home = real["home"]
ok(app.WINDOWS and app._config_dir() == Path(app.os.environ["APPDATA"]) / "grabF", "back on Windows: settings in %APPDATA%\\grabF")
