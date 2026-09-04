import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
for _library in ("libs/stf", "libs/ass"):
    _library_path = _PROJECT_ROOT / _library
    if _library_path.is_dir():
        sys.path.insert(0, str(_library_path))
