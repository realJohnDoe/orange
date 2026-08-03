"""Unit tests for the TypeScript extractor's policy layer (build_graph).

Hermetic: build_graph takes a synthetic payload dict, so no node process and
no corpus checkout are needed to test root filtering, extension filtering, id
normalization, or unresolved/external counting. `is_barrel` still needs real
source text on disk (tmp_path), the same as the Python extractor's tests. The
dependency-cruiser integration itself is covered by running the extractor
against the real corpus (see the module docstring in extractors/ts/extract.py).
"""

from pathlib import Path

import pytest

from corpus.sync import RepoEntry
from extractors.ts.extract import _has_allowed_extension, _norm, build_graph


def make_entry(
    name: str = "widget",
    commit: str = "0" * 40,
    roots: tuple[str, ...] = ("src",),
) -> RepoEntry:
    return RepoEntry(
        name=name,
        lang="ts",
        commit=commit,
        roots=roots,
        url="https://example.invalid/widget.git",
    )


def module(source: str, dependencies: list[dict] | None = None) -> dict:
    return {"source": source, "dependencies": dependencies or []}


def dep(resolved: str, *, type_only: bool = False, could_not_resolve: bool = False) -> dict:
    return {"resolved": resolved, "typeOnly": type_only, "couldNotResolve": could_not_resolve}


# --- _norm --------------------------------------------------------------


def test_norm_converts_backslashes() -> None:
    assert _norm("src\\a\\b.ts") == "src/a/b.ts"


def test_norm_rejects_absolute_paths() -> None:
    with pytest.raises(ValueError, match="repo-relative"):
        _norm("/src/a.ts")


def test_norm_rejects_paths_that_escape_their_root() -> None:
    with pytest.raises(ValueError, match="escapes its root"):
        _norm("src/../../etc/passwd")


# --- _has_allowed_extension ----------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("src/a.ts", True),
        ("src/a.tsx", True),
        ("src/a.d.ts", True),
        ("src/a.mts", True),
        ("src/a.js", True),
        ("src/a.mjs", True),
        ("src/a.md", False),
        ("src/a.json", False),
        ("src/a.css", False),
    ],
)
def test_has_allowed_extension(path: str, expected: bool) -> None:
    assert _has_allowed_extension(path) is expected


# --- build_graph: root and extension filtering ---------------------------


def test_build_graph_keeps_only_nodes_under_roots(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("export const a = 1;\n")
    entry = make_entry(roots=("src",))
    payload = {
        "modules": [
            module("src/a.ts"),
            module("other/b.ts"),  # outside the analyzed roots
        ]
    }

    graph = build_graph(payload, entry, tmp_path)

    assert [n.id for n in graph.nodes] == ["src/a.ts"]


def test_build_graph_drops_non_allowlisted_extensions(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("export const a = 1;\n")
    entry = make_entry(roots=("src",))
    payload = {"modules": [module("src/a.ts"), module("src/readme.md")]}

    graph = build_graph(payload, entry, tmp_path)

    assert [n.id for n in graph.nodes] == ["src/a.ts"]


# --- build_graph: edge counting ------------------------------------------


def test_build_graph_counts_unresolved_and_external_separately(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("import 'x'; import 'y'; import 'z';\n")
    entry = make_entry(roots=("src",))
    payload = {
        "modules": [
            module(
                "src/a.ts",
                [
                    dep("vitest", could_not_resolve=True),  # genuinely unresolved
                    dep("crypto"),  # resolved, but a Node core module -> not a kept node
                    dep("other/outside.ts"),  # resolved, but outside our roots
                ],
            )
        ]
    }

    graph = build_graph(payload, entry, tmp_path)

    assert graph.nodes[0].imports == ()
    assert graph.stats.unresolved_imports == 1
    assert graph.stats.external_imports_dropped == 2


def test_build_graph_keeps_edges_between_kept_nodes(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("import './b.js';\n")
    (tmp_path / "src" / "b.ts").write_text("export const b = 1;\n")
    entry = make_entry(roots=("src",))
    payload = {
        "modules": [
            module("src/a.ts", [dep("src/b.ts")]),
            module("src/b.ts"),
        ]
    }

    graph = build_graph(payload, entry, tmp_path)

    by_id = {n.id: n for n in graph.nodes}
    assert by_id["src/a.ts"].imports == ("src/b.ts",)
    assert graph.stats.unresolved_imports == 0
    assert graph.stats.external_imports_dropped == 0


def test_build_graph_type_only_is_a_subset_of_imports(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("import type { B } from './b.js';\n")
    (tmp_path / "src" / "b.ts").write_text("export type B = number;\n")
    entry = make_entry(roots=("src",))
    payload = {
        "modules": [
            module("src/a.ts", [dep("src/b.ts", type_only=True)]),
            module("src/b.ts"),
        ]
    }

    graph = build_graph(payload, entry, tmp_path)

    by_id = {n.id: n for n in graph.nodes}
    assert by_id["src/a.ts"].imports == ("src/b.ts",)
    assert by_id["src/a.ts"].type_only == ("src/b.ts",)


def test_build_graph_normalizes_backslashes_from_the_worker(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("import './b.js';\n")
    (tmp_path / "src" / "b.ts").write_text("export const b = 1;\n")
    entry = make_entry(roots=("src",))
    payload = {
        "modules": [
            module("src\\a.ts", [dep("src\\b.ts")]),
            module("src\\b.ts"),
        ]
    }

    graph = build_graph(payload, entry, tmp_path)

    assert [n.id for n in graph.nodes] == ["src/a.ts", "src/b.ts"]


# --- build_graph: nodes sorted, is_barrel from real source ----------------


def test_build_graph_sorts_nodes_by_id(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "z.ts").write_text("export const z = 1;\n")
    (tmp_path / "src" / "a.ts").write_text("export const a = 1;\n")
    entry = make_entry(roots=("src",))
    payload = {"modules": [module("src/z.ts"), module("src/a.ts")]}

    graph = build_graph(payload, entry, tmp_path)

    assert [n.id for n in graph.nodes] == ["src/a.ts", "src/z.ts"]


def test_build_graph_detects_barrel_from_disk(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text("export * from './a.js';\n")
    (tmp_path / "src" / "a.ts").write_text("export const a = 1;\n")
    entry = make_entry(roots=("src",))
    payload = {
        "modules": [
            module("src/index.ts", [dep("src/a.ts")]),
            module("src/a.ts"),
        ]
    }

    graph = build_graph(payload, entry, tmp_path)

    by_id = {n.id: n for n in graph.nodes}
    assert by_id["src/index.ts"].is_barrel is True
    assert by_id["src/a.ts"].is_barrel is False


def test_build_graph_stamps_repo_metadata(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("export const a = 1;\n")
    entry = make_entry(name="widget", commit="a" * 40, roots=("src",))
    payload = {"modules": [module("src/a.ts")]}

    graph = build_graph(payload, entry, tmp_path)

    assert graph.repo == "widget"
    assert graph.lang == "ts"
    assert graph.commit == "a" * 40
    assert graph.extractor == "dependency-cruiser"
    assert graph.roots == ("src",)
