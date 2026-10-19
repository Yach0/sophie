from __future__ import annotations

import base64
import json
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from sophie_bot.modules.ai.utils import ai_tokens
from sophie_bot.utils import ai_tokenizer_assets


@pytest.fixture
def offline_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    def reject_network(*arguments: object, **keywords: object) -> None:
        pytest.fail("Tokenizer loading attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_network)
    monkeypatch.setattr(ai_tokens, "TOKENIZER_DIRECTORY", tmp_path)
    monkeypatch.setattr(ai_tokenizer_assets, "TOKENIZER_DIRECTORY", tmp_path)
    ai_tokens._load_token_counter.cache_clear()
    ai_tokenizer_assets.load_encoding.cache_clear()
    yield tmp_path
    ai_tokens._load_token_counter.cache_clear()
    ai_tokenizer_assets.load_encoding.cache_clear()


@pytest.mark.parametrize("glm_model", ["z-ai/glm-5.3", "z-ai/glm-5.3-flash", "openai/z-ai/glm-5.3-flash:free"])
async def test_bundled_tokenizers_load_without_network_or_external_caches(
    offline_assets: Path,
    glm_model: str,
) -> None:
    encoding: ai_tokenizer_assets.EncodingAssets = {
        "name": "o200k_base",
        "pat_str": r"[\s\S]",
        "mergeable_ranks": {
            base64.b64encode(bytes([value])).decode("ascii"): value for value in range(256)
        },
        "special_tokens": {},
    }
    (offline_assets / "o200k_base.json").write_text(json.dumps(encoding))
    tokenizer = Tokenizer(WordLevel({"hello": 0, "[UNK]": 1}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    tokenizer.enable_truncation(max_length=1)
    tokenizer.enable_padding(length=10)
    tokenizer.save(str(offline_assets / ai_tokenizer_assets.GLM_TOKENIZER_FILENAME))

    luna = await ai_tokens.load_token_counter("openai/gpt-5.6-luna")
    glm = await ai_tokens.load_token_counter(glm_model)
    assert luna.count_text("hello") == 5
    assert glm.count_text("hello hello") == 2


@pytest.mark.parametrize("model_name", ["gpt-5.6-luna", "glm-5.3", "z-ai/glm-5.3-flash"])
async def test_missing_known_tokenizer_fails_without_downloading(offline_assets: Path, model_name: str) -> None:
    with pytest.raises(FileNotFoundError):
        await ai_tokens.load_token_counter(model_name)


async def test_corrupt_encoding_fails_without_redownloading(offline_assets: Path) -> None:
    (offline_assets / "o200k_base.json").write_text("incomplete asset")
    with pytest.raises(json.JSONDecodeError):
        await ai_tokens.load_token_counter("gpt-5.6-luna")


async def test_unknown_model_needs_no_bundled_assets(offline_assets: Path) -> None:
    counter = await ai_tokens.load_token_counter("unknown-model")
    assert counter.count_text("你好") == 6


def test_glm_asset_is_bundled_from_pinned_revision(
    offline_assets: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = offline_assets / "source.json"
    source.write_text("pinned tokenizer")

    def download(*, repo_id: str, filename: str, revision: str) -> str:
        assert repo_id == "zai-org/GLM-5.3"
        assert filename == "tokenizer.json"
        assert revision == "ba32f8c19c8738e2c57c71798c601d477a7bf844"
        return str(source)

    monkeypatch.setattr(ai_tokenizer_assets, "hf_hub_download", download)
    monkeypatch.setattr(ai_tokenizer_assets, "ENCODING_CONSTRUCTORS", {})
    ai_tokenizer_assets.bundle_tokenizers()
    assert (offline_assets / ai_tokenizer_assets.GLM_TOKENIZER_FILENAME).read_text() == source.read_text()
