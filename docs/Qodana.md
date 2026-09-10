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

## MR640: abstract-handler and protocol-property audit

The original audit reported 1,649 SARIF results, including the following eight
abstract-class and two property findings. These are historical audit counts,
not a verified result count for this configuration. No fresh Qodana scan or
recount of the original SARIF was performed while narrowing these exclusions.

| Inspection ID | Audited file | Audited symbols (findings) | Rationale |
| --- | --- | --- | --- |
| `PyAbstractClassInspection` | `sophie_bot/modules/utils_/status_handler.py` | `StatusHandlerABC`, `StatusBoolHandlerABC`, `StatusIntHandlerABC` (3) | Shared `ABC` handlers leave abstract status operations and framework hooks to concrete subclasses. |
| `PyAbstractClassInspection` | `sophie_bot/modules/federations/handlers/base.py` | `FederationCommandHandler` (1) | `ABCMeta` base declares the abstract federation command hook. |
| `PyAbstractClassInspection` | `sophie_bot/modules/federations/handlers/promote_demote_base.py` | `FederationPromoteDemoteHandler` (1) | Shared promote/demote flow leaves `_execute_action` abstract. |
| `PyAbstractClassInspection` | `sophie_bot/modules/locks/handlers/base.py` | `BaseLockToggleHandler` (1) | Shared lock flow leaves `_toggle_lock` abstract and command metadata to subclasses. |
| `PyAbstractClassInspection` | `sophie_bot/modules/restrictions/handlers/base.py` | `BaseRestrictionHandler` (1) | Shared restriction base leaves framework hooks and class-level settings to concrete handlers. |
| `PyAbstractClassInspection` | `sophie_bot/modules/ai/handlers/feature_setting.py` | `AIFeatureSetting` (1) | Shared status implementation leaves filters and feature metadata to concrete AI setting handlers. |
| `PyPropertyDefinitionInspection` | `sophie_bot/modules/ai/utils/ai_usage_service.py` | `AIUsageLike.total_tokens`, `AIModelLike.model_name` (2) | Read-only protocol properties with valid `...` stub bodies model computed third-party attributes; writable attributes would change the protocol contract. Ellipsis is valid here, not the only possible protocol body. |

## Inspections intentionally still enabled

The original MR640 audit left these 19 rule IDs unchanged because findings
were legitimate, had mixed intent, or could not be verified against its source
snapshot. The counts below are the historical SARIF counts reported by that
audit, not fresh measurements:

| Inspection ID | Results |
| --- | ---: |
| `PyCallingNonCallableInspection` | 1 |
| `PyChainedComparisonsInspection` | 3 |
| `PyClassHasNoInitInspection` | 202 |
| `PyComparisonWithNoneInspection` | 1 |
| `PyInconsistentReturnsInspection` | 78 |
| `PyIncorrectDocstringInspection` | 13 |
| `PyListCreationInspection` | 1 |
| `PyMethodMayBeStaticInspection` | 55 |
| `PyPep8NamingInspection` | 1 |
| `PyProtectedMemberInspection` | 24 |
| `PyRedeclarationInspection` | 1 |
| `PyRedundantParenthesesInspection` | 18 |
| `PyShadowingNamesInspection` | 19 |
| `PyTypeHintsInspection` | 1,149 |
| `PyUnboundLocalVariableInspection` | 14 |
| `PyUnusedFunctionInspection` | 1 |
| `PyUnusedImportsInspection` | 2 |
| `PyUnusedLocalVariableInspection` | 3 |
| `PyUnusedParameterInspection` | 53 |

Several results refer to files that do not exist in `origin/main`, or to lines
past the end of files in that worktree. Those occurrences are unverifiable and
therefore remain enabled; the audit did not treat a SARIF/source mismatch as a
false positive. The remaining rule families also include ordinary style,
typing, control-flow, API-contract, and unused-code findings, so their whole
sets are not proven false positives.

Original MR640 audit source: the supplied SARIF (not tracked here) and commit
`c5b6d20ab26c0bdc68438da828d3f2770f03a866` (`origin/main`).
