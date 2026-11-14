"""Compatibility entry point for the dedicated CI migration command.

Keep test implementations in test_migration_helper; conftest excludes this
entry point from directory collection so normal test runs execute them once.
"""

from tests.test_migration_helper import (
    cli_scenario,
    test_database_command_failures_close_every_acquired_client,
    test_database_commands_apply_and_report_real_migration_state,
    test_moderator_migration_normalizes_legacy_defaults_and_preserves_false,
    test_moderator_migration_repairs_duplicates_before_unique_index_and_rolls_back,
    test_moderator_rollback_after_first_category_configuration,
    test_unset_ai_moderator_null_enabled_migration,
)

__all__ = [
    "cli_scenario",
    "test_database_command_failures_close_every_acquired_client",
    "test_database_commands_apply_and_report_real_migration_state",
    "test_moderator_migration_normalizes_legacy_defaults_and_preserves_false",
    "test_moderator_migration_repairs_duplicates_before_unique_index_and_rolls_back",
    "test_moderator_rollback_after_first_category_configuration",
    "test_unset_ai_moderator_null_enabled_migration",
]
