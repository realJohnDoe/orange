"""Path arithmetic underlying the *integer* cost function in plan.md.

cost(u -> v) is the number of containers strictly between LCA(u, v) and v. For
concrete file paths that reduces to pure path arithmetic: the number of
directory components of v that lie beyond the longest common prefix of u's and
v's directory components. No face detection is needed to compute it — see the
cost table in plan.md, which tests/test_paths.py pins verbatim.

This is plan.md's *reporting* view, not its objective. The objective is
bit_cost() below, which charges log2(branching) per selection from the LCA down
to and including v itself. It is a function of the whole tree rather than of two
strings, which is why it takes the branching map explicitly instead of quietly
reaching for a graph: everything else in this module is answerable from two path
strings, and that difference is load-bearing (see plan.md, "Fractality").
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from pathlib import PurePosixPath

from extractors.schema import Graph

# A directory's child is either one of its own files or one whole subdirectory
# contracted to a single node: ("file", path) or ("dir", components). Both count
# as exactly one selection, which is what makes log2(|children|) an address
# length rather than a file count.
type Child = tuple[str, str] | tuple[str, tuple[str, ...]]


def dirs(path: str) -> tuple[str, ...]:
    """Directory components of a repo-root-relative POSIX path, excluding the filename.

    PurePosixPath, not Path: ids are POSIX-normalized by contract (see the schema
    in the Phase 0 plan), so parsing must not depend on which OS this runs on.
    """
    return PurePosixPath(path).parts[:-1]


def common_prefix_len(a: tuple[str, ...], b: tuple[str, ...]) -> int:
    """Length of the longest common leading run of two directory-component tuples."""
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def lca(u: str, v: str) -> tuple[str, ...]:
    """Directory-component path of the lowest common ancestor container of u and v."""
    du, dv = dirs(u), dirs(v)
    return du[: common_prefix_len(du, dv)]


def cost(u: str, v: str) -> int:
    """cost(u -> v): number of faces crossed descending from LCA(u, v) to v."""
    dv = dirs(v)
    k = common_prefix_len(dirs(u), dv)
    return len(dv) - k


def child_of(container: tuple[str, ...], node_id: str) -> Child:
    """Which direct child of `container` holds `node_id`.

    The contraction the whole model rests on: a subdirectory is one child no
    matter how much lives underneath it. Assumes node_id is inside container.
    """
    d = dirs(node_id)
    if len(d) == len(container):
        return ("file", node_id)
    return ("dir", d[: len(container) + 1])


def branching(graph: Graph) -> dict[tuple[str, ...], int]:
    """Direct-child count of every directory in the graph, root included as ().

    Files and immediate subdirectories each count as one child. The result is
    what bit_cost charges log2 of, so it must be recomputed after any transform
    that adds or removes nodes -- model.graph.filter_nodes and splice_barrels
    both change it.
    """
    children: dict[tuple[str, ...], set[Child]] = defaultdict(set)
    for node in graph.nodes:
        d = dirs(node.id)
        for k in range(len(d) + 1):
            children[d[:k]].add(child_of(d[:k], node.id))
    return {container: len(members) for container, members in children.items()}


def bit_cost(u: str, v: str, branching: Mapping[tuple[str, ...], int]) -> float:
    """bit_cost(u -> v): bits spent naming v from u, per plan.md's objective.

    One log2(|children|) term per selection on the descent from LCA(u, v), the
    last of which selects v itself among its siblings -- so there is always one
    more term than the integer cost, and even a sibling reference is not free.

    log2(1) = 0, so a single-child directory contributes nothing: it carries no
    addressing information, which is exactly the date-fns finding in plan.md.

    Takes the branching map explicitly rather than a Graph because it is a
    function of the whole tree, not of two strings -- the property that made this
    not belong in pure path arithmetic (plan.md, "Fractality").
    """
    dv = dirs(v)
    k = common_prefix_len(dirs(u), dv)
    return sum(math.log2(branching[dv[:i]]) for i in range(k, len(dv) + 1))
