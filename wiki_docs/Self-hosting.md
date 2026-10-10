---
icon: 🚀
title: Self-hosting Sophie
---

This guide explains how to self-host Sophie Bot using Podman and Ansible, mirroring the official production environment.

## Architecture Overview

Sophie is designed to run as a set of microservices to ensure scalability and high availability:

- **Stable Instance**: The primary bot instance that handles most user interactions. It can also act as a proxy to the Beta instance.
- **Beta Instance**: A secondary instance used for testing new features.
- **Scheduler**: Handles background tasks, timed events, and scheduled operations.
- **REST API**: Provides an interface for the web dashboard and external integrations.

All components are containerized and typically run using **Podman**.

> **Note:** Prebuilt Docker containers are currently only available for x86_64 architecture. ARM-based systems will need
> to build the images locally.
> {.is-warning}

## Prerequisites

Before starting, ensure you have the following installed on your host system:

- **Podman**: For container management.
- **Ansible**: For automated deployment.
- **MongoDB**: Persistent data storage.
- **Redis / Valkey**: For caching and FSM (Finite State Machine) storage.

## Deployment with Ansible

The recommended way to deploy Sophie is using the provided Ansible playbooks in the `deploy/` directory.

### 1. Configuration

Copy `data/config.example.env` to `data/config.env` and fill in the required values. Key variables include:

- `TOKEN`: Your Telegram Bot API token.
- `MONGO_HOST`: Connection string for MongoDB.
- `REDIS_HOST`: Hostname for Redis.

### AI telemetry with Sentry

Set `SENTRY_URL` to a Sentry DSN to enable error reporting and performance traces. With
a DSN, Sophie traces every transaction by default (`SENTRY_TRACES_SAMPLE_RATE=1.0`);
lower this rate to reduce volume, or set it to `0` to disable traces while keeping
error reporting. Sampling is at the parent transaction level, so the same rate also
applies to non-AI requests. The Pydantic AI integration records agent/model/tool spans,
token usage, prompts, replies, and tool inputs/outputs in captured traces. These can
contain personal data and produce high Sentry volume; set an appropriate retention policy.
For Ansible deployments, set `SENTRY_TRACES_SAMPLE_RATE` in the operator environment to
override the default in all bot, scheduler, and REST processes.

Sophie also adds spans for its own AI caches and state transitions. AI request metrics
still use the existing `METRICS_ENABLE` and `SENTRY_ENABLE_METRICS` settings.


### 2. Run the Playbook

To deploy the stable environment:

```bash
ansible-playbook -i your_inventory deploy/stable.yml
```

To deploy the beta environment (includes scheduler and REST API):

```bash
ansible-playbook -i your_inventory deploy/beta.yml
```

## Telegram Mini App authentication

Mini App login at `/auth/login/tma` validates Telegram init data with the bot token using
`init-data-py` v1. Init data has an explicit one-day lifetime; older credentials are rejected,
so clients must reopen the Mini App to obtain fresh init data rather than reuse it indefinitely.
Malformed init data (including missing required fields) or validated data without a user returns
HTTP 400. Invalid hashes and expired credentials return HTTP 401. A valid Telegram user not
registered in Sophie's database still receives HTTP 403. The successful token response is unchanged.

## Greetings and welcome security

Welcome messages go only to the members they greet, one message per member with their
own name. Nothing is posted to the chat, so clean-welcome cleanup has nothing to delete.

Captcha prompts also go to each new member as ephemeral messages, rather than into the
chat. Only new members see them, one prompt each; nothing is left to delete after
the captcha is passed.

If a member leaves before Telegram accepts their ephemeral welcome or captcha prompt,
`send_saveable` returns `None` and logs `outcome=skipped`, `reason=recipient_unavailable`,
the chat ID and the recipient ID. This applies to Rich messages, traditional text and
single-media saveables. A missing reply target is retried without the reply, but always
with the same ephemeral recipient; a departed recipient is never retried publicly.
Only the exact `USER_NOT_PARTICIPANT` failure on an ephemeral send is treated this way:
unrelated failures and this error on public sends are still surfaced.

A prompt whose security note is an album is still posted to the chat: `sendMediaGroup` cannot
address one member, and splitting the album into separate ephemeral messages is no way around it —
Telegram accepts at most five ephemeral messages per user.

## AI models and providers

Sophie's AI models, the endpoints they are served from and the API keys used to reach them live in
the database, not in the configuration file. They are managed at runtime with operator commands, so
adding a model or rotating a key never needs a redeploy.

### Seeding

The `seed_ai_catalog` migration creates catalog models and the `openrouter` provider; the
`seed_vendor_sdk_provider_keys` migration creates `mistral` and `openai` providers for
moderation and transcription. All three start without keys. Configure credentials in the
database before enabling AI, using `/op_ai_provider <name> ^key=<your-key>` in a private chat
with the bot or updating the provider through the operator API.

The initial catalog also includes a `qwencloud` model. To use it, create an OpenAI-compatible
provider named `qwencloud` with its endpoint and key via `/op_ai_provider` or the operator API.
No AI provider credentials are read from environment variables.

### Managing the catalog

| Command | Purpose |
| --- | --- |
| `/op_ai_providers` | List providers. API keys are always masked. |
| `/op_ai_provider <name> ^kind= ^base_url= ^key= ^enabled=` | Create or update a provider. Private chat only; the command message is deleted immediately. |
| `/op_ai_models` | List models and what each one is used for. |
| `/op_ai_model <name> ^provider= ^api_name= ^role= ^unrole= ^reasoning= ^context_window_tokens= ^enabled=` | Create or update a model. |

`kind` is `openrouter`, `openai_compatible`, or `moderation` (a key for a vendor SDK rather than a
chat-completions endpoint — do not point models at one). A role is `<mode>:<purpose>` — for example
`^role=support:chatbot` — or just `<purpose>` for the purposes that are not per-chat (`summary`,
`moderation_reason`). Purposes are `chatbot`, `translation`, `filters`, `summary` and
`moderation_reason`, `sophie_inspect`; modes are `entertainment`, `moderation`, `support`, `sophie_pm` and
`sophie_help`.

A mode with no model for a purpose falls back to the `support` tier, so you only need to define the
roles you want to differ. Changes take effect on every process within a few seconds without a
restart.

`context_window_tokens` is the model's authoritative positive integer context capacity.
The `add_ai_model_context_sizes` migration fetches the public OpenRouter models API once,
matching exact `api_name` first and exact catalog `name` second. Existing configured
capacities are preserved; unmatched models stay undefined and are reported. Set those
manually with `/op_ai_model <name> ^context_window_tokens=<tokens>` or the operator API.
Network or invalid-response errors stop the migration before it changes any capacities.
Rollback removes only unchanged migration-added values and preserves later operator edits.

Modern context requires a configured capacity for every possible chatbot candidate,
including the last-resort model. Missing capacities raise an error; no provider-profile
or model-name estimate is used. Legacy context can still use models with undefined capacities.


> **Warning:** AI requests require a configured catalog model and a key on its provider. Check
> `/op_ai_providers` and `/op_ai_models` after deploying; environment keys do not configure OpenRouter.
> {.is-warning}

### AI progress

Chatbot replies show an in-progress message while they stream. Manual translation
(`/tr`, `/translate`) and `/research` also show progress. All three use STFU Rich
rendering with a fixed animated AI emoji and a three-emoji footer; translation
and research edit the same Rich message for the result.
Automatic translation stays silent until its result is ready. The chatbot and manual
translation initially show a random working message after the animated AI emoji.
When reasoning or an activity begins, that space stays empty until answer text streams
in; progress appears below.
Streamed reasoning appears once as an italic “Reasoning...” activity below the
header, without revealing reasoning text. Later reasoning passes, including
those after tool calls, do not add another activity. Every tool invocation and
retry stays in the bottom activity list, including repeated calls and tools
hidden from the final header. New streamed answer text clears the activity list.
Later tool calls appear below the answer text already shown; the next text
update clears those entries in turn.

Manual `/tr` and `/translate` retain their current activity list until the final
translation replaces the progress message. A replied voice starts with “Transcribing
voice message...” before transcription, followed by “Translating...”. Replied images
and videos show processing stages (including video audio transcription) before
translation. Chatbot replies stack the same media stages during context preparation.

Video transcription reads the first audio stream even when the container lists a
video stream before it.

Completed replies replace the animation with a static AI emoji, eligible used-tool titles
without tool icons (Search first when used), and a battery footer in its own paragraph.
Each tool has its own `display_in_ai_header` setting in
`sophie_bot/modules/ai/utils/ai_tool.py`; set it to `False` to hide that tool from the
final header without hiding its in-progress activity. Custom emoji IDs remain in the
tool metadata but are not rendered in the progress activity or completed header.
The low, middle, and high battery icons correspond to 0–32%, 33–65%, and 66–100%.
`ai_chatbot_show_model_name` adds the model beside the battery reading.
Only the assistant's answer is stored in conversation history; the displayed header,
tool titles, and battery footer are not.
Legacy context replays recent tool calls and results, with each stored result capped by
`ai_chatbot_tool_history_max_chars`. Proactive replies use the same inline custom-emoji
header and bottom battery footer. Header styles are no longer configurable.

### Modern AI context

Modern history is opt-in. Run the model-context-size migration and configure every
unmatched model, including last-resort candidates, before enabling it:

```text
/op_ff ^chat=<chat_id> ai_chatbot_modern_context true
/op_ff ^chat=<chat_id> ai_chatbot_modern_context_tokens 16384
```

`ModernContext` keeps one serialized session per chat, topic and AI mode. It appends
new chat messages and native assistant/tool exchanges once, rather than rebuilding
the complete recent-message block on every turn. When the budget is reached, it
deletes old background batches first, then whole completed turns. Tool calls and
returns stay paired. There is no generated summary or compaction message; an oversized
current request fails instead of being truncated. Text budgeting uses a conservative
UTF-8 byte bound and images reserve 4096 estimated tokens each, not an exact tokenizer.
Unlimited response-limit settings remain supported; admission reserves 2048 tokens
for output plus the context safety margin.

Stable instructions precede append-only history. Date-only runtime snapshots,
escaped XML chat notes and 1-based XML memory items are appended when runtime data
changes. The latest runtime snapshot supersedes earlier snapshots. Current/replied
speakers and memory references can appear in the entertainment roster; old private
alias maps do not grow the roster forever.

Speakers and message references use session-local `uN` and `mN` labels. Typed builders
select these labels instead of real handles or Telegram IDs for attribution fields.
Entertainment alone includes literal first-name attribution. Its outgoing first-name
mentions resolve to usernames only when the session identity lookup is unambiguous;
known ambiguous names remain plain. Other modern modes keep alias-only attribution.
Authored messages, notes, summaries, configuration prompts and arbitrary native tool
data are not scanned or redacted. Only XML syntax is escaped. Native provider message
parts and signatures replay unchanged; there is no generic sanitization processor.
Memory facts use identity anchors, stored as internal links and re-aliased for a new
session, so first-name jokes do not attach to a different person after reset.

Supported providers receive caching options: Anthropic-compatible OpenRouter models
use explicit cache markers, native Anthropic uses its cache controls, and native
OpenAI uses an opaque session cache key. Generic compatible endpoints receive no
unsupported OpenAI cache fields. Actual cache reads/writes are recorded from provider
usage; cache hits remain provider-dependent.

`/ai_reset` clears the shared message cache and native sessions in one epoch-fenced
transaction. Pre-reset incoming messages and modern deliveries cannot repopulate it
after their handler or generation finishes. Tool history and memory are also cleared.
Switching the flag off uses `OldContext`; it does not reinterpret the modern session.
After a deployment that retires old display headers, reset affected histories if
previously decorated cache entries remain.


## AI moderation

The AI moderator classifies messages against nine categories and deletes anything that crosses a
threshold. It does **not** go through the AI catalog above — it calls a dedicated moderation
classifier, chosen with the `ai_moderation_provider` feature flag:

| Value | Model | Catalog provider holding the key |
| --- | --- | --- |
| `mistral` (default) | `mistral-moderation-latest` | `mistral` |
| `openai` | `omni-moderation-latest` | `openai` |

Switching backend is two steps: put the key in the catalog, then flip the flag. Neither needs a
restart — the client is rebuilt as soon as the catalog version changes.

```
/op_ai_provider openai ^key=sk-...
/op_ff ai_moderation_provider openai
```

> **Warning:** the `openrouter` provider's key cannot serve the OpenAI backend. OpenRouter proxies
> chat completions, not `/moderations`, so selecting `openai` without a real OpenAI key makes every
> moderation request fail — which silently leaves messages unmoderated. Check `/op_ai_providers`
> shows a key against `openai`.
> {.is-warning}

The two providers report different categories, and Sophie normalises them onto its own nine.
`health`, `financial`, `law` and `pii` have no OpenAI equivalent and never trigger on that backend.

### Per-chat detection levels

Chat admins run `/ai_moderator` to get a table of the nine categories and a button for each one.
Pressing a button walks that category through Off → Low → Medium → High.

The level multiplies the classifier's score before it is compared to the threshold, so a chat can be
made more or less sensitive without an operator retuning anything. The factors are feature flags:

| Level | Flag | Default |
| --- | --- | --- |
| Low | `ai_moderation_level_low_multiplier` | `0.7` |
| Medium | `ai_moderation_level_normal_multiplier` | `1.0` |
| High | `ai_moderation_level_high_multiplier` | `1.3` |

Off skips the category entirely rather than scaling it to zero.

### Tuning thresholds

Every threshold is a feature flag, so it can be changed per chat with no redeploy:

```
/op_ff ai_moderation_provider openai
/op_ff ai_moderation_threshold_openai_sexual_minors 0.1
/op_ff ai_moderation_threshold_mistral_sexual unset
```

Flags are named `ai_moderation_threshold_<provider>_<the provider's own category>`, because
categories that Sophie groups together do not score alike: `sexual/minors` needs a much lower
cut-off than `sexual`. These are the operator-level tuning; per-chat sensitivity is the detection
level above.

> **Note:** thresholds are floats, so write `1.0` rather than `1` — a bare `1` parses as `true`.
> {.is-info}

The "message deleted" notice removes itself after `ai_moderation_notice_delete_after_seconds`
(30 by default); set it to `0` to keep the notices in the chat.

### Source inspection

The Sophie-help assistant can start a sub-agent that reads Sophie's own source code
when the documentation cannot answer a question. It costs several model requests
per question, so usage is limited per chat.

Groups do not get it from their AI mode. `ai_sophie_inspect_chats` is a space or comma separated list of
group IDs allowed to use it anyway, for the chats where people ask how Sophie works.

Every run is bounded: `ai_sophie_inspect_request_limit`, `ai_sophie_inspect_tool_calls_limit` and
`ai_sophie_inspect_output_tokens_limit` cap one run, `ai_sophie_inspect_daily_chat_limit` caps how many runs
one chat may start per day, and the tokens are charged to that chat's AI quota like any other
feature.

The model it uses is the catalog model holding the `sophie_inspect` role, so it is swapped like
any other: `/op_ai_model <name> ^role=sophie_inspect`. Prefer a cheap one — the daily cap is what bounds the
damage, not the price per run.

The sub-agent can only read `.py` files inside the `sophie_bot` package, and only through search and
bounded reads — it never executes anything and never sees configuration or data.

## Running with Podman (Manual)

If you prefer to run containers manually, you can use the following logic (based on the Quadlet templates):

### Stable Instance

```bash
podman run -d \
  --name sophie-stable \
  --env-file /var/sophie/stable.env \
  -p 8071:8071 \
  registry.gitlab.com/sophiebot/sophie:main-runtime
```

### Scheduler

The scheduler is the same image but runs with `MODE=scheduler`.

```bash
podman run -d \
  --name sophie-scheduler \
  -e MODE=scheduler \
  --env-file /var/sophie/scheduler.env \
  registry.gitlab.com/sophiebot/sophie:main-runtime
```

### REST API

The REST API is the same image but runs with `MODE=rest`.

```bash
podman run -d \
  --name sophie-rest \
  -e MODE=rest \
  --env-file /var/sophie/rest.env \
  -p 8075:8075 \
  registry.gitlab.com/sophiebot/sophie:main-runtime
```

## Proxy System (Stable + Beta)

Sophie implements a unique proxying system where the Stable instance can redirect traffic to the Beta instance. This allows seamless transitions and testing of new features.

- `PROXY_ENABLE`: Set to `True` to enable proxying.
- `PROXY_STABLE_INSTANCE_URL`: URL of the stable instance.
- `PROXY_BETA_INSTANCE_URL`: URL of the beta instance.

When enabled, the bot can route requests between instances based on configuration, allowing for "canary" style deployments or easy beta testing for specific users/chats.

## Environment Variables Reference

| Variable | Description |
| :--- | :--- |
| `TOKEN` | Telegram Bot Token |
| `MONGO_DB` | MongoDB Database Name |
| `REDIS_DB_FSM` | Redis Database index for FSM |
| `OWNER_ID` | Telegram User ID of the bot owner |
| `ENVIRONMENT` | Name of the environment (e.g., `production-stable`) |
| `MODE` | Set to `scheduler` for the scheduler service |

---
> For advanced configuration, refer to the `deploy/templates/` directory in the repository.
> {.is-info}
