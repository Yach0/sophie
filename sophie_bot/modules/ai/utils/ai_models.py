from enum import Enum
from typing import Mapping, Type

from httpx import AsyncClient
from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import OpenRouterModel

from sophie_bot.modules.ai.utils.ai_providers import AI_PROVIDERS, AIProviders

ai_http_client = AsyncClient(timeout=30)


class GoogleModels(Enum):
    gemini_2_5_pro = "google/gemini-2.5-pro"
    gemini_2_5_flash = "google/gemini-2.5-flash"
    gemini_3_flash_preview = "google/gemini-3-flash-preview"


class AnthropicModels(Enum):
    sonnet_4_5 = "anthropic/claude-sonnet-4.5"
    haiku_4_5 = "anthropic/claude-haiku-4.5"
    haiku_3_5 = "anthropic/claude-3.5-haiku"


class MistralModels(Enum):
    mistral_large = "mistralai/mistral-large"
    mistral_medium = "mistralai/mistral-medium"
    mistral_small = "mistralai/mistral-small"
    magistral_small = "mistralai/magistral-small-2506"
    magistral_medium = "mistralai/magistral-medium-2506"
    codestral = "mistralai/codestral-2508"
    pixtral = "mistralai/pixtral-12b"
    mistral_small_3_2_24b_instruct = "mistralai/mistral-small-3.2-24b-instruct"


class OpenAIModels(Enum):
    gpt_4o_mini = "openai/gpt-4o-mini"
    gpt_5 = "openai/gpt-5"
    gpt_5_mini = "openai/gpt-5-mini"
    gpt_5_nano = "openai/gpt-5-nano"
    gpt_5_1 = "openai/gpt-5.1"
    gpt_5_2_chat = "openai/gpt-5.2-chat"
    gpt_oss_120b = "openai/gpt-oss-120b"
    gpt_oss_20b = "openai/gpt-oss-20b"
    gpt_5_4_mini = "openai/gpt-5.4-mini"
    gpt_5_4_nano = "openai/gpt-5.4-nano"


class ZaiModels(Enum):
    glm_4_7 = "z-ai/glm-4.7"
    glm_4_6v = "z-ai/glm-4.6v"
    glm_4_5_air = "z-ai/glm-4.5-air"


AI_PROVIDER_TO_NAME = {
    AIProviders.auto.name: "Auto",
    AIProviders.anthropic.name: "Anthropic Claude",
    AIProviders.google.name: "Google Gemini",
    AIProviders.mistral.name: "Mistral AI",
    AIProviders.openai.name: "OpenAI ChatGPT",
    AIProviders.zai.name: "Z.AI",
}

AVAILABLE_PROVIDER_NAMES: tuple[str, ...] = (
    AIProviders.auto.name,
    AIProviders.anthropic.name,
    AIProviders.openai.name,
    AIProviders.google.name,
    AIProviders.mistral.name,
    AIProviders.zai.name,
)

PROVIDER_TO_MODELS: Mapping[str, Type[Enum]] = {
    "anthropic": AnthropicModels,
    "google": GoogleModels,
    "mistral": MistralModels,
    "openai": OpenAIModels,
    "zai": ZaiModels,
}

AI_MODEL_TO_PROVIDER = {
    AnthropicModels.sonnet_4_5.name: "anthropic",
    AnthropicModels.haiku_4_5.name: "anthropic",
    AnthropicModels.haiku_3_5.name: "anthropic",
    GoogleModels.gemini_2_5_flash.name: "google",
    GoogleModels.gemini_2_5_pro.name: "google",
    GoogleModels.gemini_3_flash_preview.name: "google",
    MistralModels.mistral_large.name: "mistral",
    MistralModels.mistral_medium.name: "mistral",
    MistralModels.mistral_small.name: "mistral",
    MistralModels.codestral.name: "mistral",
    MistralModels.pixtral.name: "mistral",
    MistralModels.magistral_small.name: "mistral",
    MistralModels.magistral_medium.name: "mistral",
    MistralModels.mistral_small_3_2_24b_instruct.name: "mistral",
    OpenAIModels.gpt_4o_mini.name: "openai",
    OpenAIModels.gpt_5.name: "openai",
    OpenAIModels.gpt_5_mini.name: "openai",
    OpenAIModels.gpt_5_nano.name: "openai",
    OpenAIModels.gpt_5_1.name: "openai",
    OpenAIModels.gpt_5_2_chat.name: "openai",
    OpenAIModels.gpt_oss_120b.name: "openai",
    OpenAIModels.gpt_oss_20b.name: "openai",
    OpenAIModels.gpt_5_4_mini.name: "openai",
    OpenAIModels.gpt_5_4_nano.name: "openai",
    ZaiModels.glm_4_7.name: "zai",
    ZaiModels.glm_4_6v.name: "zai",
    ZaiModels.glm_4_5_air.name: "zai",
}

AI_MODEL_TO_SHORT_NAME = {
    AnthropicModels.sonnet_4_5.value: "Claude Sonnet 4.5",
    AnthropicModels.haiku_4_5.value: "Claude Haiku 4.5",
    AnthropicModels.haiku_3_5.value: "Claude Haiku 3.5",
    GoogleModels.gemini_2_5_flash.value: "Gemini 2.5 Flash",
    GoogleModels.gemini_2_5_pro.value: "Gemini 2.5 Pro",
    GoogleModels.gemini_3_flash_preview.value: "Gemini 3 Flash Preview",
    MistralModels.mistral_large.value: "Mistral Large",
    MistralModels.mistral_medium.value: "Mistral Medium",
    MistralModels.mistral_small.value: "Mistral Small",
    MistralModels.codestral.value: "Codestral",
    MistralModels.pixtral.value: "Pixtral 12B",
    MistralModels.magistral_small.value: "Magistral Small",
    MistralModels.magistral_medium.value: "Magistral Medium",
    MistralModels.mistral_small_3_2_24b_instruct.value: "Mistral Small 3.2 24B Instruct",
    OpenAIModels.gpt_4o_mini.value: "GPT-4o mini",
    OpenAIModels.gpt_5.value: "GPT-5",
    OpenAIModels.gpt_5_mini.value: "GPT-5 mini",
    OpenAIModels.gpt_5_nano.value: "GPT-5 nano",
    OpenAIModels.gpt_5_1.value: "GPT-5.1",
    OpenAIModels.gpt_5_2_chat.value: "GPT-5.2 Chat",
    OpenAIModels.gpt_oss_120b.value: "GPT-OSS 120B",
    OpenAIModels.gpt_oss_20b.value: "GPT-OSS 20B",
    OpenAIModels.gpt_5_4_mini.value: "GPT-5.4 mini",
    OpenAIModels.gpt_5_4_nano.value: "GPT-5.4 nano",
    ZaiModels.glm_4_7.value: "GLM-4.7",
    ZaiModels.glm_4_6v.value: "GLM-4.6V",
    ZaiModels.glm_4_5_air.value: "GLM-4.5 Air",
}

DEFAULT_MODELS: dict[str, str] = {
    AIProviders.auto.name: MistralModels.mistral_medium.name,
    AIProviders.anthropic.name: AnthropicModels.haiku_4_5.name,
    AIProviders.google.name: GoogleModels.gemini_3_flash_preview.name,
    AIProviders.mistral.name: MistralModels.mistral_medium.name,
    AIProviders.openai.name: OpenAIModels.gpt_5_4_mini.name,
    AIProviders.zai.name: ZaiModels.glm_4_6v.name,
}

TRANSLATE_DEFAULT_MODELS: dict[str, str] = {
    AIProviders.auto.name: MistralModels.mistral_medium.name,
    AIProviders.anthropic.name: AnthropicModels.haiku_4_5.name,
    AIProviders.google.name: GoogleModels.gemini_2_5_flash.name,
    AIProviders.mistral.name: MistralModels.mistral_medium.name,
    AIProviders.openai.name: OpenAIModels.gpt_5_4_mini.name,
    AIProviders.zai.name: ZaiModels.glm_4_6v.name,
}

AI_PROVIDER_TO_MODEL_CLASS = {
    AIProviders.anthropic.name: OpenRouterModel,
    AIProviders.google.name: OpenRouterModel,
    AIProviders.mistral.name: OpenRouterModel,
    AIProviders.openai.name: OpenRouterModel,
    AIProviders.zai.name: OpenRouterModel,
}


# Lazy model initialization
_ai_models = None
_filter_handler_model = None


def _get_ai_models():
    global _ai_models
    if _ai_models is None:

        def build_models(provider: str, model: str) -> Model:
            model_enum = PROVIDER_TO_MODELS[provider]
            model_value = model_enum[model].value
            ModelClass = AI_PROVIDER_TO_MODEL_CLASS[provider]
            provider_factory = AI_PROVIDERS[provider]
            provider_instance = provider_factory()
            return ModelClass(model_value, provider=provider_instance)

        _ai_models = {
            model_name: build_models(provider, model_name) for model_name, provider in AI_MODEL_TO_PROVIDER.items()
        }
    return _ai_models


def _get_filter_handler_model():
    global _filter_handler_model
    if _filter_handler_model is None:
        _filter_handler_model = _get_ai_models()[OpenAIModels.gpt_5_4_nano.name]
    return _filter_handler_model


# Backwards compatibility: AI_MODELS is now a property-like dict
class _LazyAIModels(dict):
    def __getitem__(self, key):
        return _get_ai_models()[key]

    def __contains__(self, key):
        return key in _get_ai_models()

    def keys(self):
        return _get_ai_models().keys()

    def values(self):
        return _get_ai_models().values()

    def items(self):
        return _get_ai_models().items()

    def get(self, key, default=None):
        return _get_ai_models().get(key, default)


class _LazyFilterHandlerModel:
    def __get__(self, obj, objtype=None):
        return _get_filter_handler_model()

    def __call__(self):
        return _get_filter_handler_model()


class _LazyModerationReasonModel:
    def __get__(self, obj, objtype=None):
        return _get_ai_models()[MistralModels.mistral_small.name]

    def __call__(self):
        return _get_ai_models()[MistralModels.mistral_small.name]


AI_MODELS = _LazyAIModels()
FILTER_HANDLER_MODEL = _LazyFilterHandlerModel()
MODERATION_REASON_MODEL = _LazyModerationReasonModel()
