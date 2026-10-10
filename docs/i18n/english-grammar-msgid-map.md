# English grammar msgid mapping

The adjacent [CSV](english-grammar-msgid-map.csv) records the 37 original English
gettext msgids corrected by this audit, relative to commit `6ef562ef`.
Each row contains the complete old and new msgid, its correction category,
runtime or help/metadata usage, gettext helper, context, and source locations
before and after the edit. Locations identify the string literals, as in Babel
extraction. The CSV is UTF-8; read it with a CSV parser to preserve quoting.
All contexts are empty, and no plural msgids or formatting placeholders changed.

Translation migration is a separate issue. This change leaves tracked POT/PO
catalogs and translations untouched. Until catalogs and translations are migrated,
the corrected msgids fall back to the original English source text. Do not treat
this mapping as translated content or automatically translate its new msgids.
Doc-only corrections in `docs/modules/` and `wiki_docs/` are not gettext msgids
and are excluded from the CSV.

The audit inspected the available `origin/docs/filter-handler-help` ref for
MR !757 and `mr729/source` for MR !729. The six detailed Filters help messages
added by !757 and its locale additions are excluded. Edits to Filters help here
correct only prose already present at the baseline.

Validation used existing help/i18n and relevant E2E tests, Ruff, pycln, type
checking, and `git diff --check`. An AST comparison confirmed that every Python
change affects only a gettext string argument, with identical placeholders.
Babel extraction to scratch storage verified all 37 new msgids and their source
locations without changing tracked catalogs. Human-maintained Filters and Notes
prose matches its existing wiki copies.

The `make commit` recipe was inspected rather than run: it extracts and merges
catalogs, regenerates all wiki/API output, and its API step can copy output into a
sibling checkout. Relevant checks were run individually within this worktree.
