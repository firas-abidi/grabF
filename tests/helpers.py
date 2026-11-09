"""Shared set-up for every test: grabF's folder on the import path, and a fresh temp folder per run.

Settings and history are pointed into that temp folder before any test runs, so the tests never touch
your real grabF settings, history or downloads.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = Path(tempfile.mkdtemp(prefix="grabF-tests-"))

import app  # noqa: E402

app.CONFIG = WORK / "settings.json"
app.HISTORY = WORK / "history.json"
