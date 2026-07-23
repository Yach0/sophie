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
- `OPENROUTER_API_KEY`: Seeds the AI catalog on first migration. See below.

### 2. Run the Playbook

To deploy the stable environment:

```bash
ansible-playbook -i your_inventory deploy/stable.yml
```

To deploy the beta environment (includes scheduler and REST API):

```bash
ansible-playbook -i your_inventory deploy/beta.yml
```

## AI models and providers

Sophie's AI models, the endpoints they are served from and the API keys used to reach them live in
the database, not in the configuration file. They are managed at runtime with operator commands, so
adding a model or rotating a key never needs a redeploy.

### Seeding

The `seed_ai_catalog` migration creates the initial catalog from your environment:

- `OPENROUTER_API_KEY` becomes the `openrouter` provider.
- `CUSTOM_PROVIDERS` becomes one provider per entry, for OpenAI-compatible endpoints:

```
CUSTOM_PROVIDERS='[{"name":"qwencloud","base_url":"https://example.com/compatible-mode/v1","api_key":"sk-..."}]'
```

Both variables are read **only** by this migration. Once the catalog exists, changing them has no
effect — use the commands below instead.

### Managing the catalog

| Command | Purpose |
| --- | --- |
| `/op_aiproviders` | List providers. API keys are always masked. |
| `/op_aiprovider <name> ^kind= ^base_url= ^key= ^enabled=` | Create or update a provider. Private chat only; the command message is deleted immediately. |
| `/op_aimodels` | List models and what each one is used for. |
| `/op_aimodel <name> ^provider= ^api_name= ^role= ^unrole= ^reasoning= ^enabled=` | Create or update a model. |

`kind` is `openrouter` or `openai_compatible`. A role is `<mode>:<purpose>` — for example
`^role=support:chatbot` — or just `<purpose>` for the purposes that are not per-chat (`summary`,
`moderation_reason`). Purposes are `chatbot`, `translation`, `filters`, `summary` and
`moderation_reason`; modes are `entertainment`, `moderation`, `support`, `sophie_pm` and
`sophie_help`.

A mode with no model for a purpose falls back to the `support` tier, so you only need to define the
roles you want to differ. Changes take effect on every process within a few seconds without a
restart.

> **Warning:** with an empty catalog no AI feature can resolve a model and every AI request fails.
> Check `/op_aimodels` after deploying.
> {.is-warning}

### Experimental: source inspection

`ai_deep_help` lets the Sophie-help assistant start a sub-agent that reads Sophie's own source code
when the documentation cannot answer a question. It is **off by default** because it costs several
model requests per question.

Every run is bounded: `ai_deep_help_request_limit`, `ai_deep_help_tool_calls_limit` and
`ai_deep_help_output_tokens_limit` cap one run, `ai_deep_help_daily_chat_limit` caps how many runs
one chat may start per day, and the tokens are charged to that chat's AI quota like any other
feature.

The model it uses is the catalog model holding the `deep_help` role, so it is swapped like any
other: `/op_aimodel <name> ^role=deep_help`. Prefer a cheap one — the daily cap is what bounds the
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
  registry.gitlab.com/sophiebot/sophie:stable-runtime
```

### Scheduler

The scheduler is the same image but runs with `MODE=scheduler`.

```bash
podman run -d \
  --name sophie-scheduler \
  -e MODE=scheduler \
  --env-file /var/sophie/scheduler.env \
  registry.gitlab.com/sophiebot/sophie:stable-runtime
```

### REST API

The REST API is the same image but runs with `MODE=rest`.

```bash
podman run -d \
  --name sophie-rest \
  -e MODE=rest \
  --env-file /var/sophie/rest.env \
  -p 8075:8075 \
  registry.gitlab.com/sophiebot/sophie:stable-runtime
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
