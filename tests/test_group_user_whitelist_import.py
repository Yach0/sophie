from __future__ import annotations

import os
import subprocess
import sys


def test_group_whitelist_imports_before_model_package() -> None:
    environment = os.environ.copy()
    environment["TESTING"] = "1"

    result = subprocess.run(
        [sys.executable, "-c", "import sophie_bot.utils.group_whitelist"],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr
