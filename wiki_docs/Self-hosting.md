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
- `OPENROUTER_API_KEY`, `MISTRAL_API_KEY`, `OPENAI_API_KEY`: Seed the AI catalog on first
  migration, and are never read again. See below.

### 2. Run the Playbook

To deploy the stable environment:

```bash
ansible-playbook -i your_inventory deploy/stable.yml
```

To deploy the beta environment (includes scheduler and REST API):

```bash
ansible-playbook -i your_inventory deploy/beta.yml
```

## Greetings and welcome security

`greetings_ephemeral` sends the welcome only to the members it greets, one message each, filled
with their own name. Nothing is posted to the chat, so the clean-welcome cleanup has nothing to
delete afterwards.

`welcomecaptcha_ephemeral` sends the captcha prompt as an ephemeral message to each new member
instead of posting it in the chat. Only they see it, one prompt per member rather than one for the
batch, and nothing is left behind to clean up — so the prompt is not deleted when the captcha is
passed, because there is nothing there to delete.

A prompt whose security note is an album is still posted to the chat: `sendMediaGroup` cannot
address one member, and splitting the album into separate ephemeral messages is no way around it —
Telegram accepts at most five ephemeral messages per user.

## AI models and providers

Sophie's AI models, the endpoints they are served from and the API keys used to reach them live in
the database, not in the configuration file. They are managed at runtime with operator commands, so
adding a model or rotating a key never needs a redeploy.

### Seeding

The `seed_ai_catalog` migration creates the initial catalog from your environment:

- `OPENROUTER_API_KEY` becomes the `openrouter` provider.
- `MISTRAL_API_KEY` and `OPENAI_API_KEY` become the `mistral` and `openai` providers, of kind
  `moderation`. These carry no models: they hold the key for a service Sophie calls with the
  vendor's own SDK — the moderation classifiers, and Mistral's voice and video transcription.
- `CUSTOM_PROVIDERS` becomes one provider per entry, for OpenAI-compatible endpoints:

```
CUSTOM_PROVIDERS='[{"name":"qwencloud","base_url":"https://example.com/compatible-mode/v1","api_key":"sk-..."}]'
```

Every one of these variables is read **only** by a seed migration. Once the catalog exists, changing
them has no effect — use the commands below instead.

### Managing the catalog

| Command | Purpose |
| --- | --- |
| `/op_aiproviders` | List providers. API keys are always masked. |
| `/op_aiprovider <name> ^kind= ^base_url= ^key= ^enabled=` | Create or update a provider. Private chat only; the command message is deleted immediately. |
| `/op_aimodels` | List models and what each one is used for. |
| `/op_aimodel <name> ^provider= ^api_name= ^role= ^unrole= ^reasoning= ^enabled=` | Create or update a model. |

`kind` is `openrouter`, `openai_compatible`, or `moderation` (a key for a vendor SDK rather than a
chat-completions endpoint — do not point models at one). A role is `<mode>:<purpose>` — for example
`^role=support:chatbot` — or just `<purpose>` for the purposes that are not per-chat (`summary`,
`moderation_reason`). Purposes are `chatbot`, `translation`, `filters`, `summary` and
`moderation_reason`, `sophie_inspect`; modes are `entertainment`, `moderation`, `support`, `sophie_pm` and
`sophie_help`.

A mode with no model for a purpose falls back to the `support` tier, so you only need to define the
roles you want to differ. Changes take effect on every process within a few seconds without a
restart.

> **Warning:** with an empty catalog no AI feature can resolve a model and every AI request fails.
> Check `/op_aimodels` after deploying.
> {.is-warning}

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
/op_aiprovider openai ^key=sk-...
/op_ff ai_moderation_provider openai
```

> **Warning:** the `openrouter` provider's key cannot serve the OpenAI backend. OpenRouter proxies
> chat completions, not `/moderations`, so selecting `openai` without a real OpenAI key makes every
> moderation request fail — which silently leaves messages unmoderated. Check `/op_aiproviders`
> shows a key against `openai`.
> {.is-warning}

The two providers report different categories, and Sophie normalises them onto its own nine.
`health`, `financial`, `law` and `pii` have no OpenAI equivalent and never trigger on that backend.

### Per-chat detection levels

Chat admins run `/aimoderator` to get a table of the nine categories and a button for each one.
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

### Experimental: source inspection

`ai_sophie_inspect` lets the Sophie-help assistant start a sub-agent that reads Sophie's own source code
when the documentation cannot answer a question. It is **off by default** because it costs several
model requests per question.

Groups do not get it from their AI mode. `ai_sophie_inspect_chats` is a space or comma separated list of
group IDs allowed to use it anyway, for the chats where people ask how Sophie works.

Every run is bounded: `ai_sophie_inspect_request_limit`, `ai_sophie_inspect_tool_calls_limit` and
`ai_sophie_inspect_output_tokens_limit` cap one run, `ai_sophie_inspect_daily_chat_limit` caps how many runs
one chat may start per day, and the tokens are charged to that chat's AI quota like any other
feature.

The model it uses is the catalog model holding the `sophie_inspect` role, so it is swapped like
any other: `/op_aimodel <name> ^role=sophie_inspect`. Prefer a cheap one — the daily cap is what bounds the
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
