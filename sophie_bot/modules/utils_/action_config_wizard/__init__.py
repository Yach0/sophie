"""Shared aggregate Action Config Wizard primitives."""

from typing import Any

from .config import ActionWizardConfig, ActionWizardContext, ActionWizardDraft


def create_action_config_system(
    cfg: ActionWizardConfig,
) -> tuple[
    type[Any],
    type[Any],
    type[Any],
    type[Any],
    type[Any],
    type[Any],
]:
    """Import and create generated handlers lazily to keep module imports acyclic."""
    from .factory import create_action_config_system as create_system

    return create_system(cfg)


__all__ = [
    "ActionWizardConfig",
    "ActionWizardContext",
    "ActionWizardDraft",
    "create_action_config_system",
]
