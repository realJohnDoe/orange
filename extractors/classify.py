"""is_face / is_barrel structural predicates, and a generic path-exclusion matcher.

is_face and is_barrel are facts about a file's role in the containment tree --
the cost model and barrel splicing need them, so extractors compute them at
extraction time. Test/generated status is not: it's an analysis-time filter,
applied by matching a node's id against exclude glob patterns rather than
being baked into the cached graph. That keeps it consistent with how the
plan already treats barrel-splicing and type-edge filtering (post-hoc
transforms over one maximally-inclusive graph), and it means "without
tests" / "without generated files" are just two values of the same
--exclude/--ignore flag report/run.py exposes, not separate schema fields.

DEFAULT_TEST_EXCLUDES and DEFAULT_GENERATED_EXCLUDES are exactly that: sensible
default values for --exclude, not classification rules a node is stamped
with. A repo with an unusual convention passes different patterns at report
time -- no code change needed.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from pathlib import PurePosixPath

DEFAULT_TEST_EXCLUDES: tuple[str, ...] = (
    "**/*.test.ts",
    "**/*.test.tsx",
    "**/*.spec.ts",
    "**/*.spec.tsx",
    "**/__tests__/**",
    "**/test-utils/**",
    "**/test_*.py",
    "**/*_test.py",
    "**/tests/**",
    "**/conftest.py",
    "**/e2e/**",
    "**/*.bench.*",
)

DEFAULT_GENERATED_EXCLUDES: tuple[str, ...] = (
    "**/*.gen.ts",
    "**/*.generated.*",
    "**/*_pb2.py",
)

FACE_FILENAMES: dict[str, tuple[str, ...]] = {
    "py": ("__init__.py",),
    "ts": ("index.ts", "index.tsx"),
}


def matches_any(path: str, patterns: Sequence[str]) -> bool:
    """Whether path matches any of the given glob patterns (`**` supported)."""
    p = PurePosixPath(path)
    return any(p.full_match(pattern) for pattern in patterns)


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
