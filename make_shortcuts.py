"""Create the 'grabF' shortcuts on the desktop and in the Start menu (Windows).

Run once:  py -3 make_shortcuts.py
The shortcuts carry grabF's app ID, so the running window groups with them on the taskbar
and shows grabF's icon instead of Python's.
"""

import os
import shutil
import sys
from pathlib import Path

import pythoncom
from win32com.client import Dispatch
from win32com.propsys import propsys, pscon

HERE = Path(__file__).resolve().parent
APP_ID = "grabF.downloader"  # must match APP_ID in app.py


def pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else shutil.which("pyw") or exe)


def make(path: Path) -> None:
    sh = Dispatch("WScript.Shell").CreateShortcut(str(path))
    sh.TargetPath = pythonw()
    sh.Arguments = f'"{HERE / "app.py"}"'
    sh.WorkingDirectory = str(HERE)
    sh.IconLocation = str(HERE / "icon.ico")
    sh.Description = "download video, audio and photos"
    sh.Save()
    store = propsys.SHGetPropertyStoreFromParsingName(str(path), None, 2, propsys.IID_IPropertyStore)  # GPS_READWRITE
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID, pythoncom.VT_LPWSTR))
    store.Commit()
    print("made", path)


if __name__ == "__main__":
    desktop = Path(os.path.expandvars(Dispatch("WScript.Shell").SpecialFolders("Desktop")))
    start = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    for folder in (desktop, start):
        make(folder / "grabF.lnk")
