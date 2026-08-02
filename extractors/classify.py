"""is_face / is_barrel structural predicates.

Both describe a file's role in the containment tree, but differently:
is_face is a pure function of (path, lang) -- no file content needed, so it
is never stored on a Node (see extractors.schema). is_barrel_py/is_barrel_ts
answer a content question ("is this file purely re-exports?") that only the
extractor's parse of the source can settle, so the cost model and barrel
splicing need that result stored per-node at extraction time.

Test/generated status is deliberately not here either: per the plan's
"extract once, filter in analysis" principle, that's an analysis-time
exclude-glob concern for report/run.py (not yet built), the same as
barrel-splicing and type-edge filtering. Nothing about excluding
tests/generated files belongs in this module until that CLI exists to
consume it.
"""

from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath

FACE_FILENAMES: dict[str, tuple[str, ...]] = {
    "py": ("__init__.py",),
    "ts": ("index.ts", "index.tsx"),
}


def is_face(path: str, lang: str) -> bool:
    name = PurePosixPath(path).name
    return name in FACE_FILENAMES.get(lang, ())


def is_barrel_py(source: str) -> bool:
    """Whether a Python module's body is only imports and an __all__ assignment.

    Intended for __init__.py -- that's the file the plan treats as Python's
    face, so this is the check that decides whether it's a pure barrel.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    if not tree.body:
        return False
    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(stmt, ast.Assign) and all(
            isinstance(target, ast.Name) and target.id == "__all__" for target in stmt.targets
        ):
            continue
        return False
    return True


_TS_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)

_TS_STRING = re.compile(
    r"`(?:[^`\\]|\\.)*`" r"|'(?:[^'\\]|\\.)*'" r'|"(?:[^"\\]|\\.)*"',
    re.DOTALL,
)

_TS_BARREL_STATEMENT = re.compile(
    r"^\s*export\s+(type\s+)?(\*(?:\s+as\s+\w+)?|\{[^}]*\})\s*from\s+['\"][^'\"]+['\"]\s*;?\s*$"
)


def is_barrel_ts(source: str) -> bool:
    """Whether a .ts/.tsx file is a pure re-export barrel.

    Lexical, not type-aware, per the plan: drop comments, normalize string and
    template literals to a placeholder (so their contents can't be mistaken
    for code, while the quotes an `export ... from '...'` needs survive), then
    check every remaining non-blank top-level line is a pure re-export.
    """
    stripped = _TS_COMMENT.sub("", source)
    normalized = _TS_STRING.sub("'x'", stripped)
    lines = [line for line in normalized.splitlines() if line.strip()]
    if not lines:
        return False
    return all(_TS_BARREL_STATEMENT.match(line) for line in lines)
