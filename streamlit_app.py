"""Entrypoint for Streamlit Community Cloud (defaults to streamlit_app.py)."""
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
try:
    os.chdir(ROOT_DIR)
except Exception:
    pass

try:
    from utils import sanitize_environment
    sanitize_environment()
except Exception:
    pass

import runpy

app_path = ROOT_DIR / "app.py"
runpy.run_path(str(app_path), run_name="__main__")
