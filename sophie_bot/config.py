from ipaddress import IPv4Network
from typing import Annotated, List, Literal, Optional

from aiogram.webhook.security import DEFAULT_TELEGRAM_NETWORKS
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    Field,
    FilePath,
    ValidationInfo,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class CustomProviderConfig(BaseModel):
    """An OpenAI-compatible AI provider configured at runtime via the CUSTOM_PROVIDERS env var.

    Models are referenced as ``<name>/<model>`` (e.g. ``qwencloud/qwen3-vl-flash``); the ``<name>/``
    prefix selects this provider and is stripped before the request reaches ``base_url``.
    """

    name: str
    base_url: str
    api_key: str


class Config(BaseSettings):
    token: str = "12345:ABCDEFG"

    username: str | None = None

    owner_id: int | None = None
    operators: List[int] = []

    mode: Literal["bot", "scheduler", "nostart", "rest"] = "bot"
    instance_name: str = "development"
    dev_reload: bool = False  # Enable hot-reload for development (watches file changes)

    mongo_host: str = "mongodb://localhost"
    mongo_port: int = 27017
    mongo_db: str = "sophie"
    mongo_allow_index_dropping: bool = False
    mongo_skip_indexes: bool = False
    mongo_use_replica_set: bool = False  # Set to True for transaction support

    # Migration configuration
    run_migrations_on_startup: bool = True
    migrations_path: str = "sophie_bot/db/migrations"
    migration_mode: Literal["auto", "manual"] = "auto"
    migration_use_transactions: bool = False  # Requires MongoDB replica set
    migration_batch_size: int = 1000  # For large collections
    migration_timeout_seconds: int = 3600  # 1 hour timeout per migration

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_username: Optional[str] = None
    redis_password: Optional[str] = None
    redis_db_fsm: int = 1
    redis_db_states: int = 2
    redis_db_schedule: int = 3

    botapi_server: Optional[AnyHttpUrl] = None

    # Debugging
    # off = no debug, normal = debug logging, high = debug + mongo logs + debug middlewares
    debug_mode: Literal["off", "normal", "high"] = "off"
    memory_debug: bool = False  # Memory leaks debugging

    modules_load: List[str] = ["*"]
    modules_not_load: List[str] = []
    legacy_modules_not_load: List[str] = []

    webhooks_enable: bool = False
    webhooks_listen: str = "127.0.0.1"
    webhooks_port: int = 8080
    webhooks_path: str = "/"
    webhooks_https_certificate: Optional[FilePath] = None
    webhooks_https_certificate_key: Optional[FilePath] = None
    webhooks_filter_ips: bool = False
    webhooks_allowed_networks: Annotated[List[IPv4Network], Field(validate_default=True)] = [IPv4Network("127.0.0.0/8")]
    webhooks_secret_token: Optional[str] = None
    webhooks_handle_in_background: bool = True

    # IPs of trusted reverse proxies; only these may set X-Real-IP / X-Forwarded-For
    trusted_proxies: List[str] = ["127.0.0.1"]

    api_listen: str = "127.0.0.1"
    api_port: int = 8075
    api_jwt_secret: str = "change_me_in_production"
    api_operator_token: Optional[str] = "test"
    api_jwt_expire_minutes: int = 720  # 12 hours
    api_cors_origins: List[str] = ["*"]

    # Kill switch for the MessageOrigin discriminator workaround (see utils/update_sanitizer.py).
    # Lives here rather than in feature_flags because it runs inside the synchronous JSON hook,
    # where the async Redis-backed flag API cannot be awaited.
    updates_sanitize_message_origin: bool = True

    commands_prefix: str = "/!"
    commands_ignore_case: bool = True
    commands_ignore_mention: bool = False
    commands_ignore_forwarded: bool = True
    commands_ignore_code: bool = True

    sentry_url: Optional[AnyHttpUrl] = None
    sentry_enable_logs: bool = True
    sentry_enable_metrics: bool = True
    sentry_traces_sample_rate: float | None = None
    sentry_profile_session_sample_rate: float | None = 0.2

    devs_managed_languages: List[str] = ["en_US"]
    # A list of languages that are managed by developers; Will disable
    # showing percent of it and won't suggest to help to translate it on crowdin.
    translation_url: str = "https://crowdin.com/project/sophiebot"
    support_link: str = "https://t.me/SophieSupport"
    news_channel: str = "https://t.me/SophieNEWS"
    wiki_link: str = "https://sophie-wiki.orangefox.tech/"
    wiki_modules_link: str = "https://sophie-wiki.orangefox.tech/modules/"
    privacy_link: str = "https://sophie-wiki.orangefox.tech/docs/Privacy%20policy"

    help_featured_module: str = "ai"

    default_locale: str = "en_US"

    # Deploy templates always set ENVIRONMENT explicitly (deploy/templates/*.env.j2), so this default is
    # only ever used by local checkouts and CI. It must not be "production", or the production safety
    # checks below would reject every unconfigured dev run.
    environment: str = "development"

    proxy_enable: bool = False
    proxy_stable_instance_url: str = "http://host.container.internal:8071"
    proxy_beta_instance_url: str = "http://host.container.internal:8072"

    # OpenRouter API key for routing non-Mistral models and note embeddings via OpenAI-compatible API
    openrouter_api_key: str | None = None
    tavily_api_key: str = ""
    kagi_api_key: str = ""
    mistral_api_key: str | None = None

    # Extra OpenAI-compatible AI providers, e.g.
    # CUSTOM_PROVIDERS='[{"name":"qwencloud","base_url":"https://dashscope-intl.aliyuncs.com/compatible-mode/v1","api_key":"sk-..."}]'
    custom_providers: List[CustomProviderConfig] = []

    gitlab_token: str | None = None
    gitlab_project_id: str | None = None  # GitLab project ID or URL-encoded path

    ai_autotrans_lowmem: bool = False
    ai_timeout_seconds: int = 120  # Timeout for AI handler execution (seconds)

    # Metrics configuration
    metrics_enable: bool = True
    metrics_sample_ratio: float = 1.0

    model_config = SettingsConfigDict(env_parse_none_str="None", env_file="data/config.env", env_file_encoding="utf-8")

    @computed_field
    @property
    def bot_id(self) -> int:
        return int(self.token.split(":")[0])

    @property
    def custom_providers_by_name(self) -> dict[str, CustomProviderConfig]:
        return {provider.name: provider for provider in self.custom_providers}

    @computed_field
    @property
    def security_log_file(self) -> str:
        return f"data/security.{self.instance_name}.{self.bot_id}.log.txt"

    # Production deploys set ENVIRONMENT to "production" (rest) or "production-<flavour>" (beta, stable,
    # scheduler). Matching the prefix rather than the exact string keeps a future "production-<something>"
    # guarded by default instead of silently unguarded. See deploy/templates/*.env.j2.
    @property
    def is_production(self) -> bool:
        return self.environment.startswith("production")

    # The REST API is the only thing that reads api_jwt_secret / api_operator_token / api_cors_origins,
    # and it only runs in "rest" mode (see sophie_bot/__main__.py).
    @property
    def serves_rest_api(self) -> bool:
        return self.mode == "rest"

    # Full runtime log file. Captures every log record so AI agents can trace
    # what happened during development. Truncated on every (re)start, including
    # dev hot-reloads, so it always reflects only the current run.
    runtime_log_file: str = "data/runtime.logs"

    @field_validator("redis_username")
    @classmethod
    def validate_redis_username(cls, v: str | None) -> str | None:
        if v and v.startswith('"') and v.endswith('"'):
            return v[1:-1]
        return v

    @field_validator("operators", mode="before")
    @classmethod
    def validate_operators(cls, v: List[int] | None, info: ValidationInfo) -> List[int]:
        owner_id = info.data.get("owner_id")

        if not v:
            return [owner_id] if owner_id else []

        if owner_id and owner_id not in v:
            v.append(owner_id)
        return v

    # Runs as a model validator rather than per-field validators: `environment` is declared after the
    # fields it guards, and a field_validator's ValidationInfo.data only holds fields validated before it.
    # Scoped to REST deploys because only they read — and provision — these settings; the bot, scheduler
    # and stable env files set no API_* vars, so enforcing there would fail their boot on defaults they
    # never use.
    @model_validator(mode="after")
    def validate_production_safety(self) -> "Config":
        if not (self.is_production and self.serves_rest_api):
            return self

        if self.api_jwt_secret == "change_me_in_production":
            raise ValueError("api_jwt_secret must be changed in production")

        if self.api_operator_token == "test":
            raise ValueError("api_operator_token must be changed in production")

        if "*" in self.api_cors_origins:
            raise ValueError("api_cors_origins must not contain '*' in production")

        return self

    @field_validator("webhooks_allowed_networks")
    @classmethod
    def add_telegram_networks(cls, v: List[IPv4Network]) -> List[IPv4Network]:
        v.extend(DEFAULT_TELEGRAM_NETWORKS)
        return v


CONFIG = Config()
