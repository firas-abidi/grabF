# group: offline
"""The old-name move, in a fake home folder: settings, download folder and history paths."""
import json, sys, tempfile
from pathlib import Path
from helpers import WORK  # grabF on the path, settings in a temp folder
import app
ok = lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True)

def setup(out_is_default=True, new_exists=False):
    home = Path(tempfile.mkdtemp())
    (home / "AppData" / "grab").mkdir(parents=True)
    old_out = home / "Downloads" / "grab"; old_out.mkdir(parents=True)
    (old_out / "song.mp3").write_bytes(b"x")
    out = old_out if out_is_default else home / "Music"
    (home / "AppData" / "grab" / "settings.json").write_text(json.dumps({"out": str(out), "kbps": 256}))
    (home / "AppData" / "grab" / "history.json").write_text(json.dumps([{"id": "1", "files": [str(out / "song.mp3")]}]))
    if new_exists:
        (home / "AppData" / "grabF").mkdir()
    app.Path.home = staticmethod(lambda: home)
    app.CONFIG = home / "AppData" / "grabF" / "settings.json"
    app.HISTORY = app.CONFIG.parent / "history.json"
    app.DEFAULTS["out"] = str(home / "Downloads" / "grabF")
    return home

h = setup()
app.move_from_old_name()
s = app.load_settings()
ok(not (h / "AppData" / "grab").exists() and app.CONFIG.exists() and s["kbps"] == 256, "settings folder moved to grabF, settings kept")
ok(s["out"] == str(h / "Downloads" / "grabF") and (h / "Downloads" / "grabF" / "song.mp3").exists()
   and not (h / "Downloads" / "grab").exists(), "download folder renamed to grabF with its files")
ok(app.load_history()[0]["files"] == [str(h / "Downloads" / "grabF" / "song.mp3")], "history points to the moved file")
app.move_from_old_name()
ok(app.load_settings()["kbps"] == 256, "running it again changes nothing")

h = setup(out_is_default=False)
app.move_from_old_name()
ok(app.load_settings()["out"] == str(h / "Music") and (h / "Downloads" / "grab").exists(), "a folder you picked yourself is left alone")

h = setup(new_exists=True)
app.move_from_old_name()
ok((h / "AppData" / "grab").exists(), "if grabF already has its own folder, nothing is touched")
