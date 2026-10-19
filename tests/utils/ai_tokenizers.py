from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from sophie_bot.modules.ai.utils import ai_tokens
from sophie_bot.utils import ai_tokenizer_assets


@pytest.fixture(autouse=True)
def bundled_test_tokenizers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Small offline vocabularies exercise the real loaders without build-time assets."""
    ranks = {bytes([value]): value for value in range(256)}
    for word in ("你好", "world", "hello", "runtime", "style", "Give", "complete"):
        encoded = word.encode()
        for size in range(2, len(encoded) + 1):
            token = encoded[:size]
            if token not in ranks:
                ranks[token] = len(ranks)
    encoding = {
        "name": "test-openai",
        "pat_str": r"\w+|[^\w]",
        "mergeable_ranks": {base64.b64encode(token).decode(): rank for token, rank in ranks.items()},
        "special_tokens": {},
    }
    for name in ("cl100k_base", "o200k_base"):
        (tmp_path / f"{name}.json").write_text(json.dumps(encoding))
    tokenizer = Tokenizer(WordLevel({"[UNK]": 0, "Hello": 1, "world": 2}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    tokenizer.save(str(tmp_path / ai_tokenizer_assets.GLM_TOKENIZER_FILENAME))
    monkeypatch.setattr(ai_tokens, "TOKENIZER_DIRECTORY", tmp_path)
    monkeypatch.setattr(ai_tokenizer_assets, "TOKENIZER_DIRECTORY", tmp_path)
    counter_loader = ai_tokens._load_token_counter
    counter_loader.cache_clear()
    ai_tokenizer_assets.load_encoding.cache_clear()
    yield
    counter_loader.cache_clear()
    ai_tokenizer_assets.load_encoding.cache_clear()
