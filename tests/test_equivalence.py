"""Structural equivalence -- the junk-drawer/taxonomy discriminator.

Two kinds of test. The synthetic ones pin the definition on shapes whose answer
is obvious by inspection; the corpus ones pin the six directories FINDINGS.md
labelled by hand, because those numbers are quoted in prose and a change that
moves them has to be noticed. `structural_equivalence` is also checked against
`container_equivalence` on every directory of every repo -- the fast path exists
only so date-fns is affordable, so it has to agree with the readable definition
everywhere rather than on a sample.
"""

from __future__ import annotations

import pytest

from model.equivalence import (
    container_equivalence,
    median_jaccard,
    neighbourhoods,
    structural_equivalence,
)
from model.graph import reroot, splice_barrels
from tests.fixtures.graphs import graph, node

# The six directories FINDINGS.md, "Junk drawer or taxonomy", labelled by eye.
FINDINGS_TABLE = [
    ("zod", "v4/locales", 1.000, 1.000),
    ("rich", "_unicode_data", 1.000, 0.000),
    ("vite", "node/plugins", 0.250, 0.100),
    ("vite", "shared", 0.000, 0.015),
    ("zod", "v3/helpers", 0.000, 0.333),
    ("date-fns", "_lib", 0.000, 0.000),
]


def parallel_siblings():
    """Three files that never touch and all import the same two modules.

    The taxonomy shape: out-neighbourhoods identical, no internal edges at all,
    so `directory_cohesion` calls it a three-way split and equivalence calls it
    what it is.
    """
    return graph(
        node("core/a.ts"),
        node("core/b.ts"),
        node("t/x.ts", imports=("core/a.ts", "core/b.ts")),
        node("t/y.ts", imports=("core/a.ts", "core/b.ts")),
        node("t/z.ts", imports=("core/a.ts", "core/b.ts")),
    )


def junk_drawer():
    """Three files in one directory, each importing something different."""
    return graph(
        node("core/a.ts"),
        node("core/b.ts"),
        node("core/c.ts"),
        node("t/x.ts", imports=("core/a.ts",)),
        node("t/y.ts", imports=("core/b.ts",)),
        node("t/z.ts", imports=("core/c.ts",)),
    )


def test_parallel_siblings_score_one() -> None:
    assert container_equivalence(parallel_siblings(), ("t",))["out_jaccard"] == 1.0


def test_disjoint_neighbourhoods_score_zero() -> None:
    assert container_equivalence(junk_drawer(), ("t",))["out_jaccard"] == 0.0


def test_children_importing_only_each_other_are_not_equivalent() -> None:
    """The convention that keeps a tight cluster from masquerading as a taxonomy.

    a -> b -> c is maximally cohesive and minimally equivalent: every neighbour
    is inside the directory, and each child has a different one.
    """
    g = graph(
        node("t/a.ts", imports=("t/b.ts",)),
        node("t/b.ts", imports=("t/c.ts",)),
        node("t/c.ts"),
    )
    assert container_equivalence(g, ("t",))["out_jaccard"] == 0.0


def test_an_edge_inside_one_child_is_not_a_neighbour() -> None:
    """Neighbourhoods are external to the *child*, not merely to the container.

    `t/sub` contracts to one child, so its interior edge must not make `sub`
    look like it neighbours itself.
    """
    g = graph(
        node("t/sub/a.ts", imports=("t/sub/b.ts",)),
        node("t/sub/b.ts"),
        node("t/other.ts"),
    )
    out, _ = neighbourhoods(g, ("t",))
    assert out[("dir", ("t", "sub"))] == frozenset()


def test_a_sibling_child_counts_as_a_neighbour() -> None:
    g = graph(node("t/a.ts", imports=("t/b.ts",)), node("t/b.ts"))
    out, into = neighbourhoods(g, ("t",))
    assert out[("file", "t/a.ts")] == frozenset({"t/b.ts"})
    assert into[("file", "t/b.ts")] == frozenset({"t/a.ts"})


def test_two_empty_neighbourhoods_score_zero_not_one() -> None:
    """Set theory says J(empty, empty) = 1; the metric must not.

    Files that import nothing are not evidence of a taxonomy, and the
    conventional definition would report a directory of them as a perfect 1.000.
    """
    assert median_jaccard([frozenset(), frozenset()]) == 0.0


def test_one_empty_neighbourhood_scores_zero() -> None:
    assert median_jaccard([frozenset(), frozenset({"a"})]) == 0.0


def test_fewer_than_two_children_has_no_pairs() -> None:
    assert median_jaccard([frozenset({"a"})]) is None
    assert median_jaccard([]) is None


def test_median_not_mean() -> None:
    """One overlapping pair among many disjoint ones must not carry the score."""
    sets = [frozenset({"a"}), frozenset({"a"}), frozenset({"b"}), frozenset({"c"})]
    assert median_jaccard(sets) == 0.0


def test_striding_bounds_the_pair_count_without_an_rng() -> None:
    """The cap has to be deterministic -- the CSVs it feeds are checked in."""
    sets = [frozenset({str(i % 3)}) for i in range(60)]
    first = median_jaccard(sets, max_pairs=50)
    assert first == median_jaccard(sets, max_pairs=50)
    assert first == pytest.approx(median_jaccard(sets), abs=0.5)


@pytest.mark.parametrize(("repo", "directory", "out", "into"), FINDINGS_TABLE)
def test_reproduces_the_findings_table(corpus_graph, repo, directory, out, into) -> None:
    g = splice_barrels(reroot(corpus_graph(repo)))
    scores = structural_equivalence(g)[directory]
    assert scores["out_jaccard"] == pytest.approx(out, abs=0.001)
    assert scores["in_jaccard"] == pytest.approx(into, abs=0.001)


@pytest.mark.parametrize("repo", ["flask", "requests", "rich", "zod", "tanstack-router", "vite"])
def test_fast_path_agrees_with_the_readable_definition(corpus_graph, repo) -> None:
    g = splice_barrels(reroot(corpus_graph(repo)))
    bulk = structural_equivalence(g)
    for path, scores in bulk.items():
        one = container_equivalence(g, tuple(path.split("/")) if path else ())
        assert one == scores, path


def test_a_locale_directory_scores_high_on_in_jaccard_not_out(corpus_graph) -> None:
    """The case that broke the out-only rule, pinned so it cannot quietly change.

    date-fns's 87 per-locale `_lib` directories each hold the same five parts
    pulled in by one index.ts. They are the most convention-governed directories
    in the corpus and the out-only rule calls every one of them a junk drawer.
    """
    g = splice_barrels(reroot(corpus_graph("date-fns")))
    scores = structural_equivalence(g)["locale/af/_lib"]
    assert scores["out_jaccard"] < 0.5
    assert scores["in_jaccard"] == 1.0
