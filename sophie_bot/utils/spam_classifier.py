from __future__ import annotations

from functools import cache
from hashlib import file_digest, sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Protocol, TypedDict, cast

import httpx2
import joblib
import numpy as np
from numpy.typing import NDArray
from ussr.precision_assets import precision_assets
from ussr.precision_spam import PrecisionClassifier

MODEL_DIRECTORY = Path(__file__).resolve().parents[1] / "spam_model_data"
MODEL_PATH = MODEL_DIRECTORY / "ussr_text_classifier.joblib"
MODEL_REVISION = "3b1737f220efc11ce915ed0e894747088697c0e3"
MODEL_SHA256 = "28c602aa8b24943261656cb06403003870e91a405cc1d0f3c939ec5e69aefd6d"
MODEL_URL = f"https://gitlab.com/SophieBot/ussr/-/raw/{MODEL_REVISION}/models/ussr_text_classifier.joblib"
PRECISION_REVISION = "02617181afce0cb7aab8b8642ad3b81c35acd8db"
PRECISION_DIRECTORY = MODEL_DIRECTORY / PRECISION_REVISION
_PRECISION_LOAD_LOCK = Lock()


class TextClassifier(Protocol):
    classes_: NDArray[np.int64]

    def predict_proba(self, texts: list[str]) -> NDArray[np.float64]: ...


class SpamPrediction(TypedDict):
    spam_probability: float
    nsfw_probability: float


@cache
def load_classifier() -> TextClassifier:
    return cast(TextClassifier, joblib.load(MODEL_PATH))


@cache
def load_precision_classifier() -> PrecisionClassifier:
    return PrecisionClassifier(PRECISION_DIRECTORY)


def predict_message(text: str, links: tuple[str, ...] = (), *, precision: bool = True) -> SpamPrediction:
    if precision:
        with _PRECISION_LOAD_LOCK:
            classifier = load_precision_classifier()
        probabilities = classifier.predict_proba([text], [links])
        spam_column = 1
    else:
        legacy_classifier = load_classifier()
        probabilities = legacy_classifier.predict_proba([text])
        spam_column = list(legacy_classifier.classes_).index(1)
    return {
        "spam_probability": float(probabilities[0, spam_column]),
        "nsfw_probability": 0.0,
    }


def download_asset(url: str, destination: Path, checksum: str) -> None:
    """Verify each pinned file before making it available to the model."""
    if destination.is_file():
        with destination.open("rb") as artifact:
            if file_digest(artifact, "sha256").hexdigest() == checksum:
                return

    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as scratch:
        temporary = Path(scratch) / destination.name
        digest = sha256()
        with httpx2.stream("GET", url, timeout=120, follow_redirects=True) as response:
            response.raise_for_status()
            with temporary.open("wb") as artifact:
                for chunk in response.iter_bytes():
                    digest.update(chunk)
                    artifact.write(chunk)
        if digest.hexdigest() != checksum:
            raise ValueError(f"USSR model checksum mismatch for {destination.name}")
        temporary.replace(destination)


def bundle_model() -> None:
    """Provision the precision model and its rollback model during the build."""
    download_asset(MODEL_URL, MODEL_PATH, MODEL_SHA256)
    for asset in precision_assets(PRECISION_REVISION):
        download_asset(asset.url, PRECISION_DIRECTORY / asset.path, asset.sha256)
    print(f"Bundled USSR precision model @{PRECISION_REVISION}")


if __name__ == "__main__":
    bundle_model()
    print(predict_message("USSR build smoke check"))
