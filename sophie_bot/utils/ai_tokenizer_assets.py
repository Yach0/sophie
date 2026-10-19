from __future__ import annotations

import base64
import json
from functools import cache
from pathlib import Path
from shutil import copyfile
from typing import TypedDict, cast

import tiktoken
from huggingface_hub import hf_hub_download
from tiktoken_ext.openai_public import ENCODING_CONSTRUCTORS

TOKENIZER_DIRECTORY = Path(__file__).resolve().parents[1] / "tokenizer_data"
GLM_TOKENIZER_REPOSITORY = "zai-org/GLM-5.3"
GLM_TOKENIZER_REVISION = "ba32f8c19c8738e2c57c71798c601d477a7bf844"
GLM_TOKENIZER_FILENAME = "glm-5.3.json"


class EncodingAssets(TypedDict):
    name: str
    pat_str: str
    mergeable_ranks: dict[str, int]
    special_tokens: dict[str, int]


@cache
def load_encoding(name: str) -> tiktoken.Encoding:
    # Construct directly from bundled data: get_encoding can fetch missing/corrupt cache entries.
    assets = cast(EncodingAssets, json.loads((TOKENIZER_DIRECTORY / f"{name}.json").read_text()))
    return tiktoken.Encoding(
        name=assets["name"],
        pat_str=assets["pat_str"],
        mergeable_ranks={base64.b64decode(token): rank for token, rank in assets["mergeable_ranks"].items()},
        special_tokens=assets["special_tokens"],
    )


def bundle_tokenizers() -> None:
    """Fetch vocabularies during image build; runtime readers only open bundled files."""
    TOKENIZER_DIRECTORY.mkdir(parents=True, exist_ok=True)
    glm_path = hf_hub_download(
        repo_id=GLM_TOKENIZER_REPOSITORY,
        filename="tokenizer.json",
        revision=GLM_TOKENIZER_REVISION,
    )
    copyfile(glm_path, TOKENIZER_DIRECTORY / GLM_TOKENIZER_FILENAME)
    for name, constructor in ENCODING_CONSTRUCTORS.items():
        # The extension API supplies Encoding constructor arguments, including its vocabulary.
        configuration = constructor()
        ranks = cast(dict[bytes, int], configuration["mergeable_ranks"])
        assets: EncodingAssets = {
            "name": configuration["name"],
            "pat_str": configuration["pat_str"],
            "mergeable_ranks": {base64.b64encode(token).decode("ascii"): rank for token, rank in ranks.items()},
            "special_tokens": configuration["special_tokens"],
        }
        (TOKENIZER_DIRECTORY / f"{name}.json").write_text(json.dumps(assets, separators=(",", ":")))
        print(f"Bundled {name}")
    print(f"Bundled {GLM_TOKENIZER_REPOSITORY}@{GLM_TOKENIZER_REVISION}")


if __name__ == "__main__":
    bundle_tokenizers()
