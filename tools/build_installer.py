"""Build dist/grabF-setup.exe: grabF with its own private Python, engines, ffmpeg and deno.

    py -3 tools/build_installer.py

Needs (on the build PC only): Inno Setup 6, and ffmpeg + deno installed (winget install Gyan.FFmpeg DenoLand.Deno).
The installed app needs nothing else. It installs per-user (no admin), so grabF can keep its own engines updated.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGE = Path(os.environ.get("GRAB_BUILD", Path(os.environ["TEMP"]) / "grabF-build"))
APP = STAGE / "grabF"
CACHE = STAGE / "cache"
PY_VER = "3.11.9"  # last 3.11 release with an official embeddable build
PY_URL = f"https://www.python.org/ftp/python/{PY_VER}/python-{PY_VER}-embed-amd64.zip"
GETPIP_URL = "https://bootstrap.pypa.io/get-pip.py"
PACKAGES = ["yt-dlp[default,curl-cffi]", "gallery-dl", "pywebview"]
APP_FILES = ["app.py", "icon.ico", "SUPPORTED_SITES.md"]
VERSION = re.search(r'^VERSION = "([\d.]+)"', (ROOT / "app.py").read_text(encoding="utf-8"), re.M).group(1)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def step(msg):
    print(f"\n== {msg}", flush=True)


def fetch(url: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / url.rsplit("/", 1)[-1]
    if not dest.exists():
        print("   downloading", url)
        with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    print(f"   {dest.name}  sha256 {hashlib.sha256(dest.read_bytes()).hexdigest()[:16]}...")
    return dest


def find_tool(name: str) -> Path:
    hit = shutil.which(name)
    if hit:
        return Path(hit).resolve()
    pkgs = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Packages"
    for p in pkgs.rglob(f"{name}.exe"):
        return p
    sys.exit(f"{name} not found - install it first (winget)")


def run(cmd, **kw):
    print("   $", " ".join(str(c) for c in cmd[:6]), "...")
    subprocess.run([str(c) for c in cmd], check=True, creationflags=NO_WINDOW, **kw)


def main():
    step(f"fresh stage in {STAGE}")
    shutil.rmtree(APP, ignore_errors=True)
    APP.mkdir(parents=True)

    step("private Python")
    py = APP / "python"
    with zipfile.ZipFile(fetch(PY_URL)) as z:
        z.extractall(py)
    pth = next(py.glob("python3*._pth"))
    # site-packages for the engines, '..' = grabF's own folder, 'import site' for pip
    pth.write_text(f"{pth.stem}.zip\n.\nLib\\site-packages\n..\nimport site\n", encoding="ascii")
    run([py / "python.exe", fetch(GETPIP_URL), "--no-warn-script-location", "-q"])
    run([py / "python.exe", "-m", "pip", "install", "-q", "--no-warn-script-location", *PACKAGES])

    step("app files")
    for f in APP_FILES:
        shutil.copy2(ROOT / f, APP / f)
    shutil.copytree(ROOT / "web", APP / "web")
    shutil.copytree(ROOT / "fonts", APP / "fonts")  # TTF copies for burned-in subtitles

    step("ffmpeg and deno")
    bin_ = APP / "bin"
    bin_.mkdir()
    ff = find_tool("ffmpeg")
    for exe in ("ffmpeg.exe", "ffprobe.exe"):
        shutil.copy2(ff.parent / exe, bin_ / exe)
    shutil.copy2(find_tool("deno"), bin_ / "deno.exe")
    lic = APP / "licenses"
    lic.mkdir()
    ff_root = ff.parent.parent
    for name in ("LICENSE", "README.txt"):
        if (ff_root / name).exists():
            shutil.copy2(ff_root / name, lic / f"ffmpeg-{name}")
    (lic / "THIRD-PARTY.txt").write_text(
        "grabF bundles:\n"
        "- Python (PSF License)  https://www.python.org/\n"
        "- yt-dlp (Unlicense)  https://github.com/yt-dlp/yt-dlp\n"
        "- gallery-dl (GPL-2.0)  source: https://github.com/mikf/gallery-dl\n"
        "- pywebview (BSD-3-Clause)  https://github.com/r0x0r/pywebview\n"
        "- FFmpeg (GPL build by gyan.dev)  source: https://ffmpeg.org/download.html  build: https://www.gyan.dev/ffmpeg/builds/\n"
        "- Deno (MIT)  https://github.com/denoland/deno\n"
        "- Jost and Instrument Serif fonts (SIL Open Font License 1.1)  https://fonts.google.com/\n"
        "- TeX Gyre Heros font (GUST Font License, see fonts/TeXGyreHeros-LICENSE.txt)  https://www.gust.org.pl/projects/e-foundry/tex-gyre\n",
        encoding="utf-8")

    step("smoke test the private Python")
    run([py / "python.exe", "-c",
         "import webview, yt_dlp, gallery_dl, curl_cffi, clr; print('engines ok', yt_dlp.version.__version__)"])

    step("installer")
    iscc = next((p for p in [Path(os.environ["LOCALAPPDATA"]) / "Programs/Inno Setup 6/ISCC.exe",
                             Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6/ISCC.exe"] if p.exists()), None)
    if not iscc:
        sys.exit("Inno Setup 6 not found (winget install JRSoftware.InnoSetup)")
    iss = STAGE / "grabF.iss"
    iss.write_text(ISS.format(src=APP, out=ROOT / "dist", icon=ROOT / "icon.ico", version=VERSION), encoding="utf-8")
    run([iscc, "/Q", iss])
    out = ROOT / "dist" / "grabF-setup.exe"
    print(f"\nbuilt {out}  ({out.stat().st_size / 1048576:.1f} MB)")


ISS = r'''
[Setup]
AppId={{{{8C3A6F2E-5B1D-4E7A-9F0C-2D6B8E4A1C37}}
AppName=grabF
AppVersion={version}
AppPublisher=grabF
DefaultDirName={{localappdata}}\Programs\grabF
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={out}
OutputBaseFilename=grabF-setup
SetupIconFile={icon}
UninstallDisplayIcon={{app}}\icon.ico
UninstallDisplayName=grabF
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Put grabF on the desktop"

[Files]
Source: "{src}\*"; DestDir: "{{app}}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{{userprograms}}\grabF"; Filename: "{{app}}\python\pythonw.exe"; Parameters: """{{app}}\app.py"""; WorkingDir: "{{app}}"; IconFilename: "{{app}}\icon.ico"; AppUserModelID: "grabF.downloader"; Comment: "download video, audio and photos"
Name: "{{userdesktop}}\grabF"; Filename: "{{app}}\python\pythonw.exe"; Parameters: """{{app}}\app.py"""; WorkingDir: "{{app}}"; IconFilename: "{{app}}\icon.ico"; AppUserModelID: "grabF.downloader"; Tasks: desktopicon

[Run]
Filename: "{{app}}\python\pythonw.exe"; Parameters: """{{app}}\app.py"""; WorkingDir: "{{app}}"; Description: "Open grabF"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; the engines update themselves after install, so remove whatever is there now
Type: filesandordirs; Name: "{{app}}\python"
Type: filesandordirs; Name: "{{app}}\__pycache__"
'''

if __name__ == "__main__":
    main()
