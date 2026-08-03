"""Metrics pinned against fixtures small enough to check by hand.

Every expected number here is re-derived from the fixture's docstring shape in a
comment, so a failure says which of the two is wrong rather than just "changed".
"""

import math

import numpy as np
import pytest
from scipy import stats  # dev-only: shipped code stays numpy-only

from model.metrics import (
    conditional_entropy,
    cost_histogram,
    cross_face_entries,
    depth_histogram,
    depth_vs_fanin,
    directory_cohesion,
    edges,
    integer_edge_cost,
    spearman,
    total_bit_cost,
)
from model.paths import dirs
from tests.fixtures.graphs import (
    descending_chain,
    flat_repo,
    graph,
    node,
    one_cluster_and_a_leaf,
    plan_md_tree,
    two_clusters,
)

# --- edges ----------------------------------------------------------------


def test_edges_deduplicates_repeated_imports() -> None:
    # Extractors emit one entry per import statement; the same target imported
    # twice is one edge.
    g = graph(
        node("b.ts"),
        node("a.ts", imports=("b.ts", "b.ts"), type_only=("b.ts",)),
    )
    assert list(edges(g)) == [("a.ts", "b.ts", False)]


def test_edges_marks_an_edge_type_only_when_every_statement_is() -> None:
    g = graph(
        node("b.ts"),
        node("a.ts", imports=("b.ts", "b.ts"), type_only=("b.ts", "b.ts")),
    )
    assert list(edges(g)) == [("a.ts", "b.ts", True)]


# --- cost histogram and total ---------------------------------------------


def test_cost_histogram_matches_the_plan_md_table() -> None:
    # Costs of the six fixture edges: 0, 0, 1, 1, 2, 3.
    h = cost_histogram(plan_md_tree())["all"]
    assert h["edges"] == 6
    assert h["counts"] == {"0": 2, "1": 2, "2": 1, "3+": 1}
    assert h["fractions"]["0"] == pytest.approx(1 / 3)
    assert h["fractions"]["3+"] == pytest.approx(1 / 6)


def test_cost_histogram_splits_type_only_from_value() -> None:
    # Only a/x -> root.ts is type-only, and it is the pure-ascent edge (cost 0).
    h = cost_histogram(plan_md_tree())
    assert h["type_only"]["counts"] == {"0": 1, "1": 0, "2": 0, "3+": 0}
    assert h["value"]["counts"] == {"0": 1, "1": 2, "2": 1, "3+": 1}


def test_cost_histogram_buckets_everything_past_three_together() -> None:
    g = graph(node("a/b/c/d/deep.ts"), node("top.ts", imports=("a/b/c/d/deep.ts",)))
    assert cost_histogram(g)["all"]["counts"] == {"0": 0, "1": 0, "2": 0, "3+": 1}


def test_integer_edge_cost() -> None:
    # 0 + 0 + 1 + 1 + 2 + 3 = 7 over 6 edges; sorted costs [0,0,1,1,2,3].
    t = integer_edge_cost(plan_md_tree())
    assert t == {
        "edges": 6,
        "total": 7,
        "mean": pytest.approx(7 / 6),
        "median": pytest.approx(1.0),
        "p90": pytest.approx(2.5),
    }


def test_integer_edge_cost_of_an_edgeless_graph_is_undefined_not_zero() -> None:
    t = integer_edge_cost(graph(node("a.ts"), node("b.ts")))
    assert t["edges"] == 0
    assert t["mean"] is None


# --- the objective: total bit cost ----------------------------------------


def test_total_bit_cost_reports_the_two_terms_separately() -> None:
    # plan_md_tree's directories are a, a/sub, a/sub/deep, b -- the root is not a
    # container in the objective, since it exists in every candidate layout.
    t = total_bit_cost(plan_md_tree(), c=8.0)
    assert t["containers"] == 4
    assert t["structure_bits"] == pytest.approx(32.0)
    assert t["objective"] == pytest.approx(t["bits"] + 32.0)


def test_total_bit_cost_sums_the_edges() -> None:
    # root {root.ts, a, b} = 3, a {index, x, sub} = 3, a/sub {index, deep} = 2,
    # a/sub/deep {y} = 1. Six edges:
    #   a/x -> a/index          lg 3
    #   a/x -> root.ts          lg 3
    #   a/x -> a/sub/index      lg 3 + lg 2
    #   a/x -> a/sub/deep/y     lg 3 + lg 2 + lg 1
    #   b/x -> a/index          lg 3 + lg 3
    #   b/x -> a/sub/deep/y     lg 3 + lg 3 + lg 2 + lg 1
    lg3 = math.log2(3)
    expected = lg3 + lg3 + (lg3 + 1) + (lg3 + 1) + 2 * lg3 + (2 * lg3 + 1)
    assert total_bit_cost(plan_md_tree())["bits"] == pytest.approx(expected)


def test_flattening_is_free_under_the_integer_cost_but_not_under_bits() -> None:
    # The collapse the integer cost cannot see: 64 files in one directory.
    flat = graph(node("hub.ts", imports=("D/f0.ts",)), *(node(f"D/f{i}.ts") for i in range(64)))
    assert integer_edge_cost(flat)["total"] == 1
    assert total_bit_cost(flat)["bits"] == pytest.approx(1.0 + 6.0)


def test_compression_ratio_is_bits_per_edge_over_the_entropy_floor() -> None:
    # One importer selecting among 4 targets, all five files at the root. The
    # tree must address the importer too, so it charges lg 5 per edge against a
    # floor of lg 4 -- the ratio is slightly above 1 even here, which is why 1.0
    # is the asymptotic ideal rather than an attainable score.
    g = graph(
        node("u.ts", imports=("t0.ts", "t1.ts", "t2.ts", "t3.ts")),
        *(node(f"t{i}.ts") for i in range(4)),
    )
    t = total_bit_cost(g)
    assert t["entropy_floor"] == pytest.approx(2.0)
    assert t["bits_per_edge"] == pytest.approx(math.log2(5))
    assert t["compression_ratio"] == pytest.approx(math.log2(5) / 2.0)


def test_single_file_directories_are_pure_structure_overhead() -> None:
    # plan.md's named falsification check, in miniature: date-fns's 937
    # one-file directories should score as pure C overhead carrying zero
    # addressing information. Wrapping each target in its own directory adds a
    # selection but no choice, and log2(1) = 0, so the edge term is untouched
    # and only the structure term moves.
    flat = total_bit_cost(
        graph(node("u.ts", imports=("t0.ts", "t1.ts")), node("t0.ts"), node("t1.ts"))
    )
    nested = total_bit_cost(
        graph(node("u.ts", imports=("a/t0.ts", "b/t1.ts")), node("a/t0.ts"), node("b/t1.ts"))
    )
    assert nested["bits"] == pytest.approx(flat["bits"])
    assert nested["compression_ratio"] == pytest.approx(flat["compression_ratio"])
    assert flat["containers"] == 0
    assert nested["containers"] == 2
    assert nested["objective"] > flat["objective"]


def test_compression_ratio_is_undefined_when_there_is_nothing_to_disambiguate() -> None:
    # Every importer has exactly one target, so H(v|u) = 0 and no code can beat
    # free -- a ratio against a zero floor would be meaningless, not infinite.
    g = graph(node("a.ts", imports=("b.ts",)), node("b.ts"))
    t = total_bit_cost(g)
    assert t["entropy_floor"] == pytest.approx(0.0)
    assert t["compression_ratio"] is None


def test_conditional_entropy_is_the_edge_weighted_average() -> None:
    # u has 4 targets (2 bits each, 4 edges), w has 1 (0 bits, 1 edge).
    g = graph(
        node("u.ts", imports=("t0.ts", "t1.ts", "t2.ts", "t3.ts")),
        node("w.ts", imports=("t0.ts",)),
        *(node(f"t{i}.ts") for i in range(4)),
    )
    assert conditional_entropy(g) == pytest.approx(4 * 2.0 / 5)


# --- cross-face entries ---------------------------------------------------


def test_cross_face_entries() -> None:
    # The four edges costing >= 1:
    #   a/x -> a/sub/index      gateway a/sub, lands on that dir's face
    #   b/x -> a/index          gateway a,     lands on that dir's face
    #   a/x -> a/sub/deep/y     gateway a/sub, penetrates a/sub/deep
    #   b/x -> a/sub/deep/y     gateway a,     penetrates a/sub and a/sub/deep
    # Gateways {a, a/sub} = 2; penetrated {a, a/sub, a/sub/deep} = 3;
    # directories in the repo: a, a/sub, a/sub/deep, b = 4.
    c = cross_face_entries(plan_md_tree())
    assert c["entries"] == 4
    assert c["gateway_dirs"] == 2
    assert c["penetrated_dirs"] == 3
    assert c["directories"] == 4
    assert c["face_hits"] == 2
    assert c["face_hit_fraction"] == pytest.approx(0.5)
    assert c["gateway_dirs_per_dir"] == pytest.approx(0.5)
    assert c["penetrated_dirs_per_dir"] == pytest.approx(0.75)


def test_reaching_past_a_face_is_not_a_face_hit() -> None:
    # Same gateway directory, but the target is behind the face, not at it.
    g = graph(node("a/index.ts"), node("a/hidden.ts"), node("b/x.ts", imports=("a/hidden.ts",)))
    c = cross_face_entries(g)
    assert c["entries"] == 1
    assert c["face_hits"] == 0


def test_face_detection_follows_the_graph_language() -> None:
    g = graph(
        node("a/__init__.py"),
        node("b/x.py", imports=("a/__init__.py",)),
        lang="py",
    )
    assert cross_face_entries(g)["face_hits"] == 1


def test_a_repo_with_no_descents_reports_no_entries() -> None:
    c = cross_face_entries(flat_repo())
    assert c["entries"] == 0
    assert c["face_hit_fraction"] is None


# --- depth vs fan-in ------------------------------------------------------


def test_depth_vs_fanin_is_minus_one_on_a_perfectly_ordered_repo() -> None:
    # depths 0,1,2 against fan-ins 2,1,0 -- monotone and tie-free.
    d = depth_vs_fanin(descending_chain())
    assert d["rho_all"] == pytest.approx(-1.0)
    assert d["n_all"] == 3
    assert d["rho_fanin_ge_1"] == pytest.approx(-1.0)
    assert d["n_fanin_ge_1"] == 2


def test_depth_vs_fanin_is_undefined_when_every_file_sits_at_one_depth() -> None:
    assert depth_vs_fanin(flat_repo())["rho_all"] is None


def test_fanin_counts_distinct_importers_not_edges() -> None:
    g = graph(
        node("shared.ts"),
        node("a.ts", imports=("shared.ts", "shared.ts")),
        node("b.ts", imports=("shared.ts",)),
    )
    assert depth_vs_fanin(g)["n_fanin_ge_1"] == 1


def test_depth_vs_fanin_matches_scipy_on_a_real_graph(corpus_graph) -> None:
    g = corpus_graph("zod")
    importers: dict[str, set[str]] = {n.id: set() for n in g.nodes}
    for u, v, _ in edges(g):
        importers[v].add(u)
    depth = np.array([len(dirs(n.id)) for n in g.nodes], dtype=float)
    fanin = np.array([len(importers[n.id]) for n in g.nodes], dtype=float)
    expected = stats.spearmanr(depth, fanin).statistic
    assert depth_vs_fanin(g)["rho_all"] == pytest.approx(expected)


# --- spearman -------------------------------------------------------------


def test_spearman_perfect_negative() -> None:
    x = np.array([1.0, 2.0, 3.0, 4.0])
    assert spearman(x, x[::-1]) == pytest.approx(-1.0)


def test_spearman_averages_tied_ranks() -> None:
    # ranks of x are [1.5, 1.5, 3, 4] against [1, 2, 3, 4] -> rho = 4.5 / sqrt(4.5 * 5)
    x = np.array([1.0, 1.0, 2.0, 3.0])
    y = np.array([1.0, 2.0, 3.0, 4.0])
    assert spearman(x, y) == pytest.approx(4.5 / np.sqrt(4.5 * 5))


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_spearman_matches_scipy_with_heavy_ties(seed: int) -> None:
    rng = np.random.default_rng(seed)
    x = rng.integers(0, 4, size=50).astype(float)
    y = rng.integers(0, 6, size=50).astype(float)
    assert spearman(x, y) == pytest.approx(stats.spearmanr(x, y).statistic)


def test_spearman_is_undefined_for_a_constant_vector() -> None:
    assert spearman(np.array([1.0, 1.0, 1.0]), np.array([1.0, 2.0, 3.0])) is None


def test_spearman_is_undefined_below_two_points() -> None:
    assert spearman(np.array([1.0]), np.array([2.0])) is None


# --- depth histogram (the gate) -------------------------------------------


def test_depth_histogram_counts_files_per_depth() -> None:
    # root.ts at 0; a/index, a/x, b/x at 1; a/sub/index at 2; a/sub/deep/y at 3.
    h = depth_histogram(plan_md_tree())
    assert h["counts"] == {"0": 1, "1": 3, "2": 1, "3": 1}
    assert h["distinct_depths"] == 4
    assert h["modal_share"] == pytest.approx(0.5)
    assert h["informative"] is True


def test_depth_histogram_flags_a_single_depth_repo_as_uninformative() -> None:
    h = depth_histogram(flat_repo())
    assert h["distinct_depths"] == 1
    assert h["informative"] is False


def test_depth_histogram_flags_a_near_constant_repo_as_uninformative() -> None:
    nodes = [node(f"src/f{i}.ts") for i in range(99)] + [node("src/sub/one.ts")]
    h = depth_histogram(graph(*nodes))
    assert h["distinct_depths"] == 2
    assert h["informative"] is False


# --- directory cohesion ---------------------------------------------------


def test_cohesion_calls_two_real_clusters_a_genuine_split() -> None:
    c = directory_cohesion(two_clusters())
    assert c["directories_considered"] == 1
    assert c["splits"] == 1
    assert c["genuine_splits"] == 1
    assert c["detail"][0]["components"] == [2, 2, 1]
    assert c["detail"][0]["dir"] == "pkg"


def test_cohesion_does_not_call_one_cluster_plus_a_leaf_genuine() -> None:
    c = directory_cohesion(one_cluster_and_a_leaf())
    assert c["splits"] == 1
    assert c["genuine_splits"] == 0
    assert c["detail"][0]["components"] == [2, 1]
    assert c["detail"][0]["genuine"] is False


def test_cohesion_contracts_a_subdirectory_to_one_child() -> None:
    # pkg/ holds one file and a subtree of three; the subtree counts once, so
    # the single edge into it makes pkg cohesive.
    g = graph(
        node("pkg/entry.ts", imports=("pkg/sub/a.ts",)),
        node("pkg/sub/a.ts", imports=("pkg/sub/b.ts",)),
        node("pkg/sub/b.ts", imports=("pkg/sub/c.ts",)),
        node("pkg/sub/c.ts"),
    )
    c = directory_cohesion(g)
    assert c["splits"] == 0


def test_cohesion_ignores_directories_with_a_single_child() -> None:
    g = graph(node("a/b/only.ts"), node("root.ts", imports=("a/b/only.ts",)))
    # Root has two children (root.ts and the `a` subtree); a/ and a/b/ have one each.
    assert directory_cohesion(g)["directories_considered"] == 1


def test_cohesion_on_the_plan_md_tree() -> None:
    # Considered: root {root.ts, a, b}, a/ {index, x, sub}, a/sub {index, deep}.
    # Only a/sub is disconnected: nothing links a/sub/index.ts to the deep/ subtree,
    # because the edges reaching deep/ come from outside a/sub entirely.
    c = directory_cohesion(plan_md_tree())
    assert c["directories_considered"] == 3
    assert c["splits"] == 1
    assert c["split_rate"] == pytest.approx(1 / 3)
    assert c["genuine_splits"] == 0
    assert c["detail"][0]["dir"] == "a/sub"


def test_cohesion_of_a_repo_with_no_multi_child_directories_is_undefined() -> None:
    c = directory_cohesion(graph(node("a/b/only.ts")))
    assert c["directories_considered"] == 0
    assert c["split_rate"] is None
