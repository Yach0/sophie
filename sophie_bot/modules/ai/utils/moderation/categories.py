from __future__ import annotations

from enum import StrEnum

from sophie_bot.utils.i18n import lazy_gettext as l_


class ModerationCategory(StrEnum):
    """Sophie's normalised moderation categories.

    The values double as ``AIModeratorModel`` field names and REST schema keys, so they must not
    change without a database migration.
    """

    SEXUAL = "sexual"
    HATE_AND_DISCRIMINATION = "hate_and_discrimination"
    VIOLENCE_AND_THREATS = "violence_and_threats"
    DANGEROUS_AND_CRIMINAL_CONTENT = "dangerous_and_criminal_content"
    SELFHARM = "selfharm"
    HEALTH = "health"
    FINANCIAL = "financial"
    LAW = "law"
    PII = "pii"


MODERATION_CATEGORIES_TITLES = {
    ModerationCategory.SEXUAL: l_("🔞 Sexual"),
    ModerationCategory.HATE_AND_DISCRIMINATION: l_("💢 Hate"),
    ModerationCategory.VIOLENCE_AND_THREATS: l_("⚔ Violence"),
    ModerationCategory.DANGEROUS_AND_CRIMINAL_CONTENT: l_("☠ Dangerous"),
    ModerationCategory.SELFHARM: l_("💔 Self-harm"),
    ModerationCategory.HEALTH: l_("🩺 Health"),
    ModerationCategory.FINANCIAL: l_("💰 Financial"),
    ModerationCategory.LAW: l_("⚖ Law"),
    ModerationCategory.PII: l_("🪪 Personal data"),
}

MODERATION_CATEGORIES_TRANSLATES = {
    ModerationCategory.SEXUAL: l_(
        "Content meant to arouse sexual excitement, such as the description of sexual activity, or that promotes sexual services (excluding sex education and wellness)."
    ),
    ModerationCategory.HATE_AND_DISCRIMINATION: l_(
        "Content that expresses prejudice, hostility, or advocates discrimination against individuals or groups based on protected characteristics such as race, ethnicity, religion, gender, sexual orientation, or disability."
    ),
    ModerationCategory.VIOLENCE_AND_THREATS: l_(
        "Content that describes, glorifies, incites, or threatens physical violence against individuals or groups."
    ),
    ModerationCategory.DANGEROUS_AND_CRIMINAL_CONTENT: l_(
        "Content that promotes or provides instructions for illegal activities or extremely hazardous behaviors that pose a significant risk of physical harm, death, or legal consequences."
    ),
    ModerationCategory.SELFHARM: l_(
        "Content that promotes, instructs, plans, or encourages deliberate self-injury, suicide, eating disorders, or other self-destructive behaviors."
    ),
    ModerationCategory.HEALTH: l_("Content that contains or tries to elicit detailed or tailored medical advice."),
    ModerationCategory.FINANCIAL: l_("Content that contains or tries to elicit detailed or tailored financial advice."),
    ModerationCategory.LAW: l_("Content that contains or tries to elicit detailed or tailored legal advice."),
    ModerationCategory.PII: l_(
        "Content that requests, shares, or attempts to elicit personal identifying information such as full names, addresses, phone numbers, social security numbers, or financial account details."
    ),
}
