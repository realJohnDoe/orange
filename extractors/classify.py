"""is_test / is_generated / is_face / is_barrel classification rules.

One place for these predicates so both extractors -- and the report, which
needs to explain a repo's numbers -- agree on what counts as a test file,
what's generated, and what's a barrel. Per-repo pattern additions come from
corpus/manifest.toml (see corpus.sync.RepoEntry) rather than from editing
this file, so an unusual repo convention is a data change, not a code
change.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

TEST_GLOBS: tuple[str, ...] = (
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

GENERATED_GLOBS: tuple[str, ...] = (
    "**/*.gen.ts",
    "**/*.generated.*",
    "**/*_pb2.py",
)

# Substrings looked for in the first few lines of a file, for generated files
# that don't follow a naming convention (e.g. meridian2's routeTree.gen.ts
# already matches GENERATED_GLOBS; this catches the ones that don't).
GENERATED_HEADER_SENTINELS: tuple[str, ...] = (
    "@generated",
    "Code generated",
    "DO NOT EDIT",
    "This file was automatically generated",
)

FACE_FILENAMES: dict[str, tuple[str, ...]] = {
    "py": ("__init__.py",),
    "ts": ("index.ts", "index.tsx"),
}


@dataclass(frozen=True, slots=True)
class RepoOverrides:
    """Per-repo pattern additions, sourced from manifest.toml."""

    test_globs: tuple[str, ...] = ()
    generated_globs: tuple[str, ...] = ()


def is_test(path: str, overrides: RepoOverrides = RepoOverrides()) -> bool:
    return _match_any(path, TEST_GLOBS + overrides.test_globs)


def is_generated(path: str, head: str = "", overrides: RepoOverrides = RepoOverrides()) -> bool:
    if _match_any(path, GENERATED_GLOBS + overrides.generated_globs):
        return True
    return any(sentinel in head for sentinel in GENERATED_HEADER_SENTINELS)


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


def _match_any(path: str, globs: tuple[str, ...]) -> bool:
    p = PurePosixPath(path)
    return any(p.full_match(glob) for glob in globs)
