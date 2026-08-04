"""Unit tests for the Graph transforms.

The corpus-level acceptance check for splice_barrels -- reproducing zod's and
date-fns's recorded cost histograms -- lives in test_corpus_metrics.py.
"""

import pytest

from model.graph import filter_nodes, reroot, splice_barrels, value_edges_only
from tests.fixtures.graphs import (
    barrel_chain,
    barrel_cycle,
    barrel_with_test_files,
    graph,
    node,
    plan_md_tree,
)


def imports_of(g, node_id: str) -> tuple[str, ...]:
    return next(n for n in g.nodes if n.id == node_id).imports


def type_only_of(g, node_id: str) -> tuple[str, ...]:
    return next(n for n in g.nodes if n.id == node_id).type_only


def ids(g) -> set[str]:
    return {n.id for n in g.nodes}


# --- filter_nodes ---------------------------------------------------------


def test_filter_drops_matching_nodes_and_edges_into_them() -> None:
    g = filter_nodes(barrel_with_test_files(), ["**/*.test.ts"])
    assert "src/core.test.ts" not in ids(g)
    assert imports_of(g, "src/app.ts") == ("src/core.ts",)


def test_filter_glob_spans_directories() -> None:
    g = filter_nodes(barrel_with_test_files(), ["**/__tests__/**"])
    assert "src/__tests__/helper.ts" not in ids(g)
    assert "src/core.test.ts" in ids(g)


def test_filter_also_prunes_type_only() -> None:
    g = filter_nodes(barrel_with_test_files(), ["src/index.ts"])
    assert type_only_of(g, "src/__tests__/helper.ts") == ()
    assert imports_of(g, "src/__tests__/helper.ts") == ()


def test_filter_accepts_several_patterns() -> None:
    g = filter_nodes(barrel_with_test_files(), ["**/*.test.ts", "**/__tests__/**"])
    assert ids(g) == {"src/core.ts", "src/index.ts", "src/app.ts"}


def test_filter_with_no_patterns_is_identity() -> None:
    original = barrel_with_test_files()
    assert filter_nodes(original, []) == original


def test_filter_with_no_matches_is_identity() -> None:
    original = barrel_with_test_files()
    assert filter_nodes(original, ["**/*.py"]) == original


def test_filter_does_not_mutate_its_input() -> None:
    original = barrel_with_test_files()
    filter_nodes(original, ["**/*.test.ts"])
    assert "src/core.test.ts" in ids(original)


# --- splice_barrels -------------------------------------------------------


def test_splice_removes_barrels_and_rewires_transitively() -> None:
    g = splice_barrels(barrel_chain())
    assert ids(g) == {"a/x.ts", "a/y.ts", "b/consumer.ts"}
    assert imports_of(g, "b/consumer.ts") == ("a/x.ts", "a/y.ts")


def test_splice_keeps_an_edge_type_only_if_any_link_in_the_chain_is() -> None:
    g = splice_barrels(barrel_chain())
    assert type_only_of(g, "b/consumer.ts") == ("a/y.ts",)


def test_splice_lets_a_value_path_win_over_a_type_only_one() -> None:
    # consumer reaches a/x.ts twice: through the barrel as a type-only import,
    # and directly as a value import. One real value dependency is enough.
    g = splice_barrels(
        graph(
            node("a/x.ts"),
            node("a/index.ts", imports=("a/x.ts",), is_barrel=True),
            node(
                "b/consumer.ts",
                imports=("a/index.ts", "a/x.ts"),
                type_only=("a/index.ts",),
            ),
        )
    )
    assert imports_of(g, "b/consumer.ts") == ("a/x.ts",)
    assert type_only_of(g, "b/consumer.ts") == ()


def test_splice_survives_a_barrel_cycle() -> None:
    g = splice_barrels(barrel_cycle())
    assert ids(g) == {"d/real.ts", "consumer.ts"}
    assert imports_of(g, "consumer.ts") == ("d/real.ts",)


def test_splice_is_idempotent() -> None:
    once = splice_barrels(barrel_chain())
    assert splice_barrels(once) == once


def test_splice_is_idempotent_through_a_cycle() -> None:
    once = splice_barrels(barrel_cycle())
    assert splice_barrels(once) == once


def test_splice_drops_a_self_edge_created_by_a_barrel() -> None:
    # x.ts imports the barrel that re-exports x.ts itself; that is not a dependency.
    g = splice_barrels(
        graph(
            node("a/x.ts", imports=("a/index.ts",)),
            node("a/y.ts"),
            node("a/index.ts", imports=("a/x.ts", "a/y.ts"), is_barrel=True),
        )
    )
    assert imports_of(g, "a/x.ts") == ("a/y.ts",)


def test_splice_without_barrels_is_identity() -> None:
    original = plan_md_tree()
    assert splice_barrels(original) == original


def test_splice_leaves_a_barrel_reexporting_nothing_reachable_with_no_edges() -> None:
    g = splice_barrels(
        graph(
            node("a/index.ts", imports=("b/index.ts",), is_barrel=True),
            node("b/index.ts", imports=("a/index.ts",), is_barrel=True),
            node("consumer.ts", imports=("a/index.ts",)),
        )
    )
    assert ids(g) == {"consumer.ts"}
    assert imports_of(g, "consumer.ts") == ()


# --- value_edges_only -----------------------------------------------------


def test_value_edges_only_drops_type_edges() -> None:
    g = value_edges_only(plan_md_tree())
    assert imports_of(g, "a/x.ts") == ("a/index.ts", "a/sub/index.ts", "a/sub/deep/y.ts")
    assert type_only_of(g, "a/x.ts") == ()


def test_value_edges_only_keeps_every_node() -> None:
    original = plan_md_tree()
    assert ids(value_edges_only(original)) == ids(original)


# --- reroot ----------------------------------------------------------------


def test_reroot_strips_the_prefix_every_file_shares() -> None:
    g = reroot(
        graph(
            node("packages/zod/src/v4/core.ts", imports=("packages/zod/src/v3/old.ts",)),
            node("packages/zod/src/v3/old.ts"),
        )
    )
    assert ids(g) == {"v4/core.ts", "v3/old.ts"}
    assert imports_of(g, "v4/core.ts") == ("v3/old.ts",)
    assert g.roots == (".",)


def test_reroot_is_a_no_op_without_a_common_prefix() -> None:
    original = plan_md_tree()
    assert reroot(original) == original


def test_reroot_stops_at_the_first_branch() -> None:
    # packages/ has two children, so only it is stripped.
    g = reroot(
        graph(
            node("packages/core/src/a.ts"),
            node("packages/react/src/b.ts", imports=("packages/core/src/a.ts",)),
        )
    )
    assert ids(g) == {"core/src/a.ts", "react/src/b.ts"}


def test_reroot_leaves_the_edge_term_untouched() -> None:
    # The stripped levels each have exactly one child, and log2(1) = 0, so the
    # bit cost cannot change -- only the container count and the root branching.
    from model.metrics import total_bit_cost

    original = graph(
        node("pkgs/core/src/a.ts", imports=("pkgs/core/src/deep/b.ts",)),
        node("pkgs/core/src/deep/b.ts"),
    )
    assert total_bit_cost(reroot(original), 0.0)["bits"] == pytest.approx(
        total_bit_cost(original, 0.0)["bits"]
    )
    assert total_bit_cost(reroot(original))["containers"] == (
        total_bit_cost(original)["containers"] - 3
    )
