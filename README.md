# 🤖 Sophie Telegram Bot

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Sophie is a modern, fast, and feature-rich Telegram chat manager bot built with [aiogram 3](https://github.com/aiogram/aiogram). It's designed to be modular, scalable, and easy to extend.

## ✨ Features

- **🛡️ Advanced Moderation:** Ban, mute, warn, and report systems.
- **🌍 Internationalization:** Multi-language support via Gettext and Crowdin.
- **⚙️ Microservices Architecture:** Separate modes for Bot, REST API, and Scheduler.
- **🗄️ Database:** Uses MongoDB with Beanie ODM for persistence.
- **🚀 High Performance:** Powered by `uv`, `ujson`, and `redis`.
- **🛠️ In-house Libraries:**
    - [ASS](https://gitlab.com/SophieBot/ass) — Argument Searcher of Sophie.
    - [STFU](https://gitlab.com/SophieBot/stf) — Sophie Text Formatting Utility.

## 📋 Requirements

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** (modern package manager)
- **MongoDB** (data storage)
- **Redis / Valkey** (caching and FSM)

### OS-specific Dependencies (e.g., openSUSE)

To build some dependencies (like `pyicu`), you might need:

**openSUSE:**
`libicu-devel`, `gcc-c++`, `python313-devel`, `gcc13`, `pkg-config`

**Debian/Ubuntu:**
`libicu-dev`, `pkg-config`, `g++`

**Fedora:**
`libicu-devel`, `pkgconf-pkg-config`, `gcc-c++`

## 🚀 Quick Start

### 1. Installation

Clone the repository and install dependencies using `uv`:

```bash
git clone https://gitlab.com/SophieBot/sophie.git
cd sophie
uv sync
```

### 2. Configuration

Copy the example environment file and edit it:

```bash
cp data/config.example.env data/config.env
# Edit data/config.env with your credentials (TOKEN, USERNAME, etc.)
```

### 3. Running Sophie

Sophie can be started in different modes depending on your needs.

#### 🤖 Bot Mode (Main)
```bash
# Standard
uv run python -m sophie_bot
# Development with hot-reload
make dev_bot
```

#### 🌐 REST API Mode
```bash
# Standard
MODE=rest uv run python -m sophie_bot
# Development with hot-reload
make dev_rest
```

#### 📅 Scheduler Mode
```bash
# Standard
MODE=scheduler uv run python -m sophie_bot
# Development with hot-reload
make dev_scheduler
```

## 🛠️ Development

We use `make` for common development tasks. Please read our **[CONTRIBUTING.md](CONTRIBUTING.md)** for detailed guidelines.

- **Check everything:** `make commit` (runs formatters, linters, tests, and generates docs)
- **Format code:** `make fix_code_style`
- **Local ASS/STFU development:** `make dev-libs`
- **Run tests:** `make run_tests`
- **Type checking:** `make test_codeanalysis`
- **Translations:** `make locale`

### 🧩 Local ASS/STFU development

Sophie uses the in-house [`ass-tg`](https://gitlab.com/SophieBot/ass) and [`stfu-tg`](https://gitlab.com/SophieBot/stf) libraries. By default, `uv sync` installs them from the Git sources configured in `pyproject.toml`.

When you need to work on Sophie together with local ASS/STFU changes, run:

```bash
make dev-libs
```

This target:

- clones or updates ASS into `libs/ass`;
- clones or updates STFU into `libs/stf`;
- installs both packages in editable mode;
- configures Makefile-driven commands to import `ass_tg` and `stfu_tg` from `libs/` before site-packages.

After that, commands started through the Makefile, such as `make dev_bot`, `make dev_rest`, `make extract_lang`, and `make commit`, use your local `libs/ass` and `libs/stf` checkouts.

To verify which copies are being imported:

```bash
uv run python -c "import ass_tg, stfu_tg; print(ass_tg.__path__[0]); print(stfu_tg.__path__[0])"
```

The paths should point to `libs/ass/ass_tg` and `libs/stf/stfu_tg` when local development is active.

To stop using local library checkouts and return to the configured Git sources:

```bash
make dev-libs-clean
```

`libs/` is ignored by Git, so local library checkouts are not pushed with Sophie.

### 🌲 Git Worktrees

This project supports [git worktrees](https://git-scm.com/docs/git-worktree) for parallel development. A `post-checkout` hook automatically syncs dependencies when creating new worktrees.

**Setup (one-time):**
```bash
python tools/setup_hooks.py install
```

**Creating a worktree:**
```bash
# Create a new worktree for feature branch
git worktree add ../sophie-feature -b feature/my-feature

# The hook automatically:
# - Links libs/ directory from main repo
# - Syncs dependencies (uv sync)
# - Compiles locale files
# - Links data/ directory
# - Installs package in editable mode
```

**Working in a worktree:**
```bash
cd ../sophie-feature
make commit  # All development commands work normally
```

**Manual setup (if hook didn't run):**
```bash
cd ../sophie-feature
make dev_branch  # or make setup_worktree
```

**Removing a worktree:**
```bash
git worktree remove ../sophie-feature
git branch -D feature/my-feature
```

## Federation progress replies

A failed final progress-message edit does not undo applied federation bans or prevent federation logging. If the progress message was deleted, Sophie does not send a replacement. Other Telegram edit errors are logged while the completed task retains its result.

### Runtime debugger

Run `make dev` with a dedicated `data/debug.env`, or set `DEBUG_CONFIG` to another development configuration file. The collector and browser UI bind to loopback and keep captured data in memory. Use development credentials and databases, not production targets.

Worker reload closes the previous process and its streams, including failed or cancelled startup. Each new run resumes live following; you can pause again after any reload.

Redis scans display the returned continuation cursor. Use **Load next bounded page** to continue without changing the query. Redis `COUNT` is a hint, so a page can contain more rows than the requested limit. Complete cursor batches are retained; oversized replies fail explicitly instead of silently skipping rows.

AI cache pagination counts both valid and malformed stored messages. Inspecting malformed rows does not change the cache.

The event feed retains at most 1,000 summaries. Pausing keeps a bounded snapshot while live capture continues; resuming shows the latest buffer. Time filters accept RFC3339 timestamps with `Z` or numeric offsets and compare instants.

Repeating a Mongo or Redis inspector submission reads current data again. Mongo update/delete actions require a literal `_id` (including canonical Extended JSON IDs); query predicates and array IDs are rejected. Large Mongo rows may have truncated contents while every page row and its continuation metadata are preserved.

Known credentials are redacted before binary payloads are base64 encoded. Shutdown waits for any active worker reload and prevents further restarts.

## 📖 Documentation

- **Wiki:** [https://sophie-wiki.orangefox.tech/](https://sophie-wiki.orangefox.tech/)
- **Self-hosting Guide:** See [wiki_docs/Self-hosting.md](wiki_docs/Self-hosting.md)
- **AGENTS.md:** Even if you're a human, it's still very helpful to read this file to understand the project structure, guidelines, best practices and the boilerplates.

## ⚖️ License

Sophie is licensed under the **GNU AGPLv3**. See [LICENSE](LICENSE) for more details.
