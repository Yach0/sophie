from __future__ import annotations

from functools import cache
from hashlib import file_digest, sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import TypedDict

import httpx2
from ussr.precision_assets import precision_assets
from ussr.precision_spam import PrecisionClassifier

MODEL_REVISION = "02617181afce0cb7aab8b8642ad3b81c35acd8db"
MODEL_DIRECTORY = Path(__file__).resolve().parents[1] / "spam_model_data" / MODEL_REVISION
_MODEL_LOAD_LOCK = Lock()


class SpamPrediction(TypedDict):
    spam_probability: float
    nsfw_probability: float


@cache
def load_classifier() -> PrecisionClassifier:
    return PrecisionClassifier(MODEL_DIRECTORY)


def predict_message(text: str, links: tuple[str, ...] = ()) -> SpamPrediction:
    with _MODEL_LOAD_LOCK:
        classifier = load_classifier()
    probabilities = classifier.predict_proba([text], [links])
    return {
        "spam_probability": float(probabilities[0, 1]),
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
    """Provision the pinned precision model during the build."""
    for asset in precision_assets(MODEL_REVISION):
        download_asset(asset.url, MODEL_DIRECTORY / asset.path, asset.sha256)
    print(f"Bundled USSR precision model @{MODEL_REVISION}")


if __name__ == "__main__":
    bundle_model()
    print(predict_message("USSR build smoke check"))
