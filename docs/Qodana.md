# Qodana configuration

Qodana uses the recommended profile. The two excluded inspections below were
reviewed against the available SARIF output and the current source; they are
not suppressions for unreviewed findings.

## Excluded inspections

| Inspection ID | Findings | Evidence and rationale |
| --- | ---: | --- |
| `PyCallingNonCallableInspection` | 1 | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/parse_arg.py:85` calls `self.child`, whose concrete type is `ButtonArg`, a subclass of ASS `OrArg` and `ArgFabric`. ASS defines `ArgFabric.__call__`; the call is covered by the existing button parser tests. Qodana does not resolve this inherited callable from the external ASS dependency. |
| `PyPropertyDefinitionInspection` | 2 | `sophie_bot/modules/ai/utils/ai_usage_service.py:23` and `:71` are read-only properties in `AIUsageLike` and `AIModelLike` protocols. Their `...` bodies are the required protocol declaration, modeling computed third-party properties (`RunUsage.total_tokens` and `Model.model_name`) without requiring writable attributes. |

No source SARIF was present at the requested path
`/home/yacha/.hermes/cache/documents/doc_5ad27c08200e_3Pjr1_p5l0d_b7dc9491_33fc_4bc2_979b_875d07c540ef_qodana_sarif.json`.
The audit used the complete local artifacts at
`/tmp/sophie-qodana-unsuppressed-results/qodana.sarif.json` and
`/tmp/sophie-qodana-results/qodana.sarif.json`, which contain respectively
the three findings and the two property findings after the existing local
suppression run. The repository source was audited at `origin/main`
(`c5b6d20ab26c0bdc68438da828d3f2770f03a866`).

No production fix or new test was added because all three findings are
intentional framework/dependency patterns and existing tests cover the only
runtime call site.
