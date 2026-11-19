from __future__ import annotations

from functools import cache
from hashlib import sha256
from pathlib import Path
from typing import Protocol, TypedDict, cast

import joblib
import numpy as np
import requests
from numpy.typing import NDArray

MODEL_PATH = Path(__file__).resolve().parents[1] / "spam_model_data" / "ussr_text_classifier.joblib"
MODEL_REVISION = "3b1737f220efc11ce915ed0e894747088697c0e3"
MODEL_SHA256 = "28c602aa8b24943261656cb06403003870e91a405cc1d0f3c939ec5e69aefd6d"
MODEL_URL = f"https://gitlab.com/SophieBot/ussr/-/raw/{MODEL_REVISION}/models/ussr_text_classifier.joblib"


class TextClassifier(Protocol):
    classes_: NDArray[np.int64]

    def predict_proba(self, texts: list[str]) -> NDArray[np.float64]: ...


class SpamPrediction(TypedDict):
    spam_probability: float
    nsfw_probability: float


@cache
def load_classifier() -> TextClassifier:
    """Load the trusted, build-time bundled USSR artifact once per process."""
    return cast(TextClassifier, joblib.load(MODEL_PATH))


def predict_message(text: str) -> SpamPrediction:
    # The pinned artifact is a binary LogisticRegression pipeline, whereas USSR's
    # public predictor expects a MultiOutputClassifier and crashes on this model.
    classifier = load_classifier()
    spam_column = list(classifier.classes_).index(1)
    probabilities = classifier.predict_proba([text])
    return {
        "spam_probability": float(probabilities[0, spam_column]),
        # This artifact has no NSFW classifier; do not reinterpret ham as NSFW.
        "nsfw_probability": 0.0,
    }


def bundle_model() -> None:
    """Fetch and verify the pinned model during build, never during message handling."""
    response = requests.get(MODEL_URL, timeout=120)
    response.raise_for_status()
    artifact = response.content
    if sha256(artifact).hexdigest() != MODEL_SHA256:
        raise ValueError("USSR model checksum mismatch")
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_bytes(artifact)
    print(f"Bundled USSR text model @{MODEL_REVISION}")


if __name__ == "__main__":
    bundle_model()
    print(predict_message("USSR build smoke check"))
