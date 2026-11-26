from __future__ import annotations

from redis.asyncio import Redis

from sophie_bot.db.models.ai.ai_catalog import AICatalogModelModel, AICatalogProviderModel, AIModelRole
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_catalog import bump_version, load_catalog


async def select_native_models(provider: str, *, redis: Redis, mode: AIMode = AIMode.support) -> None:
    """Configure real catalog roles, leaving SDK calls to each test's offline transport."""
    for name in ("mistral", "openai"):
        if not await AICatalogProviderModel.find_one(AICatalogProviderModel.name == name):
            await AICatalogProviderModel(name=name, kind="moderation", api_key="offline").save()
    upstream_names = {
        "mistral": {"transcription": "voxtral-mini-latest", "moderation": "mistral-moderation-latest"},
        "openai": {"transcription": "gpt-transcribe", "moderation": "omni-moderation-latest"},
    }
    for purpose, api_name in upstream_names[provider].items():
        role_mode = AIMode.support if purpose == "transcription" else mode
        async for model in AICatalogModelModel.find_all():
            model.roles = [role for role in model.roles if (role.mode, role.purpose.value) != (role_mode, purpose)]
            await model.save()
        model_name = f"{provider}/{api_name}"
        model = await AICatalogModelModel.find_one(AICatalogModelModel.name == model_name)
        if model is None:
            model = AICatalogModelModel(name=model_name, provider=provider, api_name=api_name)
        model.roles.append(AIModelRole(mode=role_mode, purpose=purpose))
        await model.save()
    await bump_version(redis=redis)
    await load_catalog(redis=redis)
