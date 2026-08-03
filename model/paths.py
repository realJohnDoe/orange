"""Path arithmetic underlying the *integer* cost function in plan.md.

cost(u -> v) is the number of containers strictly between LCA(u, v) and v. For
concrete file paths that reduces to pure path arithmetic: the number of
directory components of v that lie beyond the longest common prefix of u's and
v's directory components. No face detection is needed to compute it — see the
cost table in plan.md, which tests/test_paths.py pins verbatim.

This is plan.md's *reporting* view, not its objective. The objective is the bit
cost, which charges log2(branching) per selection from the LCA down to and
including v itself; it is a function of the whole tree rather than of two
strings, so it does not belong in this module's pure-path-arithmetic shape and
takes the branching data explicitly. See plan.md, PR 4a.
"""

from __future__ import annotations

from pathlib import PurePosixPath


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
