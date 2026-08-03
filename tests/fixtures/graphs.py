"""Synthetic graphs small enough that every metric can be computed by hand.

The expected values live in the test files, not here -- a fixture that also
asserts its own numbers proves nothing. Each builder documents its shape; the
tests re-derive the answers from that shape independently.
"""

from __future__ import annotations

from extractors.schema import Graph, Lang, Node


def node(
    id: str,
    imports: tuple[str, ...] = (),
    type_only: tuple[str, ...] = (),
    is_barrel: bool = False,
) -> Node:
    return Node(id=id, kind="file", is_barrel=is_barrel, imports=imports, type_only=type_only)


def graph(*nodes: Node, lang: Lang = "ts") -> Graph:
    return Graph(
        repo="fixture",
        lang=lang,
        commit="0" * 40,
        extractor="fixture",
        roots=(".",),
        nodes=nodes,
    )


def plan_md_tree() -> Graph:
    """The six edges of plan.md's cost table, as one graph.

        root.ts
        a/index.ts        a/x.ts
        a/sub/index.ts    a/sub/deep/y.ts
        b/x.ts

    Edges, with their cost from that table:
        a/x -> a/index            0   sibling
        a/x -> root               0   pure ascent          (type-only)
        a/x -> a/sub/index        1   own child's face
        b/x -> a/index            1   sibling's face
        a/x -> a/sub/deep/y       2   own subtree, deep
        b/x -> a/sub/deep/y       3   stranger's interior
    """
    return graph(
        node("root.ts"),
        node("a/index.ts"),
        node(
            "a/x.ts",
            imports=("a/index.ts", "root.ts", "a/sub/index.ts", "a/sub/deep/y.ts"),
            type_only=("root.ts",),
        ),
        node("a/sub/index.ts"),
        node("a/sub/deep/y.ts"),
        node("b/x.ts", imports=("a/index.ts", "a/sub/deep/y.ts")),
    )


def descending_chain() -> Graph:
    """Three files whose depth decreases exactly as their fan-in increases.

        x.ts        depth 0, fan-in 2   (imported by p/y and p/q/z)
        p/y.ts      depth 1, fan-in 1   (imported by p/q/z)
        p/q/z.ts    depth 2, fan-in 0

    Perfectly monotone and tie-free, so Spearman rho is exactly -1.
    """
    return graph(
        node("x.ts"),
        node("p/y.ts", imports=("x.ts",)),
        node("p/q/z.ts", imports=("x.ts", "p/y.ts")),
    )


def flat_repo() -> Graph:
    """Every file at the same depth -- the degenerate case the depth gate exists for."""
    return graph(
        node("src/a.ts", imports=("src/b.ts",)),
        node("src/b.ts", imports=("src/c.ts",)),
        node("src/c.ts"),
    )


def two_clusters() -> Graph:
    """One directory holding two independent pairs plus an unreferenced leaf.

    pkg/ children: {a1,a2}, {b1,b2}, {lonely} -> component sizes [2, 2, 1].
    Two components of size >= 2, so this split is *genuine*.
    """
    return graph(
        node("pkg/a1.ts", imports=("pkg/a2.ts",)),
        node("pkg/a2.ts"),
        node("pkg/b1.ts", imports=("pkg/b2.ts",)),
        node("pkg/b2.ts"),
        node("pkg/lonely.ts"),
    )


def one_cluster_and_a_leaf() -> Graph:
    """One directory holding a connected pair plus an isolated leaf.

    pkg/ children: {a1,a2}, {lonely} -> component sizes [2, 1]. Split, but only
    one component has >= 2 members, so it is *not* genuine: this is the benign
    shape (a stray data file, an independent test) the filter exists to ignore.
    """
    return graph(
        node("pkg/a1.ts", imports=("pkg/a2.ts",)),
        node("pkg/a2.ts"),
        node("pkg/lonely.ts"),
    )


def barrel_chain() -> Graph:
    """A consumer reaching two files through two nested barrels.

        b/consumer.ts -> outer/index.ts -> a/index.ts -> {a/x.ts, a/y.ts}

    Both index files are barrels. `a/index.ts -> a/y.ts` is type-only and every
    other edge is a value edge, so splicing must leave consumer -> a/x.ts a
    value edge and consumer -> a/y.ts type-only.
    """
    return graph(
        node("a/x.ts"),
        node("a/y.ts"),
        node("a/index.ts", imports=("a/x.ts", "a/y.ts"), type_only=("a/y.ts",), is_barrel=True),
        node("outer/index.ts", imports=("a/index.ts",), is_barrel=True),
        node("b/consumer.ts", imports=("outer/index.ts",)),
    )


def barrel_cycle() -> Graph:
    """Two barrels re-exporting each other, one of which re-exports a real file.

        c/index.ts <-> d/index.ts -> d/real.ts
        consumer.ts -> c/index.ts

    The cycle must not recurse forever, and consumer must still reach d/real.ts.
    """
    return graph(
        node("d/real.ts"),
        node("c/index.ts", imports=("d/index.ts",), is_barrel=True),
        node("d/index.ts", imports=("c/index.ts", "d/real.ts"), is_barrel=True),
        node("consumer.ts", imports=("c/index.ts",)),
    )


def barrel_with_test_files() -> Graph:
    """A barrel, a real file, and two test files that exclude globs should remove."""
    return graph(
        node("src/core.ts"),
        node("src/index.ts", imports=("src/core.ts",), is_barrel=True),
        node("src/core.test.ts", imports=("src/core.ts",)),
        node("src/__tests__/helper.ts", imports=("src/index.ts",), type_only=("src/index.ts",)),
        node("src/app.ts", imports=("src/core.test.ts", "src/core.ts")),
    )
