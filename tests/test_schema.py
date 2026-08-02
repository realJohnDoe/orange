from pathlib import Path

import pytest

from extractors.schema import Graph, Node, Stats, from_dict, load, to_dict, write


def make_node(id: str, imports: tuple[str, ...] = (), type_only: tuple[str, ...] = ()) -> Node:
    return Node(
        id=id,
        kind="file",
        is_barrel=False,
        imports=imports,
        type_only=type_only,
    )


def make_graph() -> Graph:
    return Graph(
        repo="example",
        lang="ts",
        commit="a" * 40,
        extractor="test-fixture",
        roots=("src",),
        generated_at="2026-08-01T00:00:00Z",
        nodes=(
            make_node("src/a.ts", imports=("src/b.ts",), type_only=("src/b.ts",)),
            make_node("src/b.ts"),
        ),
        stats=Stats(unresolved_imports=1, external_imports_dropped=2, ambiguous=0),
    )


def test_valid_graph_constructs() -> None:
    graph = make_graph()
    assert len(graph.nodes) == 2


def test_rejects_duplicate_node_ids() -> None:
    with pytest.raises(ValueError, match="duplicate node ids"):
        Graph(
            repo="example",
            lang="ts",
            commit="a" * 40,
            extractor="test-fixture",
            roots=("src",),
            generated_at="2026-08-01T00:00:00Z",
            nodes=(make_node("src/a.ts"), make_node("src/a.ts")),
        )


def test_rejects_edge_to_unknown_node() -> None:
    with pytest.raises(ValueError, match="unknown node"):
        Graph(
            repo="example",
            lang="ts",
            commit="a" * 40,
            extractor="test-fixture",
            roots=("src",),
            generated_at="2026-08-01T00:00:00Z",
            nodes=(make_node("src/a.ts", imports=("src/missing.ts",)),),
        )


def test_rejects_non_sha_commit() -> None:
    with pytest.raises(ValueError, match="not a git commit sha"):
        Graph(
            repo="example",
            lang="ts",
            commit="main",
            extractor="test-fixture",
            roots=("src",),
            generated_at="2026-08-01T00:00:00Z",
            nodes=(),
        )


def test_node_rejects_type_only_not_subset_of_imports() -> None:
    with pytest.raises(ValueError, match="subset of imports"):
        make_node("src/a.ts", imports=(), type_only=("src/b.ts",))


def test_node_rejects_leading_slash_id() -> None:
    with pytest.raises(ValueError, match="POSIX path"):
        make_node("/src/a.ts")


def test_node_rejects_backslash_id() -> None:
    with pytest.raises(ValueError, match="POSIX path"):
        make_node("src\\a.ts")


def test_to_dict_from_dict_roundtrip() -> None:
    graph = make_graph()
    assert from_dict(to_dict(graph)) == graph


def test_write_load_roundtrip(tmp_path: Path) -> None:
    graph = make_graph()
    path = tmp_path / "graphs" / "example.json.gz"
    write(graph, path)
    assert load(path) == graph
