"""Pins the cost function against the worked table in plan.md.

If this drifts, every metric downstream is wrong -- see the Phase 0 plan's
Verification section.
"""

import pytest

from model.paths import cost, dirs, lca

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
