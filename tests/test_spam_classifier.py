from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from shlex import split
from subprocess import run
from unittest.mock import MagicMock

import numpy as np
import pytest
import requests

from sophie_bot.utils import spam_classifier


@pytest.mark.parametrize("target", ["build_onefile", "build_standalone"])
def test_compiled_build_prepares_and_bundles_model(target: str) -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = run(
        ["make", "--dry-run", target, "ASS_PATH="],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    commands = [split(line) for line in result.stdout.splitlines()]
    preparation = ["uv", "run", "python", "sophie_bot/utils/spam_classifier.py"]
    compilation = next(command for command in commands if command[:5] == ["uv", "run", "python", "-m", "nuitka"])
    assert preparation in commands
    assert commands.index(preparation) < commands.index(compilation)
    model_path = spam_classifier.MODEL_PATH.relative_to(project_root).as_posix()
    assert f"--include-data-files={model_path}={model_path}" in compilation
    assert "--include-package=sklearn" in compilation


@pytest.fixture
def classifier_cache() -> Iterator[None]:
    spam_classifier.load_classifier.cache_clear()
    yield
    spam_classifier.load_classifier.cache_clear()


@pytest.mark.parametrize("classes, probabilities", [([0, 1], [0.1, 0.9]), ([1, 0], [0.9, 0.1])])
def test_binary_artifact_and_cached_loading(
    monkeypatch: pytest.MonkeyPatch,
    classifier_cache: None,
    classes: list[int],
    probabilities: list[float],
) -> None:
    model = MagicMock(classes_=np.array(classes))
    model.predict_proba.return_value = np.array([probabilities])
    loader = MagicMock(return_value=model)
    monkeypatch.setattr(spam_classifier.joblib, "load", loader)

    for text in ("buy now", "/etc/passwd"):
        result = spam_classifier.predict_message(text)
        assert result == {"spam_probability": 0.9, "nsfw_probability": 0.0}
        model.predict_proba.assert_called_with([text])
    loader.assert_called_once_with(spam_classifier.MODEL_PATH)


def test_missing_artifact_raises_instead_of_returning_empty_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, classifier_cache: None
) -> None:
    monkeypatch.setattr(spam_classifier, "MODEL_PATH", tmp_path / "missing.joblib")
    with pytest.raises(FileNotFoundError):
        spam_classifier.predict_message("buy now")


@pytest.mark.parametrize("valid_checksum", [True, False])
def test_bundle_verifies_artifact_before_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, valid_checksum: bool
) -> None:
    artifact = b"trusted artifact"
    response = MagicMock(content=artifact)
    download = MagicMock(return_value=response)
    destination = tmp_path / "models" / "classifier.joblib"
    monkeypatch.setattr(spam_classifier.requests, "get", download)
    monkeypatch.setattr(spam_classifier, "MODEL_PATH", destination)
    monkeypatch.setattr(spam_classifier, "MODEL_SHA256", sha256(artifact if valid_checksum else b"other").hexdigest())

    if valid_checksum:
        spam_classifier.bundle_model()
        assert destination.read_bytes() == artifact
    else:
        with pytest.raises(ValueError, match="checksum mismatch"):
            spam_classifier.bundle_model()
        assert not destination.exists()
    download.assert_called_once_with(spam_classifier.MODEL_URL, timeout=120)
    response.raise_for_status.assert_called_once()


def test_bundle_http_failure_does_not_write_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    response = MagicMock()
    response.raise_for_status.side_effect = requests.HTTPError("Not found")
    monkeypatch.setattr(spam_classifier.requests, "get", MagicMock(return_value=response))
    destination = tmp_path / "classifier.joblib"
    monkeypatch.setattr(spam_classifier, "MODEL_PATH", destination)
    with pytest.raises(requests.HTTPError):
        spam_classifier.bundle_model()
    assert not destination.exists()


@pytest.mark.skipif(not spam_classifier.MODEL_PATH.exists(), reason="Run the USSR model bundling command first")
@pytest.mark.filterwarnings("ignore::sklearn.exceptions.InconsistentVersionWarning")
def test_bundled_artifact_predicts_from_another_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, classifier_cache: None
) -> None:
    monkeypatch.chdir(tmp_path)
    result = spam_classifier.predict_message("free crypto at t.me/example")
    assert result["spam_probability"] == pytest.approx(0.6640663, abs=1e-6)
    assert result["nsfw_probability"] == 0.0
