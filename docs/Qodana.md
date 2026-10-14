# Qodana configuration

The recommended profile remains enabled. `qodana.yaml` excludes selected
inspection/file pairs, not entire inspection IDs across the repository.

## Scope and maintenance

Qodana's `exclude` entries accept `name` and project-root-relative `paths`.
Omitting `paths` disables an inspection everywhere. Each entry here has a
nonempty list of exact files, with no directory or wildcard exclusions.

These are **file-level**, not occurrence-level, suppressions: new findings of
the same inspection in a listed file will also be hidden. Other inspections in
those files, and these inspections in every other file, remain enabled by the
profile. Re-audit exclusions when changing these files; remove a path when the
false positive disappears. Do not expand to a directory or global exclusion to
silence a new finding.

Configuration references checked for this change:

- [JetBrains Qodana CLI YAML types](https://github.com/JetBrains/qodana-cli/blob/main/internal/platform/qdyaml/yaml.go)
  (`QodanaYaml.Excludes` and `Clude.Paths`).
- [Qodana 1.0 JSON schema](https://raw.githubusercontent.com/SchemaStore/schemastore/master/src/schemas/json/qodana-1.0.json)
  (`definitions.exclude`, including the documented global behavior when paths
  are omitted). The JetBrains help link referenced by that schema returned 404
  during verification; the CLI source and schema were available.

## Exclusions and Rationales

| Inspection ID | Excluded file | Target symbols | Rationale |
| --- | --- | --- | --- |
| `PyAbstractClassInspection` | `sophie_bot/modules/utils_/status_handler.py` | `StatusHandlerABC`, `StatusBoolHandlerABC`, `StatusIntHandlerABC` | Shared `ABC` handlers leave abstract status operations and framework hooks to concrete subclasses. |
| `PyAbstractClassInspection` | `sophie_bot/modules/federations/handlers/base.py` | `FederationCommandHandler` | `ABCMeta` base declares the abstract federation command hook. |
| `PyAbstractClassInspection` | `sophie_bot/modules/federations/handlers/promote_demote_base.py` | `FederationPromoteDemoteHandler` | Shared promote/demote flow leaves `_execute_action` abstract. |
| `PyAbstractClassInspection` | `sophie_bot/modules/locks/handlers/base.py` | `BaseLockToggleHandler` | Shared lock flow leaves `_toggle_lock` abstract and command metadata to subclasses. |
| `PyAbstractClassInspection` | `sophie_bot/modules/restrictions/handlers/base.py` | `BaseRestrictionHandler` | Shared restriction base leaves framework hooks and class-level settings to concrete handlers. |
| `PyAbstractClassInspection` | `sophie_bot/modules/ai/handlers/feature_setting.py` | `AIFeatureSetting` | Shared status implementation leaves filters and feature metadata to concrete AI setting handlers. |
| `PyPropertyDefinitionInspection` | `sophie_bot/modules/ai/utils/ai_usage_service.py` | `AIUsageLike.total_tokens`, `AIModelLike.model_name` | Read-only protocol properties with stub bodies model computed third-party attributes. |
| `PyCallingNonCallableInspection` | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/parse_arg.py` | `ButtonsArg.parse` | `self.child` inherits `ArgFabric`, whose `__call__` is callable at runtime. |
