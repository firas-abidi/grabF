"""Run grabF's tests and count the results.

    py -3 tests/run_tests.py              offline tests only (fast, no internet)
    py -3 tests/run_tests.py --network    + tests that read and download real public videos
    py -3 tests/run_tests.py --window     + tests that open the real window (keep it in front)
    py -3 tests/run_tests.py --all        everything above

Each suite prints one PASS or FAIL line per check. A suite that crashes counts as failed.
test_instagram_photo_dates.py needs your own Instagram login in Firefox; it only runs when
GRABF_TEST_INSTAGRAM_POST is set to a photo post link you can open.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
args = set(sys.argv[1:])
groups = {"offline"}
if "--network" in args or "--all" in args:
    groups.add("network")
if "--window" in args or "--all" in args:
    groups.add("window")
if os.environ.get("GRABF_TEST_INSTAGRAM_POST"):
    groups.add("login")

total_pass = total_fail = 0
for f in sorted(HERE.glob("test_*.py")):
    group = f.read_text(encoding="utf-8").split("\n", 1)[0].removeprefix("# group: ").strip()
    if group not in groups:
        print(f"{f.name:36} skipped ({group})")
        continue
    t = time.time()
    try:
        r = subprocess.run([sys.executable, str(f)], cwd=HERE, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
        out, code = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        out, code = "FAIL timed out", 1
    p = sum(1 for line in out.splitlines() if line.startswith("PASS"))
    n = sum(1 for line in out.splitlines() if line.startswith("FAIL"))
    if code != 0 and not n:
        n = 1  # crashed before reporting
    total_pass += p
    total_fail += n
    print(f"{f.name:36} {p:3} passed {n:3} failed  {time.time() - t:5.0f}s")
    for line in out.splitlines():
        if line.startswith("FAIL") or (code != 0 and ("Error" in line or "Traceback" in line)):
            print("    " + line[:200])
print(f"\n{total_pass} passed, {total_fail} failed")
sys.exit(1 if total_fail else 0)
