"""Development runner with hot-reload support using watchfiles."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Literal

from watchfiles import Change, run_process

from sophie_bot.utils.logger import log


def run_with_reload(mode: Literal["bot", "scheduler"]) -> None:
    """
    Run the specified mode with hot-reload using watchfiles.

    This function watches the sophie_bot directory for changes and restarts
    the process when files are modified.

    Args:
        mode: The mode to run ('bot' or 'scheduler')
    """
    project_root = Path(__file__).parent.parent.parent
    watch_dirs = [str(project_root / "sophie_bot")]
    log.info(f"Starting {mode} mode with hot-reload enabled...")
    log.info(f"Watching directories: {watch_dirs}")

    with suppress(KeyboardInterrupt):
        run_process(
            *watch_dirs,
            target=_run_mode_subprocess,
            args=(mode,),
            watch_filter=_python_filter,
        )


def _python_filter(_: Change, path: str) -> bool:
    """Filter to only watch Python files."""
    return path.endswith(".py")


def _run_mode_subprocess(mode: str) -> None:
    """Run the mode in a subprocess."""

    env = os.environ.copy()
    env["DEV_RELOAD"] = "false"
    env["MODE"] = mode

    try:
        result = subprocess.run(
            [sys.executable, "-m", "sophie_bot"],
            env=env,
            cwd=Path(__file__).parent.parent.parent,
            check=False,
        )
        if result.returncode != 0:
            log.warning(f"Subprocess exited with code {result.returncode}")
    except KeyboardInterrupt:
        log.error("Process interrupted")
