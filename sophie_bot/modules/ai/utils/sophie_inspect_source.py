from __future__ import annotations

from pathlib import Path

import sophie_bot

# The sub-agent may only ever look at Sophie's own Python sources: no config, no data, no secrets.
SOURCE_ROOT = Path(sophie_bot.__file__).resolve().parent

# Every cap here exists to bound what one sub-agent run can pull into its context.
MAX_SEARCH_MATCHES = 25
MAX_MATCH_LINE_LENGTH = 200
MAX_READ_LINES = 120


def _relative(path: Path) -> str:
    return str(path.relative_to(SOURCE_ROOT))


def _resolve(relative_path: str) -> Path | None:
    """Resolve a caller-supplied path inside the source root, or None when it escapes it."""
    candidate = (SOURCE_ROOT / relative_path).resolve()
    if not candidate.is_relative_to(SOURCE_ROOT) or candidate.suffix != ".py" or not candidate.is_file():
        return None
    return candidate


def search_source(query: str) -> list[str]:
    """Case-insensitive substring search over Sophie's sources, as ``path:line: text`` entries."""
    needle = query.strip().lower()
    if not needle:
        return []

    matches: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue

        for number, line in enumerate(lines, start=1):
            if needle in line.lower():
                matches.append(f"{_relative(path)}:{number}: {line.strip()[:MAX_MATCH_LINE_LENGTH]}")
                if len(matches) >= MAX_SEARCH_MATCHES:
                    return matches
    return matches


def read_source(relative_path: str, start_line: int = 1) -> str | None:
    """Read a bounded window of one source file, or None when the path is not a Sophie source."""
    path = _resolve(relative_path)
    if path is None:
        return None

    lines = path.read_text(encoding="utf-8").splitlines()
    first = max(start_line, 1)
    window = lines[first - 1 : first - 1 + MAX_READ_LINES]
    numbered = "\n".join(f"{number}: {line}" for number, line in enumerate(window, start=first))
    remaining = len(lines) - (first - 1 + len(window))
    if remaining > 0:
        numbered += f"\n... {remaining} more lines, continue with start_line={first + len(window)}"
    return numbered
