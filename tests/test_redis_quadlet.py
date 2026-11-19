from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIRECTORY = Path(__file__).resolve().parents[1] / "deploy" / "templates"
DATA_DIRECTORY = "/home/containers/.local/share/sophie-redis"
GENERATOR = Path("/usr/libexec/podman/quadlet")


@pytest.mark.parametrize(
    ("base_volumes", "retained_volumes"),
    [
        ([], []),
        (["/var/sophie/redis:/data:Z"], []),
        (["redis-data:/data", "/etc/redis:/etc/redis:ro"], ["/etc/redis:/etc/redis:ro"]),
        (["/data", "/data-backup:/backup:ro"], ["/data-backup:/backup:ro"]),
        (["/obsolete:/config", "", "/current:/config:ro", "/data"], ["/current:/config:ro"]),
    ],
)
def test_persistence_replaces_only_the_data_volume(
    base_volumes: list[str], retained_volumes: list[str], tmp_path: Path
) -> None:
    environment = Environment(loader=FileSystemLoader(TEMPLATE_DIRECTORY), trim_blocks=True, lstrip_blocks=True)
    rendered = environment.get_template("redis-persistence.conf.j2").render(
        redis_volumes=base_volumes,
        redis_data_dir=DATA_DIRECTORY,
        redis_exec="redis-server --requirepass test-password",
    )
    volume_settings = [line for line in rendered.splitlines() if line.startswith("Volume=")]
    assert volume_settings == [
        "Volume=",
        *[f"Volume={volume}" for volume in retained_volumes],
        f"Volume={DATA_DIRECTORY}:/data:rw,Z",
    ]
    assert 'Exec=redis-server --requirepass test-password --save "900 1 300 10 60 10000"' in rendered

    if not GENERATOR.exists():
        return

    (tmp_path / "redis.container").write_text(
        "[Container]\nImage=docker.io/redis:7\nPodmanArgs=--memory=512M\n"
        + "".join(f"Volume={volume}\n" for volume in base_volumes)
    )
    dropin_directory = tmp_path / "redis.container.d"
    dropin_directory.mkdir()
    (dropin_directory / "90-persistence.conf").write_text(rendered)
    generated = subprocess.run(
        [str(GENERATOR), "--dryrun", "--user"],
        env={**os.environ, "QUADLET_UNIT_DIRS": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
    )
    command = next(line for line in generated.stdout.splitlines() if line.startswith("ExecStart="))
    assert f"-v {DATA_DIRECTORY}:/data:rw,Z" in command
    assert command.count(":/data") == 1
    assert "-v /var/sophie/redis:/data" not in command
    assert "-v redis-data:/data" not in command
    assert "--memory=512M" in command
    for volume in retained_volumes:
        assert f"-v {volume}" in command
