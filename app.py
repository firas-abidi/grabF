"""grabF - paste a link, pick a quality, download.

yt-dlp and gallery-dl do the downloading; pywebview shows web/index.html in a native window.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import functools
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

import webview


class _LazyYtDlp:
    """yt-dlp takes ~0.4 s to import. Loading it on first use (or in the background while the window
    opens, see warm_up) means the window doesn't wait for it."""

    def __getattr__(self, name):
        import yt_dlp as real
        import yt_dlp.utils  # noqa: F401  (makes yt_dlp.utils available)
        return getattr(real, name)


yt_dlp = _LazyYtDlp()


# Our own handles to user32/kernel32. ctypes.windll is shared with pywebview, and setting argtypes
# on it changes pywebview's calls too (that broke window dragging once).
USER32 = ctypes.WinDLL("user32") if os.name == "nt" else None
KERNEL32 = ctypes.WinDLL("kernel32") if os.name == "nt" else None
# Windows is the main target. On macOS / Linux the app runs from source, and every Windows-only
# call below has a fallback for those.
WINDOWS = os.name == "nt"
MAC = sys.platform == "darwin"


def open_with_system(path) -> None:
    """Open a file or folder the way double-clicking it would."""
    if WINDOWS:
        os.startfile(path)
    else:
        subprocess.Popen(["open" if MAC else "xdg-open", str(path)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


VERSION = "1.0.1"
# the GitHub repo whose releases are checked for a newer grabF ("" = no check at all)
UPDATE_REPO = "firas-abidi/grabF"
API_BASE = "https://api.github.com"
# the only two outside pages the window can open (plus a verified release page, see check_app_update)
LINKS = {"contact": "https://github.com/firas-abidi", "donate": "https://ko-fi.com/firaspanda"}

HERE = Path(__file__).parent
WEB = HERE / "web"  # the only folder pywebview's local server can hand out

# --- what the page is allowed to ask for ---
BROWSERS = {"", "firefox", "chrome", "edge", "brave", "opera", "vivaldi", "chromium"} \
    | ({"safari"} if sys.platform == "darwin" else set())
AFMTS = {"mp3", "m4a", "flac", "wav", "opus", "original"}
KBPS = {320, 256, 192, 128}
# files grabF will ever offer to open; anything else that lands in the folder is renamed .blocked
MEDIA_EXT = {
    "mp4", "mkv", "webm", "mov", "m4v", "avi", "flv", "3gp", "ts",
    "mp3", "m4a", "opus", "ogg", "oga", "flac", "wav", "aac", "weba", "mka",
    "jpg", "jpeg", "png", "gif", "webp", "avif", "heic", "bmp", "tif", "tiff",
    "srt", "vtt", "ass",
}
KNOWN_FILES: set[str] = set()  # files this session downloaded - the only ones open/show will touch
SUB_LANG = re.compile(r"^(auto:)?[A-Za-z0-9][A-Za-z0-9_-]{0,23}$")  # 'en', or 'auto:en-orig' for auto-captions


def check_url(url) -> str:
    """A single http(s) link, nothing else - it ends up as a program argument."""
    if not isinstance(url, str):
        raise ValueError("not a link")
    url = url.strip()
    parts = urlsplit(url)
    if len(url) > 4000 or parts.scheme not in ("http", "https") or not parts.netloc or re.search(r"\s", url):
        raise ValueError("that isn't a web link")
    return url


def clean_request(req) -> dict:
    """Rebuild the download request from known-good values only."""
    if not isinstance(req, dict):
        raise ValueError("bad request")
    mode = req.get("mode") if req.get("mode") in ("video", "audio", "images") else "video"
    q = req.get("quality")
    quality = q if isinstance(q, int) and not isinstance(q, bool) and 1 <= q <= 10000 else "best"
    subs = req.get("subs") if isinstance(req.get("subs"), str) and SUB_LANG.match(req.get("subs")) else ""
    count = req.get("count") if isinstance(req.get("count"), int) and 0 <= req.get("count") <= 100000 else 0
    clip = req.get("clip")
    if not (isinstance(clip, list) and len(clip) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                             for x in clip) and 0 <= clip[0] < clip[1] <= 172800):
        clip = None
    title = req.get("title") if isinstance(req.get("title"), str) else ""
    items = req.get("items") if isinstance(req.get("items"), list) else []
    return {
        "clip": [float(clip[0]), float(clip[1])] if clip else None,  # seconds: download only this part
        "title": title[:200],
        "url": check_url(req.get("url")),
        "mode": mode,
        "quality": quality,
        "compat": req.get("compat") is True,
        "afmt": req.get("afmt") if req.get("afmt") in AFMTS else "mp3",
        "kbps": req.get("kbps") if req.get("kbps") in KBPS else 320,
        "subs": subs,
        "subs_mode": req.get("subs_mode") if req.get("subs_mode") in SUB_MODES else "embed",
        "sub_style": req.get("sub_style") if req.get("sub_style") in SUB_STYLES else "clean",
        # text size in percent of the style's normal size (the slider)
        "sub_size": req.get("sub_size") if isinstance(req.get("sub_size"), int) and not isinstance(req.get("sub_size"), bool)
        and 50 <= req.get("sub_size") <= 250 else 100,
        # False = burn the words without their dark edge and shadow (boxed keeps its box either way)
        "sub_outline": req.get("sub_outline") is not False,
        "playlist": req.get("playlist") is True,
        "count": count,
        "login": req.get("login") is True,  # only true when reading the link needed your login
        # the browser whose login read the link, so the download uses the same one
        "login_with": req.get("login_with") if req.get("login_with") in BROWSERS else "",
        # chosen playlist videos by position (1 = first); None = the whole playlist
        "items": sorted({i for i in items if isinstance(i, int) and not isinstance(i, bool) and 1 <= i <= 100000})[:1000]
                 or None,
    }


def quarantine(files: list[str]) -> tuple[list[str], int]:
    """Keep media, defuse anything else (an .exe, .bat, .lnk ... dressed up as a post)."""
    kept, blocked = [], 0
    for f in files:
        p = Path(f)
        if p.suffix.lower().lstrip(".") in MEDIA_EXT:
            kept.append(str(p.resolve()))
            continue
        try:
            p.rename(p.with_name(p.name + ".blocked"))
        except OSError:
            p.unlink(missing_ok=True)
        blocked += 1
    KNOWN_FILES.update(kept)
    return kept, blocked


def _config_dir() -> Path:
    if WINDOWS:
        return Path(os.environ.get("APPDATA", Path.home())) / "grabF"
    if MAC:
        return Path.home() / "Library" / "Application Support" / "grabF"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "grabF"


CONFIG = _config_dir() / "settings.json"
DEFAULTS = {
    "out": str(Path.home() / "Downloads" / "grabF"),
    "cookies": "",
    "mode": "video",
    "quality": "best",
    "compat": False,
    "afmt": "mp3",
    "kbps": 320,
    "subs_mode": "embed",
    "sub_style": "clean",
    "sub_size": 100,
    "sub_outline": True,
}
NAME = "%(title).180B.%(ext)s"
KIND_FOLDERS = {"video": "videos", "audio": "audio", "images": "photos"}  # one folder per kind, inside 'saving to'
LIST_NAME = "%(playlist_title,playlist_id|playlist).80B/%(playlist_index|0)03d - %(title).160B.%(ext)s"
HISTORY = CONFIG.parent / "history.json"
SLOTS = threading.Semaphore(3)  # at most 3 downloads at once; the rest wait their turn
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(text: str, limit: int = 150) -> str:
    """A readable Windows-safe file name from a title."""
    s = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', " ", text or "")
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")[:limit].rstrip(". ")
    if not s:
        s = "download"
    if s.split(".")[0].upper() in RESERVED:
        s = "_" + s
    return s


_reserved: set[str] = set()  # names claimed by downloads still running (folder|name, lowercase)
_reserve_lock = threading.Lock()


def reserve_base(folder: Path, base: str) -> str:
    """Pick a free name and hold it until release_base(), so two downloads of the same title at the same
    moment can't end up with the same name (and can't clean up each other's files)."""
    with _reserve_lock:
        name = unique_base(folder, base)
        _reserved.add(f"{folder}|{name}".lower())
        return name


def release_base(folder: Path, base: str) -> None:
    with _reserve_lock:
        _reserved.discard(f"{folder}|{base}".lower())


def unique_base(folder: Path, base: str) -> str:
    """'Title', or 'Title (2)', 'Title (3)'... so a new download never replaces or gets mistaken for an old one."""
    names = [p.name.lower() for p in folder.iterdir()] if folder.is_dir() else []
    tail = re.compile(r"^\.[a-z0-9]{1,6}(\.[a-z0-9]{1,6}){0,2}$")  # .mp4, .f137.mp4, .mp3.part - not ' Bean.mp4'
    used = lambda b: f"{folder}|{b}".lower() in _reserved or \
        any(n == b.lower() or (n.startswith(b.lower()) and tail.match(n[len(b):])) for n in names)
    if not used(base):
        return base
    n = 2
    while used(f"{base} ({n})"):
        n += 1
    return f"{base} ({n})"


def mmss(sec: float) -> str:
    """5:00, 1:02:30 - how times are written on screen."""
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Countdown:
    """'Time left' that ticks down steadily. The raw estimate swings with every change in speed; this only drops
    to a lower estimate, and only goes back up when the estimate stays clearly higher for a few seconds."""
    WARMUP, PATIENCE = 1.5, 3.0  # seconds: no guess while the speed settles; how long a slowdown must last

    def __init__(self):
        self.began = time.time()
        self.value: float | None = None
        self.at = 0.0
        self.higher_since = 0.0

    def update(self, estimate: float | None) -> int | None:
        now = time.time()
        if estimate is None or now - self.began < self.WARMUP:
            return None
        if self.value is None:
            current = estimate
        else:
            current = max(0.0, self.value - (now - self.at))  # where the countdown is by now
            if estimate < current:
                current, self.higher_since = estimate, 0.0
            elif estimate > current * 1.25 + 2:
                self.higher_since = self.higher_since or now
                if now - self.higher_since >= self.PATIENCE:
                    current, self.higher_since = estimate, 0.0
            else:
                self.higher_since = 0.0
        self.value, self.at = current, now
        return round(current) if current < 90 else int(round(current / 5) * 5)


def clock(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}h{m:02d}m{s:02d}" if h else f"{m}m{s:02d}"


# --- download history (kept in %APPDATA%\grabF, on this PC only) ---
# The page can call several API functions at once (each on its own thread), so every read and
# write of these files goes through a lock. RLock: a save reads the file first while holding it.
_history_lock = threading.RLock()
_settings_lock = threading.RLock()


def write_json(path: Path, data, indent: int = 1) -> None:
    """Write to a temp file next to it, then swap it in, so the file is never half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=indent)
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for _ in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # briefly held by antivirus / OneDrive
            time.sleep(0.05)
    tmp.unlink(missing_ok=True)
    path.write_text(text, encoding="utf-8")  # still busy: write in place rather than lose the change


def load_history() -> list[dict]:
    with _history_lock:
        try:
            h = json.loads(HISTORY.read_text(encoding="utf-8"))
            return [e for e in h if isinstance(e, dict)] if isinstance(h, list) else []
        except Exception:
            return []


def add_history(entry: dict) -> None:
    with _history_lock:
        h = [entry] + load_history()
        write_json(HISTORY, h[:500])


def notify(title: str, text: str) -> None:
    """A Windows notification, only when grabF isn't the window you're looking at."""
    if not WINDOWS:
        _notify_other(title, text)
        return
    if not WINDOW:
        return
    try:
        hwnd = int(WINDOW.native.Handle.ToInt64())
        if USER32.GetForegroundWindow() == hwnd:
            return
        # text goes in through environment variables, never pasted into the script itself
        script = (
            "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
            "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime]|Out-Null;"
            "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;"
            "$x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text/><text/></binding></visual></toast>');"
            "$t=$x.GetElementsByTagName('text');$t.Item(0).InnerText=$env:GRAB_T1;$t.Item(1).InnerText=$env:GRAB_T2;"
            f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}').Show("
            "[Windows.UI.Notifications.ToastNotification]::new($x))"
        )
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         env={**os.environ, "GRAB_T1": title[:120], "GRAB_T2": text[:200]},
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


WINDOW: webview.Window | None = None


def _notify_other(title: str, text: str) -> None:
    """Mac / Linux notification. The words go in as arguments, never into the script text."""
    try:
        if MAC:
            cmd = ["osascript", "-e", "on run argv",
                   "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                   "-e", "end run", title[:120], text[:200]]
        elif shutil.which("notify-send"):
            cmd = ["notify-send", "--app-name=grabF", "--", title[:120], text[:200]]
        else:
            return
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# --- helpers ---
def find_ffmpeg() -> str | None:
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return None
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    spots = [HERE / "bin", HERE / "ffmpeg", HERE / "ffmpeg" / "bin", local / "Microsoft" / "WinGet" / "Links"]
    if not WINDOWS:  # apps opened from the Dock don't get the terminal's PATH
        spots += [Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/usr/bin")]
    pkgs = local / "Microsoft" / "WinGet" / "Packages"
    if pkgs.is_dir():
        spots += sorted(pkgs.glob("*FFmpeg*/*/bin"), reverse=True)
    for s in spots:
        if (s / "ffmpeg.exe").is_file() or (s / "ffmpeg").is_file():
            return str(s)
    return "missing"


FFMPEG = find_ffmpeg()


def find_deno() -> str | None:
    """yt-dlp needs a JavaScript runtime (deno) to read YouTube fully. Bundled in bin\\ when installed."""
    if shutil.which("deno"):
        return None
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    for d in [HERE / "bin", *sorted((local / "Microsoft" / "WinGet" / "Packages").glob("DenoLand.Deno*"))]:
        if (d / "deno.exe").is_file():
            return str(d)
    if not WINDOWS:
        for d in (HERE / "bin", Path.home() / ".deno" / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
            if (d / "deno").is_file():
                return str(d)
    return None


# some engine checks (like 'download only part of a video') look on PATH and ignore ffmpeg_location,
# and yt-dlp finds deno on PATH - so put grabF's own tools first
for _tool_dir in (find_deno(), FFMPEG if FFMPEG != "missing" else None):
    if _tool_dir:
        os.environ["PATH"] = _tool_dir + os.pathsep + os.environ.get("PATH", "")
# subprocesses (gallery-dl, pip) run with the console python next to pythonw, hidden
PYEXE = str(Path(sys.executable).with_name("python.exe")) if Path(sys.executable).name.lower() == "pythonw.exe" \
    and Path(sys.executable).with_name("python.exe").is_file() else sys.executable


# engine messages -> what happened and what to do, first match wins
CLOCK_MSG = ("This PC's date and time look wrong, so secure sites can't be checked. Turn on 'Set time automatically' "
             "in Windows' date & time settings, then try again.")


def with_fix(r: dict) -> dict:
    """Errors the user can fix in one click get a 'fix' key; the page shows a button for it."""
    if not r.get("ok") and r.get("error") == CLOCK_MSG:
        r["fix"] = "clock"
    return r


PLAIN = [
    # a PC clock set to the past (or far future) makes every secure site's certificate look invalid
    (r"certificate is not yet valid|certificate has expired", CLOCK_MSG),
    (r"certificate_verify_failed|certificate verify failed|self[- ]signed certificate",
     "A secure connection couldn't be checked. This is usually an antivirus or a network (school, work, public Wi-Fi) "
     "that inspects web traffic, or a wrong date and time on this PC. Try another network, or check the PC's clock."),
    (r"(ffmpeg|ffprobe)[^\n]{0,40}not (found|installed)|ffmpeg is not installed",
     "ffmpeg is missing, so this can't be merged or converted. Reinstall grabF, or install ffmpeg."),
    (r"no space left|errno 28|not enough space on the disk|winerror 112",
     "Your disk is full. Free up some space, or pick another folder next to 'saving to' at the bottom."),
    (r"permission denied|errno 13|access is denied|winerror 5\b",
     "grabF can't save into that folder. Pick another one next to 'saving to' at the bottom."),
    (r"cannot find the path|winerror 3\b|winerror 21\b|device is not ready",
     "The download folder isn't there anymore (was it on a drive that's been unplugged?). Pick another one next to 'saving to' at the bottom."),
    (r"getaddrinfo|failed to resolve|name or service not known|temporary failure in name resolution|"
     r"network is unreachable|connection refused|connection reset|timed out|winerror 10060|winerror 10061|"
     r"remote end closed|connectionerror|unable to connect",
     "Couldn't reach the site. Check your internet connection and try again."),
    (r"http error 429|too many requests|rate.?limit",
     "The site is limiting downloads right now. Wait a few minutes and try again."),
    (r"private video|this video is private", "This video is private."),
    (r"video unavailable|this video is unavailable|has been removed|no longer available|does not exist|"
     r"http error 404|not found",
     "This isn't available anymore. It may have been deleted, made private, or blocked in your country."),
    (r"requested format is not available",
     "That quality isn't available for this one. Pick another."),
    (r"unsupported url", "grabF can't download from this link."),
    (r"http error 403|forbidden",
     "The site refused the download. Press 'update' at the bottom; if it keeps happening, the site may have changed."),
]


def clean_error(e: Exception) -> str:
    full = str(e)
    low = full.lower()
    if not needs_login(full):  # login problems get their own explanation elsewhere
        for pattern, plain in PLAIN:
            if re.search(pattern, low):
                return plain
    msg = full.replace("ERROR: ", "").split("\n")[0]
    # the engine's 'please report this issue on github ... yt-dlp -U' tail means nothing to a grabF user
    msg = re.split(r";?\s*please report this issue", msg, maxsplit=1, flags=re.I)[0].rstrip(" ;.")
    if "]" in msg[:40]:
        msg = msg.split("]", 1)[-1].split(":", 1)[-1].strip() or msg
    return msg


# --- logins: only used when a site refuses without one ---
LOGIN_NEEDED = re.compile(r"sign(ed)? in|log ?in|login|authenticat|cookies|private|age[- ]restricted|"
                          r"confirm your age|members[- ]only|not a bot", re.I)
CHROMIUM = {"chrome", "edge", "brave", "opera", "vivaldi", "chromium"}


def needs_login(msg: str) -> bool:
    return bool(LOGIN_NEEDED.search(msg or ""))


def browsers_on_pc() -> list[dict]:
    """Browsers that actually have a profile on this PC. Chromium ones are flagged: since 2024
    Chrome, Edge and friends encrypt logins so only the browser itself can read them."""
    if not WINDOWS:
        return _browsers_other()
    local, roam = Path(os.environ.get("LOCALAPPDATA", "")), Path(os.environ.get("APPDATA", ""))
    spots = {
        "firefox": [roam / "Mozilla/Firefox/Profiles",
                    *local.glob("Packages/Mozilla.Firefox_*/LocalCache/Roaming/Mozilla/Firefox/Profiles")],
        "chrome": [local / "Google/Chrome/User Data"],
        "edge": [local / "Microsoft/Edge/User Data"],
        "brave": [local / "BraveSoftware/Brave-Browser/User Data"],
        "opera": [roam / "Opera Software/Opera Stable"],
        "vivaldi": [local / "Vivaldi/User Data"],
    }
    return [{"id": b, "locked": b in CHROMIUM} for b, ps in spots.items() if any(Path(p).is_dir() for p in ps)]


def _browsers_other() -> list[dict]:
    """Mac / Linux browser profiles. The Windows-only lock on Chrome-family logins doesn't apply there
    (a Mac may ask for your password once so the browser's keychain entry can be read)."""
    home = Path.home()
    if MAC:
        sup = home / "Library" / "Application Support"
        spots = {"firefox": [sup / "Firefox/Profiles"], "chrome": [sup / "Google/Chrome"],
                 "edge": [sup / "Microsoft Edge"], "brave": [sup / "BraveSoftware/Brave-Browser"],
                 "opera": [sup / "com.operasoftware.Opera"], "vivaldi": [sup / "Vivaldi"],
                 "safari": [home / "Library/Containers/com.apple.Safari/Data/Library/Cookies", home / "Library/Cookies"]}
    else:
        cfg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
        spots = {"firefox": [home / ".mozilla/firefox", home / "snap/firefox/common/.mozilla/firefox"],
                 "chrome": [cfg / "google-chrome"], "chromium": [cfg / "chromium"], "edge": [cfg / "microsoft-edge"],
                 "brave": [cfg / "BraveSoftware/Brave-Browser"], "opera": [cfg / "opera"], "vivaldi": [cfg / "vivaldi"]}
    return [{"id": b, "locked": False} for b, ps in spots.items() if any(p.is_dir() for p in ps)]


def cookie_problem(browser: str) -> str:
    """'' if the browser's logins can be read right now, otherwise a plain explanation."""
    from yt_dlp.cookies import extract_cookies_from_browser
    try:
        jar = extract_cookies_from_browser(browser, logger=Silent())
    except Exception as e:
        msg = str(e).lower()
        if "could not find" in msg:
            return f"{browser} isn't installed on this PC, so there's no login to use."
        if browser in CHROMIUM or "dpapi" in msg or "decrypt" in msg or "copy" in msg:
            return (f"{browser.capitalize()} locks its logins so other apps can't read them. "
                    f"Sign in to this site in Firefox instead and pick firefox next to 'signed in with' at the bottom.")
        return f"couldn't read {browser}'s logins ({clean_error(e)})"
    return "" if len(jar) else f"{browser} has no saved logins - sign in to this site in {browser} first."


def read_clipboard() -> str:
    if not WINDOWS:
        return _clipboard_other()
    u32, k32 = USER32, KERNEL32
    u32.GetClipboardData.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not u32.OpenClipboard(None):
        return ""
    try:
        h = u32.GetClipboardData(13)  # CF_UNICODETEXT
        if not h:
            return ""
        p = k32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) if p else ""
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def _clipboard_other() -> str:
    for cmd in (["pbpaste"],) if MAC else (["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"]):
        if shutil.which(cmd[0]):
            try:
                return subprocess.run(cmd, capture_output=True, text=True, timeout=3, stdin=subprocess.DEVNULL).stdout
            except Exception:
                return ""
    return ""


_EXTRACTORS = None
_extractors_lock = threading.Lock()


def _extractors() -> list:
    global _EXTRACTORS
    with _extractors_lock:  # the warm-up and the first link can arrive together; build it once
        if _EXTRACTORS is None:
            from yt_dlp.extractor import gen_extractors
            _EXTRACTORS = [ie for ie in gen_extractors() if ie.IE_NAME.lower() != "generic"]
    return _EXTRACTORS


def warm_up() -> None:
    """Runs in the background while the window opens: load the engines and prepare the ~1,800 site
    matchers, so the first link doesn't pay for it."""
    try:
        yt_dlp.version  # imports yt-dlp
        probe = "https://example.invalid/warm-up"
        for ie in _extractors():
            ie.suitable(probe)  # compiles each site's link pattern once
        from gallery_dl import extractor as gx
        gx.find(probe)
        burn_encoder()  # find the fastest working encoder now, so a burn starts straight away
    except Exception:
        pass


def handling_sites(url: str) -> list[str]:
    """The engines' names for the site that would handle this link (first match, like the engines)."""
    names = []
    try:
        ie = next((ie for ie in _extractors() if ie.suitable(url)), None)
        if ie:
            names.append(ie.IE_NAME.split(":")[0].strip())
        from gallery_dl import extractor as gx
        ex = gx.find(url)
        if ex and getattr(ex, "category", ""):
            names.append(ex.category)
    except Exception:
        pass
    return names


BLOCK_MSG = "grabF doesn't download from this site."
INSTA_LOGIN_MSG = ("Instagram only shows photos to signed-in visitors. Sign in to instagram.com in Firefox and paste "
                   "the link again - grabF uses that login on this PC only. (Chrome, Edge and Brave lock their logins "
                   "so other apps can't read them.)")


def is_instagram(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "instagram.com" or host.endswith(".instagram.com") or host in ("instagr.am", "www.instagr.am")


ADULT_MSG = "This is marked 18+, and grabF doesn't download adult content."


# the big everyday sites: recognised instantly by address, no need to scan ~1,800 site matchers
POPULAR = {"youtube.com", "youtu.be", "tiktok.com", "instagram.com", "x.com", "twitter.com", "facebook.com",
           "fb.watch", "pinterest.com", "pin.it", "reddit.com", "redd.it", "bsky.app", "soundcloud.com",
           "vimeo.com", "dailymotion.com", "twitch.tv", "tumblr.com", "imgur.com", "bandcamp.com", "linkedin.com",
           "snapchat.com", "9gag.com", "bilibili.com", "rumble.com", "kick.com", "vk.com", "flickr.com"}


def popular(url: str) -> bool:
    parts = (urlsplit(url).hostname or "").lower().split(".")
    return any(".".join(parts[i:]) in POPULAR for i in range(len(parts) - 1))


def refused(url: str) -> str:
    """'' if grabF will handle this link, else the reason. Sites are checked against one-way hashes
    (see 'blocked sites' below), so no site names are kept anywhere in grabF."""
    host = urlsplit(url).hostname or ""
    if host_blocked(host):
        return BLOCK_MSG
    if not popular(url) and any(site_blocked(n) for n in handling_sites(url)):
        return BLOCK_MSG
    return ""


def push(event: str, data: dict) -> None:
    if not WINDOW:
        return
    try:
        WINDOW.evaluate_js(f"window.onEvent({json.dumps(event)}, {json.dumps(data)})")
    except Exception:
        pass  # the window was closed while a download was still finishing


class Silent:
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


def base_opts(cookies: str) -> dict:
    # cookies are read from the browser into memory for this one run and only sent to the
    # site whose cookies they are; no cookiefile is set, so yt-dlp never writes them to disk
    # age_limit 17: yt-dlp itself skips anything marked 18+ (also inside playlists)
    o = {"quiet": True, "no_warnings": True, "noprogress": True, "logger": Silent(), "age_limit": 17,
         # YouTube's HLS list repeats streams we already have, is slow to fetch and its sizes are
         # rough guesses. Live streams only exist as HLS; reading_again_with_hls() handles those.
         "extractor_args": {"youtube": {"skip": ["hls"]}}}
    if cookies in BROWSERS and cookies:
        o["cookiesfrombrowser"] = (cookies,)
    if FFMPEG and FFMPEG != "missing":
        o["ffmpeg_location"] = FFMPEG
    return o


def reading_again_with_hls(ydl, error: Exception) -> bool:
    """A YouTube live stream has no streams outside HLS, so the first read finds none. Switch HLS back on
    for this one link and say so (True = read it again). Anything else is a real error."""
    if "no video formats found" in str(error).lower() and ydl.params.get("extractor_args"):
        ydl.params["extractor_args"] = {}
        return True
    return False


# --- probe: read what a link offers ---
AUDIO_EXT = {"m4a", "mp3", "opus", "ogg", "oga", "aac", "flac", "wav", "weba", "mka"}


def is_video(f: dict) -> bool:
    v = f.get("vcodec")
    if v == "none":
        return False
    if v or f.get("height"):
        return True
    return (f.get("ext") or "") not in AUDIO_EXT


TAGS = {4320: "8k", 2160: "4k", 1440: "2k"}
COMPAT_SORT = ["vcodec:h264", "res", "fps", "acodec:m4a"]  # 'plays everywhere': prefer h.264 + aac


def video_format(quality) -> str:
    """The format rule for a chosen quality - used both to preview and to download, so they always agree."""
    cap = f"[height<={quality}]" if isinstance(quality, int) else ""
    return f"bv*{cap}+ba/b{cap}/bv*+ba/b"


def what_you_get(info: dict, browser: str) -> dict | None:
    """Ask yt-dlp's own format selector what every choice would download, for both file modes.
    Runs on the format list already fetched - no extra network requests."""
    fmts = info.get("formats") or []
    heights = sorted({f["height"] for f in fmts if f.get("height") and is_video(f)}, reverse=True)
    if not heights:
        return None

    def describe(p: dict) -> dict:
        parts = p.get("requested_formats") or [p]
        size = sum(f.get("filesize") or f.get("filesize_approx") or 0 for f in parts) or None
        h, fps = p.get("height"), p.get("fps") or 0
        return {"h": h, "label": f"{h}p{int(fps) if fps > 30 else ''}" if h else "best", "size": size,
                "vcodec": (p.get("vcodec") or "").lower()}

    out = {"heights": heights, "best": {}, "compat": {}}
    for mode, sort in (("best", None), ("compat", COMPAT_SORT)):
        params = base_opts(browser)
        if sort:
            params["format_sort"] = sort
        with yt_dlp.YoutubeDL(params) as y:
            fs = [dict(f) for f in fmts]
            y.sort_formats({"formats": fs, "_format_sort_fields": info.get("_format_sort_fields")})
            ctx = {"formats": fs,
                   "has_merged_format": any("none" not in (f.get("acodec"), f.get("vcodec")) for f in fs),
                   "incomplete_formats": all(f.get("vcodec") == "none" for f in fs)
                   or all(f.get("acodec") == "none" for f in fs)}
            for h in heights:
                pick = next(iter(y.build_format_selector(video_format(h))(ctx)), None)
                out[mode][h] = describe(pick) if pick else {"h": None, "label": "", "size": None, "vcodec": ""}
    # 'plays everywhere' is only a real promise if what it picks is h.264 (or the site doesn't say)
    out["compat_real"] = all(c["vcodec"] in ("", "none") or c["vcodec"].startswith(("avc", "h264"))
                             for c in out["compat"].values())
    return out


def site_name(key: str) -> str:
    """The engine's internal name, readable: 'YoutubeTab' -> 'youtube', 'SoundcloudSet' -> 'soundcloud'."""
    k = (key or "").lower()
    return re.sub(r"(tab|playlist|user|channel|album|set|search|feed)s?$", "", k) or k


# site names in grabF's own lowercase voice, like the "youtube · ..." line above the title
SITE_NAMES = {"twitter": "x", "tedtalk": "ted"}


def subtitle_choices(info: dict) -> dict:
    """Subtitles grouped for the menu, each labelled with who made it:
    the uploader's own, auto-generated from the speech, or machine-translated."""
    key = site_name(info.get("extractor_key", "")) or ""
    site = SITE_NAMES.get(key, key).lower() or "the site"
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    name = lambda tracks, key: next((t.get("name") for t in tracks if t.get("name")), key)
    subs = sorted(({"id": k, "label": name(v, k)} for k, v in manual.items() if k != "live_chat"),
                  key=lambda s: s["label"].lower())[:60]  # by the name you read, not the code
    generated, translated = [], []
    originals = [k for k in auto if k.endswith("-orig")]
    for k, v in auto.items():
        label = name(v, "")
        if not label:
            continue  # unnamed duplicates of the uploader's own tracks
        label = re.sub(r"\s*\((Original|auto-generated)\)$", "", label)
        if k in originals or (not originals and k == info.get("language")):
            generated.append({"id": f"auto:{k}", "label": f"{label} (auto-generated by {site})"})
        else:
            translated.append({"id": f"auto:{k}", "label": f"{label} (auto-translated by {site})"})
    translated.sort(key=lambda s: s["label"].lower())
    return {"subs": subs, "autosubs": generated[:5], "autotrans": translated[:200], "site_label": site}


def srt_time(t: float) -> str:
    """Seconds -> '00:01:02,500'. Rounded to whole milliseconds first, so 1.9996 becomes 2,000 and not 1,000."""
    ms = round(max(0.0, t) * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def json3_to_srt(raw: str) -> str:
    """YouTube's raw caption timeline -> plain subtitles, one line at a time (the usual auto-caption files
    repeat each line as words roll in, which looks doubled once burned in or played back)."""
    events = json.loads(raw).get("events") or []
    cues = []
    for e in events:
        text = " ".join("".join(s.get("utf8", "") for s in e.get("segs") or []).split())
        if not text or "tStartMs" not in e:
            continue
        start = e["tStartMs"] / 1000
        cues.append([start, start + (e.get("dDurationMs") or 0) / 1000, text])
    out = []
    for n, (a, b, text) in enumerate(cues):
        if n + 1 < len(cues):
            b = min(b, cues[n + 1][0])  # one line on screen at a time
        if b - a < 0.05:
            continue
        out.append(f"{len(out) + 1}\n{srt_time(a)} --> {srt_time(b)}\n{text}")
    return "\n\n".join(out) + "\n"


def summarize(info: dict, picks: dict | None = None) -> dict:
    if info.get("_type") == "playlist":
        all_entries = list(info.get("entries") or [])
        entries = [e for e in all_entries if e]
        thumb = info.get("thumbnails") or (entries[0].get("thumbnails") if entries else None) or []
        items = [{"i": n, "title": str(e.get("title") or "untitled")[:200],
                  "dur": e.get("duration") if isinstance(e.get("duration"), (int, float)) else None}
                 for n, e in enumerate(all_entries, 1) if e][:1000]  # n = position in the playlist
        return {
            "kind": "playlist",
            "title": info.get("title") or "playlist",
            "by": info.get("uploader") or info.get("channel") or "",
            "site": site_name(info.get("extractor_key", "")),
            "count": len(entries),
            "items": items,
            "thumb": thumb[-1]["url"] if thumb else "",
            "qualities": [{"h": h, "label": f"{h}p", "tag": TAGS.get(h, ""), "size": None,
                           "c_ok": True, "c_label": f"{h}p", "c_size": None} for h in (2160, 1440, 1080, 720, 480, 360)],
            "compat_real": True,
            "subs": [],
            "audio": None,
        }

    fmts = info.get("formats") or []
    audio = [f for f in fmts if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    # some sites (facebook, linkedin) give no codec/size info at all - treat those as video
    has_video = any(is_video(f) for f in fmts) or (not fmts and info.get("url") and is_video(info))
    best_a = max(audio, key=lambda f: f.get("abr") or f.get("tbr") or 0, default=None)
    a_size = (best_a or {}).get("filesize") or (best_a or {}).get("filesize_approx") or 0

    # one row per resolution the video has; each row says what you'd really get in both file modes
    qualities, compat_real = [], True
    if picks:
        compat_real = picks["compat_real"]
        for h in picks["heights"]:
            b, c = picks["best"][h], picks["compat"][h]
            qualities.append({
                "h": h, "label": b["label"], "tag": TAGS.get(h, ""), "size": b["size"],
                "c_ok": c["h"] == h,  # does 'plays everywhere' actually reach this resolution?
                "c_label": c["label"], "c_size": c["size"],
            })

    thumbs = info.get("thumbnails") or []
    thumb = info.get("thumbnail") or (thumbs[-1]["url"] if thumbs else "")
    return {
        "kind": "video" if has_video else "audio",
        "title": info.get("title") or "untitled",
        "by": info.get("uploader") or info.get("channel") or info.get("creator") or "",
        "site": site_name(info.get("extractor_key") or ""),
        "duration": info.get("duration"),
        "thumb": thumb,
        "qualities": qualities,
        "compat_real": compat_real,  # False: the site has no h.264 at all, so 'plays everywhere' can't promise it
        **subtitle_choices(info),
        "audio": {"codec": (best_a or {}).get("acodec", "").split(".")[0],
                  "kbps": round((best_a or {}).get("abr") or 0), "size": a_size or None} if best_a else None,
    }


# --- photos: gallery-dl handles image posts, carousels, slideshows ---
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NOT_VIDEO = ("no video", "unsupported url", "no media", "there's no video", "no formats", "is not a video")


def gallery_cmd(cookies: str, url: str, *args: str) -> list[str]:
    # --config-ignore: never pick up stray config files
    # cache.file=null: don't keep login sessions/tokens on disk between runs
    cmd = [PYEXE, "-m", "gallery_dl", "--config-ignore", "-o", "cache.file=null"]
    if cookies in BROWSERS and cookies:
        cmd += ["--cookies-from-browser", cookies]
    return cmd + list(args) + ["--", check_url(url)]  # "--": the link can never be read as an option


def first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, dict):
            v = v.get("displayName") or v.get("full_name") or v.get("name") or v.get("username") or v.get("handle")
        if v:
            return str(v)
    return ""


def gallery_probe(url: str, cookies: str) -> dict:
    r = subprocess.run(gallery_cmd(cookies, url, "-j", "--range", "1-500"), capture_output=True, stdin=subprocess.DEVNULL,
                       text=True, encoding="utf-8", errors="replace", timeout=90, creationflags=NO_WINDOW)
    try:
        msgs = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        raise RuntimeError("no video or photos found at this link")
    for m in msgs:
        if m and m[0] == -1:
            err = (m[1] or {}).get("message", "could not read this link")
            if "login" in err.lower():
                err = "this site wants you signed in"
            raise RuntimeError(err)
    files = [m for m in msgs if m and m[0] == 3]
    if not files:
        raise RuntimeError("no video or photos found at this link")
    meta = next((m[1] for m in msgs if m and m[0] == 2), files[0][2])
    images = [f for f in files if (f[2].get("extension") or "").lower() not in ("mp4", "webm", "mov", "m4v")]
    thumb = next((f[1] for f in images if f[1].startswith("http")), "")
    text = first(meta, "description", "content", "caption", "title", "text") or "untitled"
    return {
        "kind": "images",
        "title": " ".join(text.split())[:140],
        "by": first(meta, "author", "user", "owner", "username", "fullname", "uploader"),
        "site": meta.get("category", ""),
        "count": len(files),
        "videos": len(files) - len(images),
        "thumb": thumb,
        "qualities": [], "subs": [], "audio": None,
    }


class GalleryJob:
    def __init__(self, jid: str, req: dict, cfg: dict):
        self.id, self.req, self.cfg = jid, req, cfg
        self.cancelled = False
        self.proc: subprocess.Popen | None = None

    def run(self) -> None:
        total = self.req.get("count") or 0
        files: list[str] = []
        out = Path(self.cfg["out"])
        tmp = out / f".grabF-{self.id}"  # gallery-dl's own names land here, then get clean names
        push("progress", {"id": self.id, "stage": "waiting for a free slot", "pct": None})
        SLOTS.acquire()
        try:
            if self.cancelled:
                raise yt_dlp.utils.DownloadCancelled()
            tmp.mkdir(parents=True, exist_ok=True)
            self.proc = subprocess.Popen(
                gallery_cmd(self.cfg["cookies"], self.req["url"], "-D", str(tmp)),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", creationflags=NO_WINDOW, env={**os.environ, "PYTHONUNBUFFERED": "1"})
            log: deque[str] = deque(maxlen=20)  # only the last lines matter, for the error message
            push("progress", {"id": self.id, "stage": "photos", "pct": 0 if total else None})
            for line in self.proc.stdout:
                path = line.strip().lstrip("# ").strip()
                log.append(line.strip())
                if path and Path(path).is_file():
                    files.append(path)
                    push("progress", {"id": self.id, "stage": "photos", "item": len(files), "of": total or None,
                                      "pct": min(len(files) / total * 100, 100) if total else None})
            self.proc.wait()
            if self.cancelled:
                raise yt_dlp.utils.DownloadCancelled()
            if not files:
                raise RuntimeError(next((l for l in reversed(log) if l), "nothing downloaded"))
            kept, _ = quarantine(files)  # defuse anything that isn't media before it leaves the temp folder
            base = safe_name(self.req.get("title") or "photos", 80)
            finish(self.id, move_into(out, kept, base), self.req)
        except yt_dlp.utils.DownloadCancelled:
            pass  # 'cancelled' was already shown when you pressed it
        except Exception as e:
            if not self.cancelled:
                push("failed", with_fix({"id": self.id, "error": clean_error(e)}))
        finally:
            SLOTS.release()
            shutil.rmtree(tmp, ignore_errors=True)
            JOBS.pop(self.id, None)

    def cancel(self) -> None:
        if not self.cancelled:
            push("failed", {"id": self.id, "error": "cancelled"})  # shown at once
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def finish(jid: str, files: list[str], req: dict, title: str = "", skipped: int = 0) -> None:
    files, blocked = quarantine(files)
    if not files:
        push("failed", {"id": jid, "error": f"blocked {blocked} file(s) that weren't video, audio or images"
                        if blocked else "Nothing could be downloaded - the videos may be deleted, private or blocked in your country."
                        if skipped else "nothing downloaded"})
        return
    size = sum(Path(f).stat().st_size for f in files)
    name = Path(files[0]).name if len(files) == 1 else f"{len(files)} files"
    title = title or req.get("title") or name
    push("done", {"id": jid, "files": files, "size": size, "blocked": blocked, "openable": True, "name": name,
                  "skipped": skipped})
    try:
        add_history({"id": jid, "title": title, "url": req["url"], "files": files, "size": size,
                     "name": name, "mode": req["mode"], "when": int(time.time())})
    except Exception:
        pass
    notify("Downloaded", title)


def move_into(folder: Path, files: list[str], base: str) -> list[str]:
    """Move freshly downloaded photos from grabF's temp folder to clean names: 'Caption.jpg' or 'Caption 1.jpg'..."""
    out = []
    multi = len(files) > 1
    for i, f in enumerate(sorted(files), 1):
        src = Path(f)
        stem = unique_base(folder, f"{base} {i}" if multi else base)
        dest = folder / f"{stem}{src.suffix.lower()}"
        os.replace(src, dest)
        os.utime(dest)  # dated when you downloaded it, like videos - not the post's own date
        out.append(str(dest))
    return out


# --- download jobs ---
class Job:
    def __init__(self, jid: str, req: dict, cfg: dict):
        self.id, self.req, self.cfg = jid, req, cfg
        self.cancelled = False
        self.last = 0.0
        self.base: str | None = None        # the name chosen for a single video
        self.current: Path | None = None    # file being written right now (playlists)
        self.thread: int | None = None
        self.reported = False               # 'cancelled' already shown, don't report again
        self.part: str | None = None        # file being downloaded now (video, then audio)
        self.speed = 0.0                    # averaged over the last couple of seconds
        self.speed_at = 0.0
        self.countdown = Countdown()

    def cancel(self) -> None:
        """Takes effect at once: the row shows 'cancelled', a running conversion is stopped, and the
        download stops at its very next step. A web request already on its way is simply ignored."""
        self.cancelled = True
        if not self.reported:
            self.reported = True
            push("failed", {"id": self.id, "error": "cancelled"})
        stop_ffmpeg(self.thread)

    def report(self, error: str) -> None:
        if not self.reported:
            self.reported = True
            push("failed", with_fix({"id": self.id, "error": error}))

    @staticmethod
    def subtitle_file(video: Path, lang: str) -> Path | None:
        """The .srt yt-dlp saved next to the video: 'Title.en.srt'."""
        stem = video.name[: -len(video.suffix)] if video.suffix else video.name
        for p in video.parent.iterdir():
            if p.suffix.lower() == ".srt" and p.name.lower().startswith(f"{stem}.{lang}".lower()):
                return p
        return None

    def auto_subs(self, video: Path, lang: str, info: dict) -> Path:
        """Auto-captions: raw timeline -> clean .srt -> inside the video, burned in, or kept as a file.
        The label always says they're automatic, e.g. 'English (auto-generated by YouTube)'."""
        stem = video.name[: -len(video.suffix)]
        raw = video.with_name(f"{stem}.{lang}.json3")
        if not raw.exists():
            push("progress", {"id": self.id, "stage": "no auto-captions came with this video", "pct": None})
            return video
        srt = video.with_name(f"{stem}.{lang.removesuffix('-orig')}.auto.srt")
        text = json3_to_srt(raw.read_text(encoding="utf-8", errors="replace"))
        if self.req.get("clip"):
            text = shift_srt(text, *self.req["clip"])
        srt.write_text(text, encoding="utf-8")
        raw.unlink(missing_ok=True)
        mode = self.req["subs_mode"]
        if mode == "burn" and not self.req.get("playlist"):
            return self.burn(video, srt, info)
        if mode == "file":
            return video
        # inside the video, as a track you can switch on and off - named so you can tell it's automatic
        choices = subtitle_choices(info) if info.get("_type", "video") == "video" else {}
        label = next((s["label"] for s in (choices.get("autosubs") or []) + (choices.get("autotrans") or [])
                      if s["id"] == f"auto:{lang}"), f"{lang} (auto-generated)")
        tmp = video.with_name(f"{stem}.subs{video.suffix}")
        push("progress", {"id": self.id, "stage": "adding subtitles", "pct": None})
        r = subprocess.run([ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-i", str(srt),
                            "-map", "0", "-map", "1", "-c", "copy",
                            "-c:s", "mov_text" if video.suffix.lower() == ".mp4" else "srt",
                            "-metadata:s:s:0", f"title={label}", "-disposition:s:0", "default", str(tmp)],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:  # couldn't add the track: keep the subtitles as a file rather than lose them
            tmp.unlink(missing_ok=True)
            return video
        os.replace(tmp, video)
        srt.unlink(missing_ok=True)
        return video

    def burn(self, video: Path, srt: Path, info: dict) -> Path:
        """Paint the subtitles into the picture: a new H.264 mp4 that plays everywhere, subtitles always on."""
        import tempfile
        work = Path(tempfile.mkdtemp(prefix="grabF-burn-"))
        try:
            # plain names inside a scratch folder: ffmpeg's filter syntax trips over ':' and '\' in real paths
            shutil.copy2(srt, work / "s.srt")
            if FONTS.is_dir():
                shutil.copytree(FONTS, work / "fonts")
            stem = video.name[: -len(video.suffix)]
            out = video.with_name(stem + ".mp4")
            tmp = video.with_name(stem + ".burning.mp4")
            clip = self.req.get("clip")
            length = (clip[1] - clip[0]) if clip else (info.get("duration") or 0)
            style = style_string(self.req["sub_style"], self.req["sub_size"] / 100, self.req.get("sub_outline", True))
            for enc in [burn_encoder(), ENCODERS[-1]]:  # if the graphics card trips up, the processor takes over
                cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-nostats", "-loglevel", "error", "-progress", "pipe:1",
                       "-i", str(video), "-map", "0:v:0", "-map", "0:a?",
                       "-vf", f"subtitles=s.srt:fontsdir=fonts:force_style='{style}'",
                       *enc[1], "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                       "-map_metadata", "0", "-movflags", "+faststart", str(tmp)]
                proc = subprocess.Popen(cmd, cwd=work, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                with _procs_lock:
                    _procs.setdefault(self.thread, []).append(proc)  # so cancel can stop it at once
                errors, last, began, countdown = [], 0.0, time.time(), Countdown()
                stage = f"burning subtitles (depends on the {enc[2]})"
                try:
                    push("progress", {"id": self.id, "stage": stage, "pct": 0})
                    for line in proc.stdout:
                        if line.startswith("out_time_us=") and length:
                            us = line.split("=", 1)[1].strip()
                            if us.isdigit() and time.time() - last > 0.3:
                                last = time.time()
                                pct = min(int(us) / 1e6 / length * 100, 99.9)
                                left = (last - began) * (100 - pct) / pct if pct > 1 else None
                                push("progress", {"id": self.id, "stage": stage, "pct": pct,
                                                  "eta": countdown.update(left)})
                        elif "=" not in line and line.strip():
                            errors.append(line.strip())
                    proc.wait()
                finally:
                    with _procs_lock:
                        if proc in _procs.get(self.thread, []):
                            _procs[self.thread].remove(proc)
                if self.cancelled:
                    tmp.unlink(missing_ok=True)
                    raise yt_dlp.utils.DownloadCancelled()
                if proc.returncode == 0:
                    break
                tmp.unlink(missing_ok=True)
                if enc == ENCODERS[-1]:
                    raise RuntimeError("couldn't burn the subtitles: " + (errors[-1] if errors else "ffmpeg failed"))
            if out != video:
                video.unlink(missing_ok=True)
            os.replace(tmp, out)
            srt.unlink(missing_ok=True)
            return out
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def gate(self, info, *, incomplete=False):
        """Checked by yt-dlp before each video (playlists): stop before starting the next one."""
        if self.cancelled:
            raise yt_dlp.utils.DownloadCancelled()
        return None

    def cleanup(self) -> None:
        """After a cancel or failure: delete everything this download started - half-finished video and
        audio parts, thumbnails, subtitles. Finished videos of a playlist and all older files are kept."""
        targets = []
        if self.base:
            targets.append((Path(self.cfg["out"]), self.base))  # a name nobody else had, so all of it is ours
        if self.current:
            m = re.match(r"^(.*?)(?:\.f\d+)?\.[A-Za-z0-9]+(?:\.part)?$", self.current.name)
            if m:
                targets.append((self.current.parent, m.group(1)))  # only the playlist item that was in progress
        for folder, base in targets:
            try:
                for p in folder.iterdir():
                    if p.is_file() and p.name.lower().startswith(base.lower() + "."):
                        p.unlink(missing_ok=True)
            except OSError:
                pass

    def opts(self) -> dict:
        r, c = self.req, self.cfg
        o = base_opts(c["cookies"])
        o.update(
            paths={"home": c["out"]},
            # pl_thumbnail '': don't save the playlist's own cover image as a stray '000 - playlist.jpg'
            outtmpl={"default": LIST_NAME if r.get("playlist") else NAME, "pl_thumbnail": ""},
            noplaylist=not r.get("playlist"),
            updatetime=False,
            concurrent_fragment_downloads=4,
            progress_hooks=[self.progress],
            postprocessor_hooks=[self.post],
            writethumbnail=True,
        )
        pp: list[dict] = []
        if r["mode"] == "audio":
            fmt = r["afmt"]
            o["format"] = "ba/b"
            # a plain number: yt-dlp ignores "320K" here and ffmpeg silently falls back to 128 kbps
            quality = str(r["kbps"]) if fmt == "mp3" else "0"
            pp.append({"key": "FFmpegExtractAudio", "preferredcodec": fmt if fmt != "original" else "best",
                       "preferredquality": quality, "nopostoverwrites": False})
            if fmt == "wav":
                o["writethumbnail"] = False
        else:
            o["format"] = video_format(r.get("quality"))
            if r.get("compat"):
                o["format_sort"] = COMPAT_SORT
                o["merge_output_format"] = "mp4"
            else:
                o["merge_output_format"] = "mp4/mkv"
            if r.get("subs", "").startswith("auto:"):
                # auto-captions: the raw caption timeline; grabF turns it into clean subtitles after the download
                o.update(writeautomaticsub=True, writesubtitles=False, subtitleslangs=[r["subs"][5:]],
                         subtitlesformat="json3")
                if r["subs_mode"] == "burn":
                    o["writethumbnail"] = False
            elif r.get("subs"):
                o.update(writesubtitles=True, subtitleslangs=[r["subs"]])
                if r["subs_mode"] == "embed":  # a track inside the file you can switch on and off
                    pp.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})
                else:  # 'file' keeps a .srt next to the video; 'burn' paints it into the picture afterwards
                    pp.append({"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"})
                if r["subs_mode"] == "burn":
                    o["writethumbnail"] = False  # the burned copy is a fresh file; no cover art to carry over
        if r.get("clip"):
            from yt_dlp.utils import download_range_func
            o["download_ranges"] = download_range_func(None, [tuple(r["clip"])])
            o["force_keyframes_at_cuts"] = True  # cut exactly where you asked, not at the nearest keyframe
        pp.append({"key": "FFmpegMetadata", "add_chapters": True, "add_metadata": True, "add_infojson": False})
        if o["writethumbnail"]:
            pp.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
            pp.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
        o["postprocessors"] = pp
        if r.get("playlist"):
            o["ignoreerrors"] = True  # a deleted or private video is skipped, the rest still download
            if r.get("items"):
                o["playlist_items"] = ",".join(map(str, r["items"]))  # only the videos you ticked
        o["match_filter"] = self.gate
        return o

    def progress(self, d: dict) -> None:
        if d.get("filename"):
            self.current = Path(d["filename"])  # remembered for cleanup, before anything can return early
        if self.cancelled:
            raise yt_dlp.utils.DownloadCancelled()
        if Path(d.get("filename") or "").suffix.lower().lstrip(".") in SIDE_FILES:
            return  # subtitles or the cover picture: a few kb, not the video's own progress
        now = time.time()
        done = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        eta = None
        if d["status"] == "downloading":
            if d.get("filename") != self.part:  # the next part (audio after video): start fresh
                self.part, self.speed, self.countdown = d.get("filename"), 0.0, Countdown()
            if d.get("speed"):  # the engine's speed is for the last instant only; average it over ~2 s
                w = 1 - math.exp(-(now - self.speed_at) / 2.0) if self.speed else 1.0
                self.speed += w * (d["speed"] - self.speed)
                self.speed_at = now
            eta = self.countdown.update((total - done) / self.speed if self.speed and total else None)
            if now - self.last < 0.15:
                return
        self.last = now
        info = d.get("info_dict") or {}
        push("progress", {
            "id": self.id,
            "stage": "audio" if info.get("vcodec") == "none" else "video",
            "pct": 100 if d["status"] == "finished" else (done / total * 100 if total else None),
            "speed": self.speed or None, "eta": eta, "total": total or None,
            "item": info.get("playlist_index"), "of": info.get("n_entries"),
            "title": info.get("title"),
        })

    def post(self, d: dict) -> None:
        if self.cancelled:
            raise yt_dlp.utils.DownloadCancelled()  # don't start the next conversion step
        if d["status"] != "started":
            return
        step = {"Merger": "merging", "FFmpegExtractAudio": "converting", "ExtractAudio": "converting",
                "EmbedThumbnail": "adding cover", "FFmpegMetadata": "tagging", "Metadata": "tagging",
                "FFmpegEmbedSubtitle": "adding subtitles", "EmbedSubtitle": "adding subtitles"}.get(
            d.get("postprocessor", ""))
        if step:
            push("progress", {"id": self.id, "stage": step, "pct": None})

    def run(self) -> None:
        push("progress", {"id": self.id, "stage": "waiting for a free slot", "pct": None})
        SLOTS.acquire()
        self.thread = threading.get_ident()
        try:
            track_ffmpeg()
            if self.cancelled:
                raise yt_dlp.utils.DownloadCancelled()
            push("progress", {"id": self.id, "stage": "starting", "pct": None})
            with yt_dlp.YoutubeDL(self.opts()) as ydl:
                if self.req.get("playlist"):
                    info = ydl.extract_info(self.req["url"], download=True)  # each video read as its turn comes
                else:
                    try:
                        info = ydl.extract_info(self.req["url"], download=False)
                    except yt_dlp.utils.DownloadError as e:
                        if not reading_again_with_hls(ydl, e):
                            raise
                        info = ydl.extract_info(self.req["url"], download=False)
                if self.cancelled:  # cancelled while the video was being read: stop before downloading anything
                    raise yt_dlp.utils.DownloadCancelled()
                if not self.req.get("playlist") and info.get("_type", "video") == "video":
                    # a clean name chosen up front: 'Title.mp4', or 'Title (2).mp4' if that's taken
                    base = safe_name(info.get("title") or info.get("id") or "video")
                    if self.req.get("clip"):
                        base += f" ({clock(self.req['clip'][0])}-{clock(self.req['clip'][1])})"
                    base = self.base = reserve_base(Path(self.cfg["out"]), base)
                    ydl.params["outtmpl"] = {**ydl.params["outtmpl"], "default": base.replace("%", "%%") + ".%(ext)s"}
                if not self.req.get("playlist"):
                    if self.req.get("clip"):
                        # ffmpeg fetches and cuts just this part and reports no progress while it does,
                        # so the row says what's happening and counts the time instead of showing a stale %
                        a, b = self.req["clip"]
                        push("progress", {"id": self.id, "stage": f"cutting {mmss(a)}–{mmss(b)}", "pct": None,
                                          "timer": True})
                    info = ydl.process_ie_result(info, download=True)
            files, got = [], 0
            for e in (info or {}).get("entries") or [info]:
                fs = [f["filepath"] for f in (e or {}).get("requested_downloads") or []
                      if f.get("filepath") and Path(f["filepath"]).is_file()]
                files += fs
                got += bool(fs)
            skipped = max(0, (self.req.get("count") or 0) - got) if self.req.get("playlist") else 0
            if self.cancelled:  # pressed cancel in the last split second: honour it
                raise yt_dlp.utils.DownloadCancelled()
            r = self.req
            if r["mode"] == "video" and r["subs"].startswith("auto:") and files:
                files = [str(self.auto_subs(Path(f), r["subs"][5:], info)) for f in files]
            elif r["mode"] == "video" and r["subs"] and r["subs_mode"] in ("burn", "file") and files and not r["playlist"]:
                srt = self.subtitle_file(Path(files[0]), r["subs"])
                if srt and r.get("clip"):  # line the subtitles up with a clip that starts at 0:00
                    srt.write_text(shift_srt(srt.read_text(encoding="utf-8", errors="replace"), *r["clip"]),
                                   encoding="utf-8")
                if r["subs_mode"] == "burn":
                    if srt:
                        files = [self.burn(Path(files[0]), srt, info)]
                    else:
                        push("progress", {"id": self.id, "stage": "no subtitles found in that language", "pct": None})
            finish(self.id, files, self.req, (info or {}).get("title") or "", skipped)
        except yt_dlp.utils.DownloadCancelled:
            self.cleanup()
            self.report("cancelled")
        except Exception as e:  # DownloadError and anything else - never leave a row spinning
            self.cleanup()
            if self.cancelled:  # a conversion we stopped on purpose shows up as an error
                self.report("cancelled")
            else:
                if not isinstance(e, yt_dlp.utils.DownloadError):
                    log_error(self.req, e)
                self.report(clean_error(e))
        finally:
            if self.base:
                release_base(Path(self.cfg["out"]), self.base)
            with _procs_lock:
                _procs.pop(self.thread, None)
            SLOTS.release()
            JOBS.pop(self.id, None)


JOBS: dict[str, Job] = {}
# small files that come along with a video; their progress isn't the video's
SIDE_FILES = {"srt", "vtt", "ass", "ssa", "ttml", "json3", "srv1", "srv2", "srv3", "lrc", "json",
              "jpg", "jpeg", "png", "webp"}

# --- burned-in subtitles ---
# ASS style overrides for ffmpeg's subtitle renderer (libass). Colours are &HAABBGGRR (alpha first, then
# blue-green-red). Sizes are relative to a 288-line-high canvas, so they scale with the video's resolution.
# One set of numbers, used both to burn and to draw the live preview in the window (sent to the page by init).
# "margin" is measured to the bottom of the font's line box (which includes room for tails like g and y), so it
# differs per font and size: each one is set so the letters themselves stand on the same line as clean's.
STYLE_PARAMS = {
    "clean": {"font": "Jost", "size": 17, "bold": 0, "italic": 0, "color": "&H00FFFFFF", "edge": "&H00000000",
              "back": "&H80000000", "border": 1, "outline": 1.1, "shadow": 0.6, "margin": 16},
    "boxed": {"font": "Jost", "size": 15, "bold": 0, "italic": 0, "color": "&H00FFFFFF", "edge": "&H50000000",
              "back": "&H50000000", "border": 3, "outline": 3, "shadow": 0, "margin": 16},
    "yellow": {"font": "Jost", "size": 17, "bold": 1, "italic": 0, "color": "&H0000E1FF", "edge": "&H00000000",
               "back": "&H80000000", "border": 1, "outline": 1.1, "shadow": 0.6, "margin": 16},
    "big": {"font": "Jost", "size": 25, "bold": 1, "italic": 0, "color": "&H00FFFFFF", "edge": "&H00000000",
            "back": "&H00000000", "border": 1, "outline": 2.4, "shadow": 0, "margin": 14},
    "editorial": {"font": "Instrument Serif", "size": 23, "bold": 0, "italic": 1, "color": "&H00DAE9EF",
                  "edge": "&H00141618", "back": "&H40000000", "border": 1, "outline": 1.1, "shadow": 1.3, "margin": 15},
    "editorial_yellow": {"font": "Instrument Serif", "size": 23, "bold": 0, "italic": 1, "color": "&H0048D4F0",
                         "edge": "&H00141618", "back": "&H40000000", "border": 1, "outline": 1.1, "shadow": 1.3, "margin": 15},
    # Helvetica-style film subtitles (TeX Gyre Heros italic, a free Helvetica clone) in a warm yellow.
    # cinema: no dark edge at all - a hairline outline in the same yellow gives it medium weight
    "cinema": {"font": "TeX Gyre Heros", "size": 15.5, "bold": 0, "italic": 1, "color": "&H0048D4F0",
               "edge": "&H0048D4F0", "back": "&H96000000", "border": 1, "outline": 0.28, "shadow": 0.7, "margin": 17},
    # cinema edge: the same with a faint see-through dark edge, so it stays readable on bright scenes
    "cinema_edge": {"font": "TeX Gyre Heros", "size": 15.5, "bold": 0, "italic": 1, "color": "&H0048D4F0",
                    "edge": "&H70000000", "back": "&H90000000", "border": 1, "outline": 0.7, "shadow": 0.7, "margin": 17},
}


def style_string(name: str, scale: float = 1.0, outline: bool = True) -> str:
    """ASS overrides for ffmpeg. `scale` is your size setting: text, outline and shadow grow together.
    outline=False drops the edge and the shadow, leaving just the letters - except for boxed,
    where the 'outline' is the box itself."""
    p = STYLE_PARAMS[name]
    bare = not outline and p["border"] != 3
    edge, shadow = (0, 0) if bare else (p["outline"] * scale, p["shadow"] * scale)
    return (f"FontName={p['font']},FontSize={p['size'] * scale:.2f},Bold={p['bold']},Italic={p['italic']},"
            f"PrimaryColour={p['color']},OutlineColour={p['edge']},BackColour={p['back']},BorderStyle={p['border']},"
            f"Outline={edge:.2f},Shadow={shadow:.2f},MarginV={p['margin']}")


SUB_STYLES = {k: style_string(k) for k in STYLE_PARAMS}
SUB_MODES = ("embed", "burn", "file")
FONTS = HERE / "fonts"
# tried in this order; the first that works on this PC wins (graphics card first - several times faster)
ENCODERS = [
    ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19"], "graphics card"),
    ("h264_qsv", ["-c:v", "h264_qsv", "-global_quality", "19"], "graphics card"),
    ("h264_amf", ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "19", "-qp_p", "19"], "graphics card"),
    ("libx264", ["-c:v", "libx264", "-preset", "medium", "-crf", "19"], "processor"),
]
if sys.platform == "darwin":  # Apple's own video chip; tried before the processor fallback
    ENCODERS.insert(-1, ("h264_videotoolbox", ["-c:v", "h264_videotoolbox", "-q:v", "65"], "graphics chip"))
_encoder: tuple | None = None
_encoder_lock = threading.Lock()


def ffmpeg_exe(name: str = "ffmpeg") -> str:
    return str(Path(FFMPEG) / (f"{name}.exe" if WINDOWS else name)) if FFMPEG and FFMPEG != "missing" else name


def burn_encoder() -> tuple:
    """(name, args, kind) of the fastest encoder that really works here, found by a tiny test encode."""
    global _encoder
    with _encoder_lock:
        if _encoder is None:
            _encoder = ENCODERS[-1]
            for enc in ENCODERS:
                r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                    "color=c=black:s=1280x720:d=0.4", *enc[1], "-f", "null", "-"],
                                   capture_output=True, stdin=subprocess.DEVNULL, timeout=60,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if r.returncode == 0:
                    _encoder = enc
                    break
    return _encoder


SRT_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})")


def shift_srt(text: str, start: float, end: float) -> str:
    """For 'just a part': move the subtitles so they line up with a clip that starts at `start`."""
    def secs(m):
        h, mi, s, ms = map(int, m.groups())
        return h * 3600 + mi * 60 + s + ms / 1000

    out, n = [], 0
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = block.splitlines()
        timing = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if timing is None:
            continue
        times = list(SRT_TIME.finditer(lines[timing]))
        if len(times) < 2:
            continue
        a, b = secs(times[0]) - start, secs(times[1]) - start
        if b <= 0 or a >= end - start:
            continue  # outside the clip
        n += 1
        out.append(f"{n}\n{srt_time(a)} --> {srt_time(min(b, end - start))}\n" + "\n".join(lines[timing + 1:]))
    return "\n\n".join(out) + "\n"


# --- ffmpeg processes per download, so cancel can stop a conversion mid-way ---
_procs: dict[int, list] = {}  # download thread -> its running ffmpeg processes
_procs_lock = threading.Lock()
_tracking = False


def track_ffmpeg() -> None:
    """yt-dlp starts ffmpeg (conversions, clips) through one helper. Swap in a version that remembers which
    download started each process - only for the duration of that process."""
    global _tracking
    if _tracking:
        return
    from yt_dlp.utils import Popen
    import yt_dlp.downloader.external as dl_ext
    import yt_dlp.postprocessor.ffmpeg as pp_ffmpeg

    class Tracked(Popen):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            with _procs_lock:
                _procs.setdefault(threading.get_ident(), []).append(self)

        def __exit__(self, *a):
            with _procs_lock:
                running = _procs.get(threading.get_ident(), [])
                if self in running:
                    running.remove(self)
            return super().__exit__(*a)

    pp_ffmpeg.Popen = dl_ext.Popen = Tracked
    _tracking = True


def stop_ffmpeg(thread_id: int | None) -> None:
    with _procs_lock:
        for p in list(_procs.get(thread_id, [])):
            try:
                p.kill()
            except Exception:
                pass


LOG_MAX = 256 * 1024  # error.log never grows past ~256 kb; older entries are dropped


def log_error(req: dict, e: Exception) -> None:
    import traceback
    try:
        log = CONFIG.parent / "error.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        site = urlsplit(req.get("url", "")).netloc  # the site only - no path, no tokens
        entry = f"\n--- {time.ctime()} {site} {req.get('mode')}\n{traceback.format_exception(e)[-1].strip()}\n" \
                + "".join(traceback.format_exception(e))[-4000:]
        old = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        text = old + entry
        if len(text.encode("utf-8")) > LOG_MAX:
            text = text[-LOG_MAX // 2:]  # keep the newest half
            text = text[text.find("\n--- ") + 1:] if "\n--- " in text else text
        log.write_text(text, encoding="utf-8")
    except Exception:
        pass


# --- the API the page calls ---
def own_page_only(fn):
    """Refuse the call unless the window is still showing grabF's own page.

    The page never navigates anywhere, but if anything ever did push the window to another
    site, that site gets no access to your files, clipboard or downloads."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            current = str(WINDOW.get_current_url() or "")
            home = urlsplit(WINDOW.real_url or "")
            ok = home.scheme in ("http", "https") and home.hostname in ("127.0.0.1", "localhost") \
                and urlsplit(current)[:2] == home[:2]
        except Exception:
            ok = False
        if not ok:
            raise PermissionError("blocked: request did not come from grabF's own page")
        return fn(*a, **kw)
    return wrapper


SETTING_CHECKS = {
    "mode": lambda v: v in ("video", "audio"),
    "quality": lambda v: v == "best" or (isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 10000),
    "compat": lambda v: isinstance(v, bool),
    "afmt": lambda v: v in AFMTS,
    "kbps": lambda v: v in KBPS,
    "cookies": lambda v: v in BROWSERS,
    "subs_mode": lambda v: v in SUB_MODES,
    "sub_style": lambda v: v in SUB_STYLES,
    "sub_size": lambda v: isinstance(v, int) and not isinstance(v, bool) and 50 <= v <= 250,
    "sub_outline": lambda v: isinstance(v, bool),
}  # "out" is deliberately missing: the folder only changes through the folder picker


class Api:
    @own_page_only
    def init(self) -> dict:
        return {"settings": load_settings(), "ffmpeg": FFMPEG != "missing", "version": VERSION,
                "browsers": browsers_on_pc(), "sub_styles": STYLE_PARAMS}

    @own_page_only
    def open_link(self, which: str) -> None:
        """'contact' or 'donate' - the page names one, the address itself never comes from the page."""
        url = LINKS.get(which) if isinstance(which, str) else None
        if url:
            import webbrowser
            webbrowser.open(url)

    @own_page_only
    def open_fix(self, which: str) -> None:
        """'clock': Windows' own date & time settings page. Opened only by name, like open_link;
        grabF never changes the clock itself - that's the user's call, in Windows."""
        if which == "clock":
            if WINDOWS:
                os.startfile("ms-settings:dateandtime")
            elif MAC:
                subprocess.Popen(["open", "x-apple.systempreferences:com.apple.Date-Time-Settings.extension"])

    @own_page_only
    def open_release(self) -> None:
        if _release_url:  # set only from a verified github.com/<this repo>/releases/ link
            import webbrowser
            webbrowser.open(_release_url)

    @own_page_only
    def save(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            return load_settings()
        with _settings_lock:
            s = load_settings()
            s.update({k: v for k, v in patch.items() if k in SETTING_CHECKS and SETTING_CHECKS[k](v)})
            write_settings(s)
        return s

    @own_page_only
    def clipboard(self) -> dict:
        """Only ever returns one http(s) link. Any other clipboard text (passwords, messages)
        never leaves this function, and nothing is sent anywhere from here."""
        try:
            text = read_clipboard().strip()
            url = check_url(text)
        except Exception:
            return {"url": ""}
        if refused(url):
            return {"url": ""}  # a blocked site is simply ignored, not even filled in
        return {"url": url}

    @own_page_only
    def probe(self, url: str, playlist: bool = False) -> dict:
        return with_fix(self._probe(url, playlist))

    def _probe(self, url: str, playlist: bool = False) -> dict:
        try:
            url = check_url(url)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        playlist = playlist is True
        reason = refused(url)
        if reason:
            return {"ok": False, "error": reason}  # checked before anything is fetched
        browser = load_settings()["cookies"]

        # 1. always try as an anonymous visitor first - your login is never sent if it isn't needed.
        # Instagram never shows photos to anonymous visitors, so only its videos (reels) are tried that way.
        insta = is_instagram(url)
        r = self._read(url, playlist, "", photos=not insta)
        insta_photos = insta and not r["ok"] and any(k in r["error"].lower() for k in NOT_VIDEO)
        if r["ok"] or not (needs_login(r["error"]) or insta_photos):
            return r
        auto = False
        # Instagram photos only open with a login. If you haven't picked a browser (or picked one that locks its
        # logins) and Firefox is on this PC, its login is used for this one link - on this PC only.
        if insta and browser != "firefox" and (not browser or cookie_problem(browser)) \
                and any(b["id"] == "firefox" for b in browsers_on_pc()):
            browser, auto = "firefox", True
        if not browser:
            if insta:
                return {"ok": False, "error": INSTA_LOGIN_MSG}
            return {"ok": False, "error": "This one needs you signed in. Choose a browser you're logged in with "
                                          "next to 'signed in with' at the bottom."}
        # 2. the site refused: check the browser's logins can actually be read before using them
        problem = cookie_problem(browser)
        if problem:
            return {"ok": False, "error": INSTA_LOGIN_MSG if insta else f"This one needs you signed in. {problem}"}
        # an Instagram post that already said "no video here" is read straight away as photos
        r = self._read(url, playlist, browser, video=not insta_photos)
        if r["ok"]:
            r["info"].update(login=True, login_with=browser, login_auto=auto)
        elif needs_login(r["error"]):
            r["error"] = (f"Still refused with your {browser} login. Sign in to instagram.com in {browser}, "
                          f"then paste the link again." if insta else
                          f"Still refused with your {browser} login. Check you're signed in to this site in {browser}.")
        return r

    @staticmethod
    def _read_video(url: str, playlist: bool, browser: str) -> dict:
        o = base_opts(browser)
        # extract_flat: a playlist is read as a list of entries only (fast, and one deleted video can't
        # fail the whole read). Single videos are still read in full.
        # socket_timeout: a site that goes silent is retried after 10 s, not the default 20, so the page
        # doesn't sit on "Reading the link..." for half a minute (downloads keep the longer patience)
        o.update(skip_download=True, noplaylist=not playlist, extract_flat="in_playlist", socket_timeout=10)
        try:
            with yt_dlp.YoutubeDL(o) as ydl:
                try:
                    info = ydl.extract_info(url, download=False)
                except yt_dlp.utils.DownloadError as e:
                    if not reading_again_with_hls(ydl, e):
                        raise
                    info = ydl.extract_info(url, download=False)
            if (info.get("age_limit") or 0) >= 18:
                return {"ok": False, "error": ADULT_MSG, "final": True}  # any site, listed or not
            picks = what_you_get(info, browser) if info.get("_type", "video") == "video" else None
            out = summarize(ydl.sanitize_info(info), picks)
            # 'a video that sits inside a playlist': the link names a playlist AND one particular video,
            # so 'this video only' makes sense. A playlist-only link has no single video to fall back to.
            in_playlist = bool(re.search(r"[?&]list=|/sets/", url))
            names_video = bool(re.search(r"[?&]v=|youtu\.be/[\w-]+|/shorts/", url))
            out["inList"] = in_playlist and (names_video or out["kind"] != "playlist")
            return {"ok": True, "info": out}
        except Exception as e:
            return {"ok": False, "error": clean_error(e)}

    @staticmethod
    def _read(url: str, playlist: bool, browser: str, video: bool = True, photos: bool = True) -> dict:
        """The video engine, then (if it finds no video) the photo engine. Either one can be skipped when it's
        already known it can't work, which saves a few seconds."""
        video_err = "there's no video in this post"
        if video:
            r = Api._read_video(url, playlist, browser)
            if r["ok"] or r.pop("final", False):
                return r
            video_err = r["error"]
        if not photos:
            return {"ok": False, "error": video_err}
        # no video here - maybe it's a photo post, carousel or slideshow
        try:
            return {"ok": True, "info": gallery_probe(url, browser)}
        except Exception as e:
            photo_err = str(e)
        if "threads.net" in url or "threads.com" in url:
            return {"ok": False, "error": "threads isn't supported by the download engines yet"}
        if needs_login(photo_err) or needs_login(video_err):
            return {"ok": False, "error": video_err if needs_login(video_err) else photo_err}
        not_video = any(k in video_err.lower() for k in NOT_VIDEO)
        return {"ok": False, "error": photo_err if not_video else video_err}

    @own_page_only
    def download(self, jid: str, req: dict) -> None:
        if not isinstance(jid, str) or not re.fullmatch(r"[a-z0-9]{1,40}", jid) or jid in JOBS:
            return
        try:
            req = clean_request(req)
        except ValueError as e:
            push("failed", {"id": jid, "error": str(e)})
            return
        if refused(req["url"]):
            push("failed", {"id": jid, "error": BLOCK_MSG})
            return
        cls = GalleryJob if req["mode"] == "images" else Job
        cfg = load_settings()
        cfg["out"] = str(Path(cfg["out"]) / KIND_FOLDERS[req["mode"]])  # downloads/grabF/videos, /audio, /photos
        if not req["login"]:
            cfg["cookies"] = ""  # the link read fine anonymously, so download it anonymously too
        elif req["login_with"]:
            cfg["cookies"] = req["login_with"]  # the same login that read the link
        job = cls(jid, req, cfg)
        JOBS[jid] = job
        threading.Thread(target=job.run, daemon=True).start()

    @own_page_only
    def cancel(self, jid: str) -> None:
        if isinstance(jid, str) and jid in JOBS:
            JOBS[jid].cancel()

    @staticmethod
    def _known(path) -> Path | None:
        """Only files grabF itself downloaded this session - never an arbitrary path."""
        try:
            p = Path(path).resolve()
        except (TypeError, OSError):
            return None
        return p if str(p) in KNOWN_FILES and p.is_file() else None

    @own_page_only
    def reveal(self, path: str) -> None:
        p = self._known(path)
        if p and WINDOWS:
            subprocess.Popen(["explorer", "/select,", str(p)])
        elif p and MAC:
            subprocess.Popen(["open", "-R", str(p)])  # Finder, with the file selected
        elif p:
            open_with_system(p.parent)
        else:
            self.open_folder()

    @own_page_only
    def open_file(self, path: str) -> None:
        p = self._known(path)
        if p and p.suffix.lower().lstrip(".") in MEDIA_EXT:
            open_with_system(p)

    @own_page_only
    def open_folder(self) -> None:
        out = Path(load_settings()["out"])
        out.mkdir(parents=True, exist_ok=True)
        open_with_system(out)

    @own_page_only
    def pick_folder(self) -> str | None:
        res = WINDOW.create_file_dialog(webview.FOLDER_DIALOG, directory=load_settings()["out"])
        if res and Path(res[0]).is_dir():
            with _settings_lock:
                s = load_settings()
                s["out"] = str(Path(res[0]).resolve())
                write_settings(s)
            return s["out"]
        return None

    @own_page_only
    def update_engine(self) -> str:
        """'restart' (newer engines are installed, restart to use them), 'current' or 'failed'."""
        if not update_engines(wait=True):
            return "failed"
        return "restart" if restart_needed() else "current"

    @own_page_only
    def restart(self) -> bool:
        return restart_app()

    @own_page_only
    def minimize(self) -> None:
        WINDOW.minimize()

    @own_page_only
    def history(self) -> list[dict]:
        """Past downloads, newest first. Files still on disk become openable again."""
        out = []
        for e in load_history()[:200]:
            files = [f for f in e.get("files") or [] if isinstance(f, str)]
            alive = [str(Path(f).resolve()) for f in files if Path(f).is_file()
                     and Path(f).suffix.lower().lstrip(".") in MEDIA_EXT]
            KNOWN_FILES.update(alive)
            out.append({"id": str(e.get("id", "")), "title": str(e.get("title", ""))[:200],
                        "name": str(e.get("name", ""))[:200], "size": e.get("size") if isinstance(e.get("size"), int) else 0,
                        "when": e.get("when") if isinstance(e.get("when"), int) else 0,
                        "files": alive, "gone": not alive})
        return out

    @own_page_only
    def forget(self, hid: str) -> None:
        with _history_lock:
            h = [e for e in load_history() if str(e.get("id")) != hid]
            write_json(HISTORY, h)

    @own_page_only
    def clear_history(self) -> None:
        with _history_lock:
            HISTORY.unlink(missing_ok=True)  # only the list - your downloaded files stay where they are

    @own_page_only
    def resize_start(self) -> None:
        global _resize_from
        if not WINDOWS:  # pywebview's own size and position, in screen points
            _resize_from = (WINDOW.x, WINDOW.y, WINDOW.x + WINDOW.width, WINDOW.y + WINDOW.height)
            return
        r = _RECT()
        USER32.GetWindowRect(ctypes.c_void_p(int(WINDOW.native.Handle.ToInt64())), ctypes.byref(r))
        _resize_from = (r.left, r.top, r.right, r.bottom)

    @own_page_only
    def resize_to(self, edge: str, dx: float, dy: float) -> None:
        """Drag an edge or corner: dx/dy are how far the mouse moved, in screen pixels."""
        if not _resize_from or not isinstance(edge, str) or not all(isinstance(v, (int, float)) for v in (dx, dy)):
            return
        global _before_big
        _before_big = None
        l, t, r, b = _resize_from
        if not WINDOWS:
            dx, dy = int(dx), int(dy)
            if "e" in edge: r = max(l + MIN_W, r + dx)
            if "w" in edge: l = min(r - MIN_W, l + dx)
            if "s" in edge: b = max(t + MIN_H, b + dy)
            if "n" in edge: t = min(b - MIN_H, t + dy)
            WINDOW.resize(r - l, b - t)
            WINDOW.move(l, t)
            return
        scale = USER32.GetDpiForWindow(ctypes.c_void_p(int(WINDOW.native.Handle.ToInt64()))) / 96
        mw, mh = int(MIN_W * scale), int(MIN_H * scale)
        dx, dy = int(dx), int(dy)
        if "e" in edge: r = max(l + mw, r + dx)
        if "w" in edge: l = min(r - mw, l + dx)
        if "s" in edge: b = max(t + mh, b + dy)
        if "n" in edge: t = min(b - mh, t + dy)
        u32 = USER32
        u32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
        u32.SetWindowPos(ctypes.c_void_p(int(WINDOW.native.Handle.ToInt64())), None, l, t, r - l, b - t, 0x0004 | 0x0010)

    @own_page_only
    def toggle_size(self) -> bool:
        """Fill the screen (minus the taskbar) or go back to the previous size. Returns True when big."""
        return toggle_big()

    @own_page_only
    def close(self) -> None:
        for j in list(JOBS.values()):
            j.cancel()
        WINDOW.destroy()


def load_settings() -> dict:
    s = dict(DEFAULTS)
    try:
        with _settings_lock:
            saved = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return s
    if isinstance(saved, dict):
        s.update({k: v for k, v in saved.items() if k in SETTING_CHECKS and SETTING_CHECKS[k](v)})
        if isinstance(saved.get("out"), str) and saved["out"].strip():
            s["out"] = saved["out"]
        if isinstance(saved.get("last_update"), int):  # set by grabF itself, never by the page
            s["last_update"] = saved["last_update"]
    return s


# --- is there a newer grabF? ---
_release_url = ""


def version_tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def check_app_update() -> None:
    """Ask GitHub once per start whether a newer grabF release exists. Only *tells* you - you download
    and run the new installer yourself; grabF never installs code on its own."""
    global _release_url
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", UPDATE_REPO or ""):
        return
    try:
        import urllib.request
        req = urllib.request.Request(f"{API_BASE}/repos/{UPDATE_REPO}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": f"grabF/{VERSION}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            rel = json.loads(r.read(200_000).decode("utf-8"))
        tag, url = str(rel.get("tag_name", "")), str(rel.get("html_url", ""))
        # only ever open this repo's own release pages
        if version_tuple(tag) > version_tuple(VERSION) and url.startswith(f"https://github.com/{UPDATE_REPO}/releases/"):
            _release_url = url
            push("appupdate", {"version": ".".join(map(str, version_tuple(tag)))})
    except Exception:
        pass  # offline or GitHub unreachable: try again next start


# --- keeping the engines fresh ---
ENGINES = ["yt-dlp[default,curl-cffi]", "gallery-dl"]
UPDATE_EVERY = 7 * 86400
_updating = threading.Lock()


def update_engines(wait: bool = False) -> bool:
    """pip-update the two engines in grabF's own Python. New versions load the next time grabF starts.
    wait=True: if an update is already running (the weekly one), wait for it instead of giving up."""
    if not _updating.acquire(blocking=wait, timeout=-1 if not wait else 200):
        return False  # one update at a time
    try:
        # --timeout: a stalled connection gives up after 15 s instead of hanging the button
        r = subprocess.run([PYEXE, "-m", "pip", "install", "-q", "-U", "--disable-pip-version-check",
                            "--timeout", "15", *ENGINES],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0:
            with _settings_lock:
                s = load_settings()
                s["last_update"] = int(time.time())
                write_settings(s)
            return True
        return False
    except Exception:
        return False
    finally:
        _updating.release()


def restart_needed() -> bool:
    """True when the engines on disk are newer than the ones this running grabF has loaded."""
    try:
        import importlib
        import importlib.metadata
        import gallery_dl.version
        importlib.invalidate_caches()
        on_disk = (importlib.metadata.version("yt-dlp"), importlib.metadata.version("gallery-dl"))
        loaded = (yt_dlp.version.__version__, gallery_dl.version.__version__)
        return on_disk != loaded
    except Exception:
        return False


def weekly_update() -> None:
    """Runs quietly after the window opens. Offline or failed: it just tries again next start."""
    if time.time() - load_settings().get("last_update", 0) < UPDATE_EVERY:
        return
    if update_engines() and restart_needed():
        push("engines", {"state": "restart"})


def restart_app() -> bool:
    """Start a fresh grabF and close this one, so updated engines get loaded. Never while downloading."""
    if JOBS:
        return False
    exe = Path(sys.executable)
    if WINDOWS and exe.name.lower() == "python.exe" and exe.with_name("pythonw.exe").is_file():
        exe = exe.with_name("pythonw.exe")  # no console window
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if WINDOWS else 0
    subprocess.Popen([str(exe), str(HERE / "app.py")], cwd=str(HERE), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
                     start_new_session=not WINDOWS)
    WINDOW.destroy()
    return True


def write_settings(s: dict) -> None:
    with _settings_lock:
        write_json(CONFIG, s, indent=2)


APP_ID = "grabF.downloader"  # also stamped on the shortcuts by make_shortcuts.py, so they group together


def use_own_icon() -> None:
    """Show grabF's icon on the window and taskbar instead of Python's."""
    if not WINDOWS:
        return
    try:
        hwnd = int(WINDOW.native.Handle.ToInt64())
        ico = str(HERE / "icon.ico")
        u32 = USER32
        u32.LoadImageW.restype = ctypes.c_void_p
        u32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        for which, size in ((1, 256), (0, 16)):  # ICON_BIG, ICON_SMALL
            h = u32.LoadImageW(None, ico, 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            if h:
                u32.SendMessageW(hwnd, 0x80, which, h)  # WM_SETICON
    except Exception:
        pass  # cosmetic only


# --- window size and place ---
BASE_W, BASE_H, MIN_W, MIN_H = 680, 820, 520, 560  # 680: wide enough for the whole footer on one line
_before_big: tuple[int, int, int, int] | None = None  # where the window was before 'bigify'
_resize_from: tuple[int, int, int, int] | None = None  # window rect when an edge drag started


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


def _work_area(hwnd: int | None = None) -> _RECT:
    """Usable part (no taskbar) of the screen the window is on - or, before it exists, the screen the mouse is on."""
    u32 = USER32
    u32.MonitorFromWindow.restype = u32.MonitorFromPoint.restype = ctypes.c_void_p
    u32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    if hwnd:
        mon = u32.MonitorFromWindow(ctypes.c_void_p(hwnd), 2)
    else:
        pt = ctypes.wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        u32.MonitorFromPoint.argtypes = [ctypes.wintypes.POINT, ctypes.c_ulong]
        mon = u32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
    mi = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
    u32.GetMonitorInfoW(mon, ctypes.byref(mi))
    return mi.rcWork


def start_geometry() -> dict:
    """Open at the normal size, centred on the screen you're using, shrunk to fit short screens."""
    if not WINDOWS:
        return {"width": BASE_W, "height": BASE_H}
    try:
        USER32.SetProcessDPIAware()  # same mode pywebview uses: real pixels from here on
        try:
            scale = USER32.GetDpiForSystem() / 96  # what pywebview multiplies sizes by
        except Exception:
            scale = 1.0
        wa = _work_area()
        ax, ay = wa.left / scale, wa.top / scale
        aw, ah = (wa.right - wa.left) / scale, (wa.bottom - wa.top) / scale
        w = int(min(BASE_W, aw - 32))
        h = int(min(BASE_H, ah - 32))  # a short laptop screen gets a shorter window, not one under the taskbar
        return {"width": w, "height": h, "x": int(ax + (aw - w) / 2), "y": int(ay + (ah - h) / 2)}
    except Exception:
        return {"width": BASE_W, "height": BASE_H}


def place_and_show() -> None:
    """The window starts invisible. Once the page is drawn, centre the real window (whatever size
    pywebview actually made it) on the screen you're using, fit it to that screen, then show it."""
    try:
        if not WINDOWS:
            raise OSError("placed by pywebview")  # it already opens centred there
        u32 = USER32
        u32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
        hwnd = int(WINDOW.native.Handle.ToInt64())
        r = _RECT()
        u32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r))
        wa = _work_area()  # the screen the mouse is on = the one you just clicked the shortcut on
        aw, ah = wa.right - wa.left, wa.bottom - wa.top
        w = min(r.right - r.left, aw - 32)
        h = min(r.bottom - r.top, ah - 32)
        u32.SetWindowPos(ctypes.c_void_p(hwnd), None, wa.left + (aw - w) // 2, wa.top + (ah - h) // 2, w, h,
                         0x0004 | 0x0010)  # SWP_NOZORDER | SWP_NOACTIVATE
    except Exception:
        pass  # worst case it opens where Windows puts it
    WINDOW.show()
    use_own_icon()
    threading.Thread(target=warm_up, daemon=True).start()  # less common sites: ready a moment later
    threading.Thread(target=weekly_update, daemon=True).start()
    threading.Thread(target=check_app_update, daemon=True).start()


_sizing_lock = threading.Lock()


def toggle_big() -> bool:
    with _sizing_lock:  # two calls at once would both see 'not big' and both enlarge
        return _toggle_big()


def _toggle_big() -> bool:
    global _before_big
    if not WINDOWS:  # the system's own maximise and restore
        if _before_big:
            _before_big = None
            WINDOW.restore()
            return False
        _before_big = (0, 0, 0, 0)
        WINDOW.maximize()
        return True
    u32 = USER32
    u32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
    hwnd = int(WINDOW.native.Handle.ToInt64())
    flags = 0x0004 | 0x0010  # SWP_NOZORDER | SWP_NOACTIVATE
    if _before_big:
        u32.SetWindowPos(ctypes.c_void_p(hwnd), None, *_before_big, flags)
        _before_big = None
        return False
    r = _RECT()
    u32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r))
    _before_big = (r.left, r.top, r.right - r.left, r.bottom - r.top)
    wa = _work_area(hwnd)
    u32.SetWindowPos(ctypes.c_void_p(hwnd), None, wa.left, wa.top, wa.right - wa.left, wa.bottom - wa.top, flags)
    return True


def move_from_old_name() -> None:
    """The app used to be called 'grab'. Once, on the first start as grabF: move its settings folder to the new
    name, and the download folder too if it's still the old default, fixing the saved paths so past downloads
    keep opening. If anything is in use and can't be moved, everything stays where it is and keeps working."""
    old_cfg = CONFIG.parent.with_name("grab")
    if CONFIG.parent.exists() or not old_cfg.is_dir():
        return
    try:
        os.replace(old_cfg, CONFIG.parent)
    except OSError:
        try:
            shutil.copytree(old_cfg, CONFIG.parent)
        except OSError:
            return
    try:
        s = load_settings()
        old_out, new_out = Path.home() / "Downloads" / "grab", Path(DEFAULTS["out"])
        if Path(s["out"]) == old_out and old_out.is_dir() and not new_out.exists():
            os.replace(old_out, new_out)
            with _settings_lock:
                s["out"] = str(new_out)
                write_settings(s)
            with _history_lock:
                h = load_history()
                for e in h:
                    e["files"] = [str(new_out / Path(f).relative_to(old_out))
                                  if isinstance(f, str) and Path(f).is_relative_to(old_out) else f
                                  for f in e.get("files") or []]
                write_json(HISTORY, h)
    except Exception:
        pass  # the settings moved; a download folder that couldn't be renamed just stays as it is


def main() -> None:
    global WINDOW
    move_from_old_name()
    if WINDOWS:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass
    Path(load_settings()["out"]).mkdir(parents=True, exist_ok=True)
    WINDOW = webview.create_window(
        "grabF", str(WEB / "index.html"), js_api=Api(), **start_geometry(), min_size=(MIN_W, MIN_H),
        frameless=True, easy_drag=False, background_color="#efe9dd", hidden=True,
    )
    shown_once = threading.Event()

    def reveal(*_):
        if not shown_once.is_set():
            shown_once.set()
            place_and_show()

    WINDOW.events.loaded += reveal  # show once the page is drawn - no blank flash
    # never stay invisible: if the page is slow or fails to load, show the window anyway
    fallback = threading.Timer(6, reveal)
    fallback.daemon = True  # never keeps grabF running after you close it
    fallback.start()
    # private_mode: the window keeps no cookies, cache or storage of its own between runs
    webview.start(debug="--debug" in sys.argv, private_mode=True)


# --- blocked sites ---
# Sites grabF refuses: adult sites, leaks of paid content, leak/piracy file hosts, unofficial manga/comic scans.
# Stored only as salted one-way hashes, so no site names live in this code. Adult content anywhere is refused
# separately by its 18+ flag. The salt must never change, or none of these would match any more.
BLOCK_SALT = "grab/v1:"
BLOCKED_HASHES = frozenset({
    "00077686a2c5a7ff15573a3d",
    "00d8eb2a2f6e05568fce6dfc",
    "017344dce43db9f2b4412613",
    "026e234b0600103d2a723e68",
    "02e296308f2fe9d2ca480f13",
    "0442b35d550e480231d87a5f",
    "04c92687e31ddbc96b063d11",
    "04edda95b0a39db6b5e5006f",
    "0508d736aeb8dd7072977b6b",
    "0534cbf2b03010842cb7c7db",
    "061b0e3ced026ca814ad9e3f",
    "08c447bf8fd55b512dc4a8e4",
    "0afb3d70695a6c43b81b132a",
    "0cc064cbb42c247115254a48",
    "0de16bdc79bf35ff93781ad5",
    "0e0dc65bcec43bd6296ac012",
    "0e47d4284ee959b814b7a7c9",
    "0e758fd8f20524f687f54382",
    "0f083b2405e6ec35b926c814",
    "0f67568f0ac7f763fb1eecf1",
    "0fb7bccb97017bbcbaa1e4a7",
    "11572e2de76d2bbfd9618c03",
    "11f2208a7a351794450547c9",
    "1205e3c5e0868253a68991ba",
    "12bfac1b536c903390f3e720",
    "14b836fe7ed2144e523c936e",
    "152b47d7a64f15fdc7326c14",
    "156d644950070b3cbeb430b6",
    "17904d53be398761b4ccd8c1",
    "17d6a46a18b7c86127ce0539",
    "17f8ced292bc34487d287319",
    "196ff03e2345daa1ba89e243",
    "19c571f4bb7b3838f842e5fd",
    "1b1abda76c578c00c3295a04",
    "1c7f309e1e106f66d29c06e5",
    "1e35df39914d9a713e21b571",
    "1f6395545ea93a54a6a1ce1f",
    "1ff2c71cad99a9c38ac23926",
    "210b7583e9e1705009e91221",
    "21570ca259f09d76067dec8f",
    "24f3878683b7079f6cbf1993",
    "252307c9358c5a9a84038161",
    "25c0f9b97424af20b70ee539",
    "25e4a8ddb2f0aa86e40b0f45",
    "2605d712c508d0afdedf5507",
    "2681287d5c91aea5eb8b41dd",
    "27212b81c3f990a062bad030",
    "277d7e4de67b9f2c567aac77",
    "28352760a64951c92ed07b6e",
    "2847f2bdc961e5cc80f14efb",
    "2875b2e3ca6e06b2d20051ba",
    "2903c00f0110d5c4a8522490",
    "2a32634aff03471d8b7d982d",
    "2af829944a0fd295c4100c5c",
    "2b0d91579763ab7625a0752d",
    "2b0dae390a4f64f420816881",
    "2c25e1cd8372b005cadaf461",
    "2dcbe61d14a2c0ab634802c8",
    "2e036fd323d34bb3668caa33",
    "2e8da3280230d6204876549a",
    "303231310c5576d220fc2783",
    "30677abb91f00892ea151ade",
    "30eb7a4d58e92058a1be97e1",
    "31cb8f391231456682fb18a0",
    "334b649f5f5d924c0909601e",
    "33c99c2c804433633953a968",
    "34f8b0d1fb58fc81af9340cd",
    "352bf20d2aa4c9431cb594bb",
    "35936004e76ab5b2ce604c41",
    "35af2f38f12671ad4effdc05",
    "36697efb749dcf72de400deb",
    "36f418ee57f89292c75a82cf",
    "375e611cc3ccd2e3f5829fbd",
    "39578dfa9bc11e0cbc087067",
    "39d0cb0404ad852fa084b940",
    "3a4ce8e64fe0e3e0aa043fed",
    "3ac8a74a8450c93eb7c32af1",
    "3c24dd749b41e6d6cf5f282d",
    "3cebcd325e62c0ed9f2de83a",
    "3d1310ab5c2e14c6ede348b2",
    "3e1ba53a173deaa624d2952f",
    "3fa167b31ec9c8bd3d80e231",
    "42bee94e12f02a208602e6ee",
    "44616f36a36836eb908ed69a",
    "44c5305e3e29552cf652c92e",
    "4789841ad8dbcee9527672a9",
    "4851ea0ea23c5a40f2d89f52",
    "491455bef2081b8edec0a96d",
    "4b1875969ec49e9c537d8aca",
    "4b2703cf96ec898add7a6697",
    "4c262f9b8bc96e7e016aa497",
    "4cdbbf9ef90c74701f3eb5b2",
    "4e309df65e8e3c0a890e5a68",
    "4e98cebfb9ccdb1496da875e",
    "4f272f90e997ece10f94468b",
    "50085c12896db65413a90f13",
    "51252a0fe5dc6dbb37ff128e",
    "51291b33390a235df11039ac",
    "51fac0d5db2b40c40cbe7d1c",
    "51fbdbcb764361d042f28378",
    "524add88414e9ad4f77dd2c8",
    "536bac21a3323f9ef844f03f",
    "53c07549ac5d814cd624d29c",
    "543efcba33d04621d348b9a2",
    "5441e6bf1c7460b83d33005a",
    "5681bae5e68eaaf2c4efd558",
    "571e2daf89f89b1565d17ef1",
    "5788fdb2cb5a449fb2115355",
    "579d5df808316769ad413919",
    "593bebe7d39eec62518b39dc",
    "59c147aec42877ab4c536557",
    "59d91eba6bc743e2368b51f3",
    "59e6ac4dd9dd3266d844f13a",
    "5a7a33091e1dcc74446c27b1",
    "5c4b4e32232a4fde70edca6f",
    "5ee3bfba325d889c1cebca09",
    "5ee850f32ca7e2b891f6f11b",
    "5f6247f21aed4a47bddff030",
    "5f991933aabb5ed8539f68c0",
    "6055fdbcee81f522fbf07ce5",
    "60bc29e8f27f007d716b4d2a",
    "619a2f01e58f44411b8a7f0e",
    "61c12eb568022296b715d445",
    "62e0a43eab795307d683fd07",
    "6310d07720d7bb9bab759f0a",
    "6344fb8f0acad406572cdf4b",
    "6359c8695e35e1b898827de7",
    "64173e51f5fa11cb5ffaf636",
    "64bc0eb10907ec1ed13b5eec",
    "655fb1f0e705ad1298682b25",
    "66864741b31a5eb16e00ed6c",
    "66c1f9a36d76833c7fc338e3",
    "66c74480329e56667c0a79ff",
    "6737bb3c00482c6f1cb4cc60",
    "674bf1be6c5c57b63b72ced3",
    "67ae24a47949472d8563f9dc",
    "68b53264dcb8983924695731",
    "68d0b06c51bc0ee0cffe010a",
    "6af66a95413460925ed05381",
    "6c653747dc6eb1864a1fe6f8",
    "6d30e5f995c4df72fe355d5a",
    "6f3819d075d68071d79e11f3",
    "7233c02d76801d53ea53dac9",
    "726b66b04c22076f06045749",
    "72fbfd37e5eca4d6522af386",
    "7313a78ff007b40ed4e40ff3",
    "73eb8ea5e0c182c5a28f40e9",
    "757e30d3b62d652fb656a43a",
    "75a8b31c88f73ff6179d6cd4",
    "75d0afc7768864fe8328b6cf",
    "76059d6aa606d8267c4bb2d3",
    "76972952cc32c7ed5c48b425",
    "77ba5eb57cfc7a24a96b1bcd",
    "78339840e08b837d42e8f736",
    "788d92b921771243c1a3e408",
    "789791130a0574eab01f1e0c",
    "7912a81c07813add4a906789",
    "79341d5a26b03321b42ffdb7",
    "79b2bcf90fc0c99a05c27a79",
    "79f9193e1a99c9de850f5dde",
    "7a3c2833e64b8336f4fa6ee0",
    "7d079e3d0c75d2880e9caa88",
    "7db82af6b4bad01ef6882d42",
    "7e51072de0f4502aebb6db35",
    "7e6b17c07374b20b68393b44",
    "7fa4c3764ee65571045681ba",
    "7fd50dc7c44dd22f17713e28",
    "80e91410dad28885e4594f30",
    "819e4bc7db9b6f96e7b220d5",
    "8342ac48f1777d8d62b77593",
    "85b8e44eb60e345416fc6e4a",
    "87577ff026f67d4b9d703e02",
    "887447571cda09bcec12bdc3",
    "8b2d44a0026a25b6f572e264",
    "8b63a1de46251b2cf4f7537d",
    "8ba7b97821e6a745617b6ab2",
    "8c8e0d612f4694f95421b9d0",
    "8d88edc7ab5fa186fc64eac3",
    "8d9cd0d1e4cb73d0688f0f42",
    "8e2ce3f160eb3ca9a5b75093",
    "8f4482fa13583cf236a2a218",
    "9047a1f66595a52f8eb7bf7d",
    "90f10621d4e9c0e49b46f5e4",
    "9358a72e9ebc96fb478554e4",
    "93b488e158c4c5f5c0b01a6b",
    "93c155c4ce2fb691bb63a44a",
    "94382a266d70f33f10394ef4",
    "944908dbf95d3c5a6c43370e",
    "947b439028d17acd6ffd8529",
    "962d73b6b156c519332fcddd",
    "9682099ea0d29cc881f8a893",
    "96a8c41e3e09c522393bb890",
    "96d56bc7646b7bdafecd6a29",
    "96d576aeadac2ae607eef460",
    "9817019fe903ee4a3f4e4ada",
    "9851e2f5f349cef3d7784503",
    "989ba0dafcc7605f6364220c",
    "9af0f385ba0d2efecb5aaa72",
    "9bdbaa67a4a9ed78a056901f",
    "9bf3b29ce26cf223a2e12049",
    "9cad26f217ce8551abd9de1b",
    "9cbfa23e3ec39c7fc9ddb2c9",
    "9d77fc862d1a8db92afc217b",
    "9dee1fdf5e740889780fc7e6",
    "a003dced6e15e650688cca64",
    "a03bf886e781a40c53bada51",
    "a0b5f59fc8e5cf0ca6a90ddb",
    "a19d3950a7a9ff73ee7efee2",
    "a279cade2a7ddb91373882ac",
    "a299aa9be0094bcab485a2f9",
    "a36fc6587386a48337a8c6a0",
    "a3e18bc30d6765d57bab145b",
    "a57f243bf363a75dd001602b",
    "a647147eca5b2e3cf2eb6f1c",
    "a67e8ad87f9585fb1f7d329f",
    "a6f78b0e3466197fd744f0d5",
    "a71a18f6b565f9872e294795",
    "a7e603f27d2e868fd8fac0e9",
    "a87549444e1f18353cae6419",
    "a92fb0dba035f10aafc2b6ab",
    "aa348a017d721048ea5dec42",
    "aa45041a32b3cac16ee093a0",
    "aa691c175b44e5bf4994c6bc",
    "ab2cc184c61820257eb8ac55",
    "ab77161a9efbb1c65ca5dbe6",
    "acb3f654fe967edb216d3ee9",
    "aceec26597df9073be849352",
    "ad5a06689a5afc76619ea1e8",
    "adc6a8f795d9a6799d17b8f0",
    "ae667c64feee37a072e60040",
    "b052cf257f6b5f6168f883de",
    "b1e0220b230da7b30bd3190e",
    "b1fe32d8eb4fbc2f3ed93739",
    "b296867f9d5819b8c510890a",
    "b36dab6bb92edb5b0d62fea1",
    "b4581af1862326f827baa5bf",
    "b540ec2c1c83304147a5a4e4",
    "b6286a1617531031e1e7f6da",
    "b6db7df231dab325e52f1ce3",
    "b730011e8deddeb44a09a9c7",
    "b7da2195c21f7f27035692dd",
    "b8670e4693b622519a194dfb",
    "b8b3164d7339f7a9f1bc3193",
    "b93d52f6ab6a8cd0e52e4fe1",
    "ba4c23a352b5cd01111122a6",
    "bb14ba9ad506db5d8a37cd97",
    "bb7876fef51e7d344e7eadc8",
    "bb88a0b23408e6baafba6d10",
    "bc16e67d81534c976db896e1",
    "bdb2a2e596a4cbfbb65e187d",
    "be3a50063da14e9e1f991317",
    "c00b5ac3133264d919e672f1",
    "c0f475c883d3e3fffafa021c",
    "c15adc91457d07b36763d61e",
    "c284380b4b46d5fb41455eec",
    "c2fec0a60b50aeb7fabc3853",
    "c57dd6545e5fc1524f312732",
    "c59b34e2643483c1a0f03b85",
    "c6897232efcfc3f41e792a65",
    "c8996ad1805384d9ce4cda71",
    "c937be32fb9208be7d1942f9",
    "caa1a92fd4aac0738b2715b2",
    "cb348c32d4584e5afa47acf7",
    "cb8f5d9ca89ee56d880e3f45",
    "cbdb2918d5db4135d4afce47",
    "cc01f6ae236de875ee99ed45",
    "ccaf39cd0d2c95783b8cbd82",
    "cdb559d8d086a2184eca3e63",
    "cdcce712a1dba108a4857fb7",
    "cde7780e12e0fc83acb93177",
    "ce8cb705eb4572e567b3ddd4",
    "cf838500a38908035711c139",
    "d07fcac453cba2026314fe2f",
    "d0804d504c7cd1af72bf22b9",
    "d17e733a62ead255b56e492c",
    "d35f6622c0c9c248bcfcf45d",
    "d5d81c1c82255a9b25ddd61a",
    "d6b88b21af889453e1eb2616",
    "d78597de8d294ffc76e2fa4e",
    "d87057f9bbebd6d3b0b02f90",
    "d9348a827765bd57a013b01a",
    "da33c51fd63e2fca7ef538f8",
    "da39f12017e02572ea41d778",
    "df51fc2f939b2a96f8ac0126",
    "e01b7e52a68d00ca4f9e2435",
    "e25df0043aadaa3f8a45b1f0",
    "e26311aac083f2d845fef51a",
    "e38f83b74d9a28e6cd663d2f",
    "e3e589bc6b43d4869ea9503a",
    "e52b9cb7a9ce8c96bdcb2b8b",
    "e57a622b4800ea45b466744d",
    "e6a1cd080bd4e6323b5bce3a",
    "e6c548f66006cf3af5b91afc",
    "e8fc1833428778bf16c4f51b",
    "e934de66e1f4af3d9d479875",
    "e941832c945d8f672e61e1f9",
    "e978ea20561854928f535270",
    "eaf8e2f8ec77ac0cad307032",
    "eb37810f9f391d1090b95ff2",
    "ebe510e81ad19b087c99cd69",
    "ec22a44a54dcbda70c8b5159",
    "ec9afaf3378d434711824724",
    "ece18385d9b0e2cf1392dce9",
    "ee2c8e8be9fd0de92387811f",
    "ef15d5ba77762a7db37a1c99",
    "f0c97decb7fffec0c8df3e80",
    "f31b43be3a704544f31e1673",
    "f3fcd3c7f1dff180c79d1861",
    "f4fddd90ab88d9c147c5bb17",
    "f567c7299f27e70be6e3e849",
    "f579d230620b7effff8f9911",
    "f5989b8a9897ce0b82f5cb0f",
    "f69f83ebcce9f4f15f6dd23c",
    "f6e9d3ddbc7b02b82ad39537",
    "f708af00881d9d5a6ac8be4f",
    "f70b542699b3a57f5178d6e3",
    "f770af977ff7a33f021ada69",
    "f7e47df56d196448a89e3bbc",
    "f8fae102d471f1d88ac54a73",
    "f9a2abe0d9a9ff77b8095cff",
    "faf86e0751c523eccd1ecfbd",
    "fb415410124215b73c0eb860",
    "fb54e1fb7a876d119fade4e0",
    "fbf611a9ac54332409b14190",
    "fc00bc4d67f1f09600968881",
    "fe2afdef95ef0535a2aa1727",
    "fe64590fdf5372179d0bfc30",
    "fef39152d19ad73aec6fcc6d",
    "ffba9ea161907e3739c9a4fe",
})


def _block_hash(s: str) -> str:
    return hashlib.sha256((BLOCK_SALT + s).encode()).hexdigest()[:24]


def site_blocked(site_name: str) -> bool:
    """site_name: the engine's name for the site that would handle a link."""
    return bool(site_name) and _block_hash("site:" + site_name.lower()) in BLOCKED_HASHES


def host_blocked(host: str) -> bool:
    """True if this host, or any domain above it (a.b.example.com -> example.com), is blocked."""
    parts = (host or "").lower().removeprefix("www.").split(".")
    return any(_block_hash("host:" + ".".join(parts[i:])) in BLOCKED_HASHES for i in range(len(parts) - 1))


if __name__ == "__main__":
    main()
