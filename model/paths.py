"""Path arithmetic underlying the cost function in plan.md.

cost(u -> v) is defined there as the number of containers strictly between
LCA(u, v) and v. For concrete file paths that reduces to pure path arithmetic:
the number of directory components of v that lie beyond the longest common
prefix of u's and v's directory components. No face detection is needed to
compute it — see the verification table in the Phase 0 plan.
"""

from __future__ import annotations


def dirs(path: str) -> tuple[str, ...]:
    """Directory components of a repo-root-relative POSIX path, excluding the filename."""
    parts = path.split("/")
    return tuple(parts[:-1])


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
