from __future__ import annotations

import subprocess
from pathlib import Path
from zipfile import ZipFile


def test_wheel_excludes_development_debugger(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(tmp_path.glob("*.whl"))
    with ZipFile(wheel) as archive:
        members = archive.namelist()

    assert any(member.startswith("sophie_bot/") for member in members)
    assert not any(member.startswith("debug/") for member in members)
    assert not any(member.endswith("config.env.example") for member in members)
