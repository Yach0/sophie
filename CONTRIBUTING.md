# Contributing to Sophie Telegram Bot

First off, thank you for considering contributing to Sophie! It's people like you who make Sophie such a great tool for everyone.

This document provides guidelines and instructions for contributing to this project. Please read it carefully before starting any work.

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [How Can I Contribute?](#how-can-i-contribute)
    - [Reporting Bugs](#reporting-bugs)
    - [Suggesting Enhancements](#suggesting-enhancements)
    - [Pull Requests](#pull-requests)
3. [Development Setup](#development-setup)
4. [Coding Standards](#coding-standards)
5. [Translation System](#translation-system)
6. [Testing](#testing)
7. [Submit Your Changes](#submit-your-changes)

---

## Code of Conduct

By participating in this project, you are expected to uphold our standards of respectful and professional communication.

---

## How Can I Contribute?

### Reporting Bugs

If you find a bug, please open an issue and include:
- A clear, descriptive title.
- Steps to reproduce the bug.
- The expected and actual behavior.
- Any relevant logs or screenshots.

### Suggesting Enhancements

We welcome suggestions for new features or improvements. Please open an issue to discuss your ideas first.

### Pull Requests

When you're ready to submit changes, please:
1. Fork the repository and create your branch from `main`.
2. Ensure your code follows the [Coding Standards](#coding-standards).
3. Update any relevant documentation.
4. Ensure all tests pass by running `make commit`.
5. Submit a Pull Request with a clear description of your changes.

---

## Development Setup

Please refer to the [README.md](README.md#1-installation) for detailed installation and setup instructions. We use `uv` as our package manager and `make` for common development tasks.

---

## Coding Standards

Sophie has strict coding standards to ensure code quality and consistency. For a detailed guide on our best practices, please read the [AI Development Guidelines (AGENTS.md)](AGENTS.md).

Key points:
- **PEP8 Compliance**: We use 120 character line length, `ruff` for formatting and import sorting, and `pycln` for removing unused imports.
- **Type Safety**: **ALWAYS** use type annotations for all function parameters and return values.
- **Functional Programming**: We prefer pure functions and async/await patterns.
- **No Global State**: Avoid global state modifications where possible.
- **Explicit Naming**: Use `chat_tid` for Telegram Chat IDs (int) and `chat_iid` for database IDs (ObjectId). **NEVER** confuse them.

---

## Translation System

Sophie uses a translation system based on Gettext and Crowdin. All user-facing text **MUST** be translatable using the i18n system. For details on how to use `gettext` and `lazy_gettext`, please see the [AGENTS.md](AGENTS.md#translation-system) section.

---

## Testing

We use `pytest` for unit testing and `aiogram-test-framework` for end-to-end testing.
- **New Features**: Must include new tests covering core logic and edge cases.
- **Bug Fixes**: Must include a reproduction test that failed before the fix and passes after.
- **Running Tests**: Use `make run_tests` or `make commit`.

---

## Submit Your Changes

Before submitting your Pull Request, **ALWAYS** run:

```bash
make commit
```

This command automatically:
- Fixes code style.
- Extracts translatable strings.
- Runs type checks (`ty`).
- Executes the full test suite.
- Generates updated documentation (Wiki and OpenAPI).

Only submit your PR if `make commit` passes successfully.

---

Thank you for contributing! 🚀
