# Qodana configuration

This project keeps Qodana's recommended profile enabled and suppresses only
inspection IDs whose complete result set was verified against `origin/main` as
intentional project patterns.

## Suppressed inspections

The SARIF audit contained 1,649 results before suppression. The following 10
results are suppressed, so the expected result count after this configuration
is 1,639:

| Inspection ID | Results | Evidence and rationale |
| --- | ---: | --- |
| `PyAbstractClassInspection` | 8 | Every occurrence is a framework/base handler class intentionally left abstract: `StatusHandlerABC`, `StatusBoolHandlerABC`, `StatusIntHandlerABC`, `FederationCommandHandler`, `FederationPromoteDemoteHandler`, `BaseLockToggleHandler`, `BaseRestrictionHandler`, or the shared `AIFeatureSetting` base. The source uses `ABC`, `ABCMeta`, or abstract methods/class-level extension points, and concrete subclasses implement the required behavior. |
| `PyPropertyDefinitionInspection` | 2 | Both occurrences are read-only properties in `Protocol` definitions (`AIUsageLike.total_tokens` and `AIModelLike.model_name`) using the required `...` protocol body. The comments document that the properties model computed third-party attributes and must remain non-writable. |

## Inspections intentionally still enabled

These 19 rule IDs were not globally suppressed because at least one occurrence
is a legitimate warning, has mixed intent, or cannot be verified against the
current `origin/main` source. The counts below are the complete SARIF counts:

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

Source audited: the SARIF file supplied for this change and commit
`c5b6d20ab26c0bdc68438da828d3f2770f03a866` (`origin/main`).
