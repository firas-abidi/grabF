# group: network
"""Burn every style for real (10 s clip with English subtitles), check timing, files and a frame of each."""
import json, shutil, subprocess, sys, time
from pathlib import Path
from helpers import WORK  # grabF on the path, settings in a temp folder
import app
SC = WORK
OUT = SC / "burntest"
shutil.rmtree(OUT, ignore_errors=True)
app.HISTORY = SC / "cfg18" / "history.json"
ok = lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True)
ev = []
app.push = lambda e, d: ev.append((e, d))
FF, FP = app.ffmpeg_exe(), app.ffmpeg_exe("ffprobe")
print("encoder chosen:", app.burn_encoder()[0], "/", app.burn_encoder()[2])

frames = []
for style in app.SUB_STYLES:
    ev.clear()
    req = app.clean_request({"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mode": "video", "quality": 1080,
                             "subs": "en", "subs_mode": "burn", "sub_style": style, "clip": [43, 53]})
    t = time.time()
    app.Job("b" + style, req, {**app.DEFAULTS, "out": str(OUT / style)}).run()
    e, d = ev[-1]
    if e != "done":
        ok(False, f"{style}: {d.get('error')}"); continue
    f = Path(d["files"][0])
    probe = json.loads(subprocess.run([FP, "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", str(f)],
                                      capture_output=True, text=True, encoding="utf-8").stdout)
    v = next(s for s in probe["streams"] if s["codec_type"] == "video")
    a = [s for s in probe["streams"] if s["codec_type"] == "audio"]
    subs_tracks = [s for s in probe["streams"] if s["codec_type"] == "subtitle"]
    left = sorted(p.name for p in f.parent.iterdir())
    burned = any(x[0] == "progress" and str(x[1].get("stage", "")).startswith("burning") for x in ev)
    ok(f.suffix == ".mp4" and v["codec_name"] == "h264" and a and not subs_tracks and left == [f.name] and burned,
       f"{style:9} {time.time() - t:4.1f}s  {f.name} | {v['codec_name']} {v['height']}p + {a[0]['codec_name'] if a else '-'} | "
       f"{float(probe['format']['duration']):.1f}s long | folder: {left}")
    png = OUT / f"{style}.png"
    subprocess.run([FF, "-y", "-loglevel", "error", "-ss", "4.5", "-i", str(f), "-frames:v", "1", "-vf", "scale=640:-1", str(png)])
    frames.append(png)

# one sheet with a frame of each style
from PIL import Image, ImageDraw
ims = [Image.open(p) for p in frames]
sheet = Image.new("RGB", (640 * 2, 360 * 3), "white")
for i, (im, name) in enumerate(zip(ims, app.SUB_STYLES)):
    x, y = i % 2 * 640, i // 2 * 360
    sheet.paste(im.resize((640, 360)), (x, y))
    ImageDraw.Draw(sheet).text((x + 10, y + 8), name, fill="white")
sheet.save(OUT / "styles.png")
print("sheet:", OUT / "styles.png")
