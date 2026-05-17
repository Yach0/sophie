"""Utility for generating restriction reasons using AI.

This module provides functions to generate reasons for warnings, restrictions,
and federations using AI when no reason is provided by the user.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from sophie_bot.db.models import AIEnabledModel, ChatModel, RulesModel
from sophie_bot.db.models.notes import Saveable
from sophie_bot.modules.ai.utils.ai_models import MODERATION_REASON_MODEL
from sophie_bot.modules.ai.utils.new_ai_chatbot import new_ai_generate_schema
from sophie_bot.modules.ai.utils.new_message_history import NewAIMessageHistory
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.logger import log


class AIReasonResponse(BaseModel):
    """Schema for AI-generated restriction reason."""

    reason: str = Field(
        description="A concise reason for the restriction/warning (1-2 sentences)",
        min_length=10,
        max_length=200,
    )


async def should_generate_ai_reason(chat_db: ChatModel) -> bool:
    """Check if AI reason generation should be used.

    Args:
        chat_db: The chat database model

    Returns:
        True if AI reason generation should be used
    """
    if not await is_enabled("ai_moderation_reasons", chat_tid=chat_db.tid):
        return False

    # Check if AI is enabled for this chat
    if not await AIEnabledModel.get_state(chat_db.iid):
        return False

    return True


async def generate_restriction_reason(
    chat_db: ChatModel,
    message_text: Optional[str] = None,
    include_rules: bool = True,
) -> Optional[str]:
    """Generate a reason for a restriction using AI.

    Args:
        chat_db: The chat database model
        message_text: The text of the message being replied to (the violation)
        include_rules: Whether to include group rules in the prompt (False for federations)

    Returns:
        The generated reason string, or None if generation failed or no message text provided
    """
    if not await should_generate_ai_reason(chat_db):
        return None

    # Only generate reason if there's a message to analyze
    if not message_text:
        return None

    try:
        # Get group rules if needed
        rules_text = ""
        if include_rules:
            rules = await RulesModel.get_rules(chat_db.iid)
            if rules:
                # Extract rules text from Saveable model
                rules_content = extract_rules_text(rules)
                if rules_content:
                    rules_text = f"\n\nGroup Rules:\n{rules_content}"

        # Build the prompt
        prompt = build_reason_prompt(message_text=message_text, rules_text=rules_text)

        # Generate AI response
        history = NewAIMessageHistory()
        history.add_system(
            "You are a moderation assistant for a Telegram group management bot. "
            "Generate concise, professional reasons for user restrictions."
        )
        history.add_custom(prompt, "Moderator")

        result: AIReasonResponse = await new_ai_generate_schema(
            history, AIReasonResponse, MODERATION_REASON_MODEL(), user_tracking_id=chat_db.iid
        )

        # Clean up the reason
        reason = result.reason.strip()
        if reason:
            log.debug(
                "Generated AI reason for restriction",
                chat_id=chat_db.tid,
                reason=reason[:50],
            )
            return reason

        return None

    except Exception as err:
        log.warning(
            "Failed to generate AI reason for restriction",
            chat_id=chat_db.tid,
            error=str(err),
        )
        return None


def extract_rules_text(rules_model: RulesModel) -> str:
    """Extract text content from rules model.

    Args:
        rules_model: The rules database model

    Returns:
        The rules text content
    """
    # Rules are stored as Saveable objects
    # Try to get the text representation
    if hasattr(rules_model, "text") and rules_model.text:
        return str(rules_model.text)

    # If it's a Saveable with text field
    if isinstance(rules_model, Saveable):
        # Try to get text from the saveable content
        try:
            content = rules_model.model_dump()
            if "text" in content and content["text"]:
                return str(content["text"])
        except Exception:
            log.warning("Failed to extract text from Saveable rules model")

    # Fallback: convert entire model to string representation
    try:
        return str(rules_model)
    except Exception:
        log.warning("Failed to convert rules model to string, returning empty")
        return ""


def build_reason_prompt(message_text: str, rules_text: str) -> str:
    """Build the prompt for AI reason generation.

    Args:
        message_text: The text of the message that triggered the restriction
        rules_text: Group rules text (may be empty)

    Returns:
        The formatted prompt string
    """
    prompt_parts = [
        "Generate a brief, professional moderation reason for restricting a user based on their message.",
        "",
        "Message Content:",
        message_text,
        "",
        "Guidelines:",
        "- Analyze the message content above to identify the violation",
        "- The reason should be 1-2 sentences explaining what rule was broken",
        "- Be professional and neutral in tone",
        "- Do not reference content from the message",
        "- If group rules are provided, reference the specific rule violated",
        "- The reason should be suitable for moderation logs",
        "- Do not preface the reason with 'User was restricted for...' or similar language, write directly the reason",
        "- Output only the reason (e.g., 'Inappropriate language').",
    ]

    if rules_text:
        prompt_parts.append("")
        prompt_parts.append(rules_text)

    prompt_parts.append("")
    prompt_parts.append("Generate a concise reason based on the message content:")

    return "\n".join(prompt_parts)
