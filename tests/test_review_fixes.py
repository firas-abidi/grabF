# group: offline
import sys, types, json, tempfile
from pathlib import Path
from helpers import WORK  # grabF on the path, settings in a temp folder
import app
ok = lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True)
d = Path(tempfile.mkdtemp())
f = d / "x.json"
app.write_json(f, {"a": 1})
ok(json.loads(f.read_text()) == {"a": 1} and not (d / "x.json.tmp").exists(), "write_json writes and leaves no temp file")
app.write_json(f, [1, 2])
ok(json.loads(f.read_text()) == [1, 2], "write_json replaces an existing file")
def boom(_): raise RuntimeError("window is gone")
app.WINDOW = types.SimpleNamespace(evaluate_js=boom)
try:
    app.push("done", {"id": "x"}); ok(True, "push after the window closed doesn't crash the download thread")
except Exception as e:
    ok(False, f"push raised {e}")
app.WINDOW = None
ok(app.shift_srt("1\n00:00:05,000 --> 00:00:08,000\nhi\n\n2\n00:00:20,000 --> 00:00:22,000\nbye", 4, 10)
   == "1\n00:00:01,000 --> 00:00:04,000\nhi\n", "shift_srt still cuts and shifts correctly")
from collections import deque
ok(isinstance(app.deque(maxlen=20), deque), "gallery log is a bounded list")
