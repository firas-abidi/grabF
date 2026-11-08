"""Rebuild SUPPORTED_SITES.md from the engines grabF uses (installed versions).

Run from the grabF folder after updating the engines:  py -3 tools/list_sites.py

Sites grabF refuses are left out using the same rules the app uses: anything yt-dlp marks 18+,
and the one-way hashes in app.py. No site names are stored here.
"""
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import gallery_dl
import yt_dlp
from gallery_dl import extractor as gx
from yt_dlp.extractor import gen_extractor_classes

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import host_blocked, site_blocked  # noqa: E402

OUT = ROOT / "SUPPORTED_SITES.md"

# yt-dlp marks a site 18+ if any of its test videos is age-restricted, which also catches big
# mainstream sites with the odd restricted video. These stay listed (grabF still refuses the
# individual 18+ videos on them).
MAINSTREAM = {"bluesky", "cda", "dailymotion", "goodgame", "kick", "mave", "mxplayer", "newgrounds", "niconico",
              "niconicochannelplus", "ondemandkorea", "picarto", "pr0gramm", "reddit", "twitter", "vevo",
              "yandexvideo", "youtube", "jeuxvideo", "paramountpressexpress", "yandexvideopreview"}

# --- yt-dlp: video and audio ---
video = defaultdict(lambda: {"desc": "", "broken": True})
for ie in gen_extractor_classes():
    if ie.IE_DESC is False or ie.IE_NAME.lower() in ("generic", "genericie"):
        continue
    fam = ie.IE_NAME.split(":")[0].strip()
    f = fam.lower()
    if not fam or f.startswith("generic"):
        continue
    if ((getattr(ie, "age_limit", 0) or 0) >= 18 and f not in MAINSTREAM) or site_blocked(f):
        continue
    v = video[f]
    v["name"] = v.get("name") or fam
    if ie.working():
        v["broken"] = False
    if not v["desc"] and isinstance(ie.IE_DESC, str):
        v["desc"] = ie.IE_DESC

# --- gallery-dl: photos, carousels, galleries ---
photos = {}
for cls in gx._list_classes():
    cat = getattr(cls, "category", "")
    if not cat or cat in ("directlink", "generic", "noop", "recursive", "test", "ytdl", "oauth"):
        continue
    ex = getattr(cls, "example", "") or ""
    host = urlsplit(ex).netloc.lower().removeprefix("www.") if ex.startswith("http") else ""
    if site_blocked(cat) or (host and host_blocked(host)):
        continue
    if cat == "civitai":
        host = "civitai.com"
    photos.setdefault(cat, host or photos.get(cat, ""))

working = {k: v for k, v in video.items() if not v["broken"]}
broken = {k: v for k, v in video.items() if v["broken"]}

POPULAR = [
    ("YouTube", "videos, shorts, music, playlists, channels", "youtube", None),
    ("TikTok", "videos, sounds; photo slideshows", "tiktok", "tiktok"),
    ("Instagram", "reels and videos without login; photos and carousels need a login", "instagram", "instagram"),
    ("X / Twitter", "videos and GIFs; photos need a login", "twitter", "twitter"),
    ("Facebook", "public videos and reels", "facebook", "facebook"),
    ("Pinterest", "video pins and image pins", "pinterest", "pinterest"),
    ("Reddit", "videos and galleries (Reddit now usually wants a login)", "reddit", "reddit"),
    ("Bluesky", "videos and photos", "bluesky", "bluesky"),
    ("Threads", "not supported by either engine", None, None),
    ("Snapchat", "Spotlight videos (currently broken in the engine)", "snapchatspotlight", None),
    ("Twitch", "clips and past broadcasts", "twitch", "twitch"),
    ("Vimeo", "videos (some need a login)", "vimeo", None),
    ("SoundCloud", "tracks, sets, likes", "soundcloud", None),
    ("Bandcamp", "tracks and albums", "bandcamp", None),
    ("Dailymotion", "videos and playlists", "dailymotion", None),
    ("LinkedIn", "public post videos", "linkedin", None),
    ("Tumblr", "videos and photos", "tumblr", "tumblr"),
    ("Imgur", "videos, GIFs and albums", "imgur", "imgur"),
    ("9GAG", "videos and images", "9gag", None),
    ("Bilibili", "videos", "bilibili", None),
    ("Kick", "clips and VODs", "kick", None),
    ("Rumble", "videos", "rumble", None),
    ("VK", "videos and photos", "vk", "vk"),
    ("Flickr", "photos and videos", "flickr", "flickr"),
    ("DeviantArt", "art and galleries", None, "deviantart"),
    ("ArtStation", "artwork", None, "artstation"),
]
TESTED = {"youtube", "tiktok", "instagram", "pinterest", "bluesky", "dailymotion", "9gag", "imgur", "soundcloud",
          "facebook", "linkedin"}

lines = [
    "# Sites grabF can download from",
    "",
    f"Generated from the engines grabF uses: **yt-dlp {yt_dlp.version.__version__}** for video and audio, "
    f"**gallery-dl {gallery_dl.version.__version__}** for photos, carousels and galleries. "
    "Sites change their pages all the time, so a site on this list can break until the engines catch up - "
    "press **update** in grabF when that happens.",
    "",
    f"- **{len(working)}** video/audio sites (plus {len(broken)} currently marked broken in yt-dlp)",
    f"- **{len(photos)}** photo/gallery sites",
    "- Anything not listed here may still work: grabF also tries a site's plain page for an embedded video.",
    "",
    "grabF refuses adult content (anything marked 18+) and a set of sites that host leaked paid content, "
    "pirated material or unofficial scans. Those are not listed here, and the app won't download from them.",
    "",
    "## The popular ones",
    "",
    "| Site | What you can get | Video engine | Photo engine | Checked |",
    "|---|---|:-:|:-:|:-:|",
]
for name, what, v, p in POPULAR:
    vv = "✓" if v and v in working else ("broken" if v and v in broken else "–")
    pp = "✓" if p and p in photos else "–"
    t = "✓" if (v in TESTED or p in TESTED) else ""
    lines.append(f"| {name} | {what} | {vv} | {pp} | {t} |")

lines += ["", "**Checked** = grabF read a real public post from that site during development (YouTube and Bluesky "
          "were also downloaded end to end). Unchecked rows are supported by the engines but weren't tried here. "
          "Login notes: Chrome, Edge and Brave lock their logins, so login-only posts need Firefox "
          "(see the app's 'signed in with').", ""]

lines += [f"## Video and audio ({len(working)} sites)", "", "Name as yt-dlp knows it, with its description where it has one.", ""]
for k in sorted(working, key=str.lower):
    v = working[k]
    d = v["desc"] if v["desc"] and v["desc"].lower() != v["name"].lower() else ""
    lines.append(f"- {v['name']}" + (f" - {d}" if d else ""))

lines += ["", f"## Photos, carousels and galleries ({len(photos)} sites)", ""]
for cat in sorted(photos, key=str.lower):
    lines.append(f"- {cat}" + (f" - {photos[cat]}" if photos[cat] else ""))

lines += ["", f"## Marked broken in yt-dlp right now ({len(broken)})", "",
          "These have an extractor but yt-dlp itself flags them as not working at the moment.", ""]
lines.append(", ".join(sorted((v["name"] for v in broken.values()), key=str.lower)))
lines.append("")

OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"{len(working)} video/audio, {len(broken)} broken, {len(photos)} photo sites -> {OUT.name}")
