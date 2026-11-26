from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from shlex import split
from subprocess import run
from unittest.mock import MagicMock, call

import httpx2
import numpy as np
import pytest

from sophie_bot.utils import spam_classifier

TEST_URL = "https://example.test/artifact"


def test_source_checkout_prepares_model() -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = run(
        ["make", "--dry-run", "spam_model", "ASS_PATH="],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    commands = [split(line) for line in result.stdout.splitlines()]
    preparation = ["uv", "run", "python", "sophie_bot/utils/spam_classifier.py"]
    assert preparation in commands


@pytest.fixture
def classifier_cache() -> Iterator[None]:
    spam_classifier.load_classifier.cache_clear()
    yield
    spam_classifier.load_classifier.cache_clear()


def test_precision_classifier_loads_once_and_receives_embedded_destinations(
    monkeypatch: pytest.MonkeyPatch, classifier_cache: None
) -> None:
    model = MagicMock()
    model.predict_proba.return_value = np.array([[0.9, 0.1]])
    loader = MagicMock(return_value=model)
    monkeypatch.setattr(spam_classifier, "PrecisionClassifier", loader)

    text = "Read the guide"
    links = ("https://example.test/guide",)
    assert spam_classifier.predict_message(text, links) == {"spam_probability": 0.1, "nsfw_probability": 0.0}
    assert spam_classifier.predict_message(text) == {"spam_probability": 0.1, "nsfw_probability": 0.0}
    model.predict_proba.assert_has_calls([call([text], [links]), call([text], [()])])
    loader.assert_called_once_with(spam_classifier.MODEL_DIRECTORY)


def test_bundle_downloads_the_precision_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    download = MagicMock()
    monkeypatch.setattr(spam_classifier, "download_asset", download)
    monkeypatch.setattr(spam_classifier, "MODEL_DIRECTORY", tmp_path)

    spam_classifier.bundle_model()

    assert download.call_args_list == [
        call(asset.url, tmp_path / asset.path, asset.sha256)
        for asset in spam_classifier.precision_assets(spam_classifier.MODEL_REVISION)
    ]


@pytest.mark.parametrize("valid_checksum", [True, False])
def test_download_verifies_artifact_before_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, valid_checksum: bool
) -> None:
    artifact = b"trusted artifact"
    request = httpx2.Request("GET", TEST_URL)
    response = httpx2.Response(200, request=request, content=artifact)
    download = MagicMock()
    download.return_value.__enter__.return_value = response
    monkeypatch.setattr(spam_classifier.httpx2, "stream", download)
    destination = tmp_path / "models" / "classifier.joblib"
    checksum = sha256(artifact if valid_checksum else b"other").hexdigest()

    if valid_checksum:
        spam_classifier.download_asset(TEST_URL, destination, checksum)
        assert destination.read_bytes() == artifact
        spam_classifier.download_asset(TEST_URL, destination, checksum)
    else:
        with pytest.raises(ValueError, match="checksum mismatch"):
            spam_classifier.download_asset(TEST_URL, destination, checksum)
        assert not destination.exists()
    download.assert_called_once_with("GET", TEST_URL, timeout=120, follow_redirects=True)


def test_download_http_failure_keeps_existing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    response = httpx2.Response(404, request=httpx2.Request("GET", TEST_URL))
    download = MagicMock()
    download.return_value.__enter__.return_value = response
    monkeypatch.setattr(spam_classifier.httpx2, "stream", download)
    destination = tmp_path / "classifier.joblib"
    destination.write_bytes(b"old artifact")
    with pytest.raises(httpx2.HTTPStatusError):
        spam_classifier.download_asset(TEST_URL, destination, sha256(b"new artifact").hexdigest())
    assert destination.read_bytes() == b"old artifact"


@pytest.mark.skipif(not spam_classifier.MODEL_DIRECTORY.is_dir(), reason="Run the USSR model bundling command first")
def test_bundled_precision_scores_recovery_and_spam(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    recovery = spam_classifier.predict_message(
        "OrangeFox boots but /data does not mount after the update. Where can I attach the log?",
        ("https://wiki.orangefox.tech/guides/base",),
    )
    spam = spam_classifier.predict_message("Send an advance fee to collect your guaranteed cash prize now.")
    assert recovery["spam_probability"] < 0.5
    assert spam["spam_probability"] > 0.5
