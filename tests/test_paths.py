"""Pins both cost functions against the worked table in plan.md.

If this drifts, every metric downstream is wrong. The table's "Integer cost"
column is the reporting contract; the "Bit cost" column alongside it is the
objective, and both are pinned here against the same six edges.
"""

import math

import pytest

from model.paths import bit_cost, branching, child_of, cost, dirs, lca
from tests.fixtures.graphs import graph, node

# The six edges from the "Cost function" table in plan.md, verbatim.
PLAN_MD_TABLE = [
    pytest.param("a/x.ts", "a/y.ts", 0, id="sibling-file"),
    pytest.param("a/x.ts", "shared.ts", 0, id="pure-ascent-to-root"),
    pytest.param("a/x.ts", "a/sub/index.ts", 1, id="own-childs-face"),
    pytest.param("b/x.ts", "a/index.ts", 1, id="siblings-face"),
    pytest.param("a/x.ts", "a/sub/deep/y.ts", 2, id="own-subtree-deep"),
    pytest.param("b/x.ts", "a/sub/deep/y.ts", 3, id="strangers-interior"),
]


@pytest.mark.parametrize("u,v,expected", PLAN_MD_TABLE)
def test_cost_matches_plan_md_table(u: str, v: str, expected: int) -> None:
    assert cost(u, v) == expected


def test_self_edge_is_free() -> None:
    assert cost("a/sub/x.ts", "a/sub/x.ts") == 0


def test_root_level_files_never_cost_more_than_zero() -> None:
    assert cost("shared.ts", "other.ts") == 0
    assert cost("a/sub/deep/x.ts", "other.ts") == 0


def test_deeply_nested_same_subtree_is_free() -> None:
    assert cost("a/sub/deep/x.ts", "a/sub/deep/y.ts") == 0


def test_deeply_nested_descent_counts_every_face() -> None:
    assert cost("root.ts", "a/sub/deep/deeper/y.ts") == 4


def test_ascent_out_of_a_deep_subtree_is_free() -> None:
    assert cost("a/sub/deep/x.ts", "a/shared.ts") == 0


def test_dirs_excludes_filename() -> None:
    assert dirs("a/sub/x.ts") == ("a", "sub")
    assert dirs("root.ts") == ()


def test_lca_is_the_common_directory_prefix() -> None:
    assert lca("a/sub/x.ts", "a/other/y.ts") == ("a",)
    assert lca("a/x.ts", "b/y.ts") == ()
    assert lca("a/sub/x.ts", "a/sub/y.ts") == ("a", "sub")


# --- branching ------------------------------------------------------------


def plan_md_shape():
    """The tree plan.md's cost table is written against, with known branching.

        root:           shared.ts, a/, b/                     -> 3 children
        a/:             index.ts, x.ts, y.ts, sub/            -> 4
        a/sub/:         index.ts, deep/                       -> 2
        a/sub/deep/:    y.ts, z.ts                            -> 2
        b/:             x.ts                                  -> 1
    """
    return graph(
        node("shared.ts"),
        node("a/index.ts"),
        node("a/y.ts"),
        node(
            "a/x.ts",
            imports=("a/y.ts", "shared.ts", "a/sub/index.ts", "a/sub/deep/y.ts"),
        ),
        node("a/sub/index.ts"),
        node("a/sub/deep/y.ts"),
        node("a/sub/deep/z.ts"),
        node("b/x.ts", imports=("a/index.ts", "a/sub/deep/y.ts")),
    )


def test_branching_counts_files_and_subdirectories_alike() -> None:
    b = branching(plan_md_shape())
    assert b[()] == 3
    assert b[("a",)] == 4
    assert b[("a", "sub")] == 2
    assert b[("a", "sub", "deep")] == 2
    assert b[("b",)] == 1


def test_branching_covers_every_directory_including_the_root() -> None:
    b = branching(graph(node("a/b/c/deep.ts")))
    assert set(b) == {(), ("a",), ("a", "b"), ("a", "b", "c")}
    assert set(b.values()) == {1}


def test_child_of_contracts_a_whole_subtree_to_one_child() -> None:
    assert child_of((), "a/b/c/deep.ts") == ("dir", ("a",))
    assert child_of(("a", "b"), "a/b/c/deep.ts") == ("dir", ("a", "b", "c"))
    assert child_of(("a", "b", "c"), "a/b/c/deep.ts") == ("file", "a/b/c/deep.ts")


# --- bit cost -------------------------------------------------------------

LG3 = math.log2(3)

# The same six edges, now against the table's "Bit cost" column, resolved
# through plan_md_shape's branching factors (root 3, a 4, sub 2, deep 2).
PLAN_MD_BIT_TABLE = [
    pytest.param("a/x.ts", "a/y.ts", 2.0, id="sibling-file: lg a"),
    pytest.param("a/x.ts", "shared.ts", LG3, id="pure-ascent: lg root"),
    pytest.param("a/x.ts", "a/sub/index.ts", 3.0, id="own-childs-face: lg a + lg sub"),
    pytest.param("b/x.ts", "a/index.ts", LG3 + 2.0, id="siblings-face: lg root + lg a"),
    pytest.param("a/x.ts", "a/sub/deep/y.ts", 4.0, id="own-subtree-deep: lg a + sub + deep"),
    pytest.param(
        "b/x.ts", "a/sub/deep/y.ts", LG3 + 4.0, id="strangers-interior: lg root + a + sub + deep"
    ),
]


@pytest.mark.parametrize("u,v,expected", PLAN_MD_BIT_TABLE)
def test_bit_cost_matches_plan_md_table(u: str, v: str, expected: float) -> None:
    assert bit_cost(u, v, branching(plan_md_shape())) == pytest.approx(expected)


def test_bit_cost_always_has_one_more_term_than_the_integer_cost() -> None:
    # Under uniform branching b, bits = (integer + 1) * log2(b) -- plan.md's
    # statement of how the reporting view coarsens the objective. Here b = 2.
    binary = graph(
        node("l.ts"),
        node("r/l.ts"),
        node("r/r/l.ts"),
        node("r/r/r.ts"),
    )
    b = branching(binary)
    assert b == {(): 2, ("r",): 2, ("r", "r"): 2}
    for u, v in [("l.ts", "r/l.ts"), ("l.ts", "r/r/l.ts"), ("r/l.ts", "r/r/l.ts")]:
        assert bit_cost(u, v, b) == pytest.approx(cost(u, v) + 1)


def test_a_single_child_directory_costs_nothing_to_enter() -> None:
    # log2(1) = 0: date-fns's 937 one-file directories carry no addressing
    # information, so they are pure C overhead (plan.md, "What we've learned").
    g = graph(node("top.ts"), node("only/deeper/one.ts"))
    assert cost("top.ts", "only/deeper/one.ts") == 2
    assert bit_cost("top.ts", "only/deeper/one.ts", branching(g)) == pytest.approx(1.0)


# --- split-neutrality: the whole argument for the formula ------------------


def flat_directory(k: int = 100):
    """`outside.ts` plus one directory D holding k files."""
    return graph(node("outside.ts"), *(node(f"D/f{i:02d}.ts") for i in range(k)))


def split_directory(m: int = 10, per: int = 10):
    """The same leaves, repartitioned into m subdirectories of `per` files each."""
    return graph(
        node("outside.ts"),
        *(node(f"D/g{i}/f{j}.ts") for i in range(m) for j in range(per)),
    )


def test_pure_regrouping_is_free_from_outside() -> None:
    # Row 1 of plan.md's split-neutrality table: log2(k) == log2(m) + log2(k/m).
    flat = bit_cost("outside.ts", "D/f42.ts", branching(flat_directory()))
    split = bit_cost("outside.ts", "D/g4/f2.ts", branching(split_directory()))
    assert flat == pytest.approx(split)
    assert flat == pytest.approx(1.0 + math.log2(100))


def test_pure_regrouping_is_free_across_groups() -> None:
    # Row 2: an edge the partition cuts pays exactly what it paid flat.
    flat = bit_cost("D/f00.ts", "D/f42.ts", branching(flat_directory()))
    split = bit_cost("D/g0/f0.ts", "D/g4/f2.ts", branching(split_directory()))
    assert flat == pytest.approx(split)


def test_regrouping_pays_off_only_for_edges_it_keeps_together() -> None:
    # Row 3, the entire value of splitting: an uncut edge saves log2(m).
    flat = bit_cost("D/f40.ts", "D/f42.ts", branching(flat_directory()))
    split = bit_cost("D/g4/f0.ts", "D/g4/f2.ts", branching(split_directory()))
    assert flat - split == pytest.approx(math.log2(10))


def test_an_unbalanced_split_is_strictly_worse_than_a_balanced_one() -> None:
    # plan.md's "absent locality, flatten" corollary: 90 + 10 costs a file in the
    # big group log2(2) + log2(90) = 7.49 bits, up from log2(100) = 6.64 flat.
    unbalanced = graph(
        node("outside.ts"),
        *(node(f"D/big/f{i}.ts") for i in range(90)),
        *(node(f"D/small/f{i}.ts") for i in range(10)),
    )
    b = branching(unbalanced)
    into_big = bit_cost("outside.ts", "D/big/f0.ts", b)
    flat = bit_cost("outside.ts", "D/f42.ts", branching(flat_directory()))
    assert into_big == pytest.approx(1.0 + math.log2(2) + math.log2(90))
    assert into_big > flat
