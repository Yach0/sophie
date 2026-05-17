from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Final

from beanie import PydanticObjectId
from openai import AsyncOpenAI

from sophie_bot.config import CONFIG
from sophie_bot.db.models import NoteModel
from sophie_bot.utils.logger import log

EMBEDDING_MODEL: Final[str] = "openai/text-embedding-3-small"
MAX_SEARCH_RESULTS: Final[int] = 10
MAX_EMBEDDING_TEXT_LENGTH: Final[int] = 8000


def build_note_embedding_text(note: NoteModel) -> str:
    names = ", ".join(note.names)
    parts = [f"Names: {names}"]
    if note.description:
        parts.append(f"Title: {note.description}")
    if note.text:
        parts.append(f"Content: {note.text}")
    return "\n".join(parts)[:MAX_EMBEDDING_TEXT_LENGTH]


def _embedding_client() -> AsyncOpenAI | None:
    if not CONFIG.openrouter_api_key:
        return None
    return AsyncOpenAI(api_key=CONFIG.openrouter_api_key, base_url="https://openrouter.ai/api/v1")


async def create_embedding(text: str) -> list[float] | None:
    client = _embedding_client()
    if client is None:
        log.debug("notes_rag: no OpenRouter API key configured, skipping embeddings")
        return None
    response = await client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    return list(response.data[0].embedding)


def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    left_values = list(left)
    right_values = list(right)
    if not left_values or not right_values or len(left_values) != len(right_values):
        return 0.0
    dot_product = sum(
        left_value * right_value for left_value, right_value in zip(left_values, right_values, strict=True)
    )
    left_norm = math.sqrt(sum(left_value * left_value for left_value in left_values))
    right_norm = math.sqrt(sum(right_value * right_value for right_value in right_values))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot_product / (left_norm * right_norm)


async def update_note_embedding(note: NoteModel) -> bool:
    embedding_text = build_note_embedding_text(note)
    if not embedding_text.strip():
        return False
    if note.embedding and note.embedding_text == embedding_text and note.embedding_model == EMBEDDING_MODEL:
        return False
    embedding = await create_embedding(embedding_text)
    if embedding is None:
        return False
    note.embedding = embedding
    note.embedding_text = embedding_text
    note.embedding_model = EMBEDDING_MODEL
    await note.save()
    return True


async def semantic_search_notes(
    chat_iid: PydanticObjectId, query: str, limit: int = MAX_SEARCH_RESULTS
) -> list[NoteModel]:
    query_embedding = await create_embedding(query)
    if query_embedding is None:
        return await NoteModel.search_chat_notes(chat_iid, query)

    notes = await NoteModel.get_chat_notes(chat_iid)
    embedded_notes = [note for note in notes if note.embedding]
    if not embedded_notes:
        return await NoteModel.search_chat_notes(chat_iid, query)

    ranked_notes = sorted(
        ((cosine_similarity(query_embedding, note.embedding or []), note) for note in embedded_notes),
        key=lambda score_and_note: score_and_note[0],
        reverse=True,
    )
    semantic_matches = [note for score, note in ranked_notes[:limit] if score > 0]
    if not semantic_matches:
        return await NoteModel.search_chat_notes(chat_iid, query)
    return semantic_matches
