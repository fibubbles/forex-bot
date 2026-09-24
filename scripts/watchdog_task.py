"""Entry point for Windows Task Scheduler (run with pythonw.exe: no console window).

pythonw has no console, so every start is recorded and any crash is written to a file.
"""
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
(ROOT / "logs").mkdir(exist_ok=True)
with open(ROOT / "logs" / "watchdog_task_runs.log", "a", encoding="utf-8") as f:
    f.write(f"{datetime.now().isoformat()} started python={sys.executable} cwd={os.getcwd()}\n")

os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import site  # noqa: E402

site.addsitedir(str(ROOT / ".venv" / "Lib" / "site-packages"))  # venv packages without the venv launcher

try:
    from src.watchdog import main

    code = main()
except Exception:
    with open(ROOT / "logs" / "watchdog_task_error.log", "a", encoding="utf-8") as f:
        f.write(f"\n[{datetime.now().isoformat()}] python={sys.executable}\n{traceback.format_exc()}")
    code = 1

sys.exit(code)