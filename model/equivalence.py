"""Structural equivalence: do a directory's children have the same *neighbours*?

FINDINGS.md's junk-drawer/taxonomy discriminator, promoted from an ad-hoc script
to real code because PR 7 needs to score it against labels.

`metrics.directory_cohesion` asks whether a directory's children link to *each
other*. That question is blind by construction to files that never touch but do
the same job: zod's 52 locale tables import an identical set and import nothing
from one another, so cohesion calls them a 52-way split and the bit cost calls
the directory a `costs` verdict, and both are wrong as advice. The other
question -- do they have the same neighbours? -- is answerable from the same
graph and separates the two cases:

    zod v4/locales    out 1.000   52 files importing an identical set
    rich _unicode_data out 1.000   23 data tables, identical imports
    vite shared        out 0.000   disjoint neighbourhoods -- junk drawer

Two conventions, both load-bearing and neither obvious:

- **Neighbourhoods are external.** An edge between two children of the same
  directory is cohesion's business, not this metric's; counting it here would
  let a tightly-coupled cluster score as "parallel siblings" because its members
  all neighbour each other.
- **Two empty neighbourhoods score 0, not 1.** Set-theoretically J(empty, empty)
  is usually defined as 1, and that would be actively wrong here: a directory of
  files that import nothing would score a perfect 1.000 and be reported as a
  taxonomy on no evidence whatever. Equivalence has to be *witnessed* by shared
  neighbours. Pairs where exactly one side is empty score 0 under the ordinary
  formula anyway, so this only fixes the degenerate case.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from statistics import median
from typing import Any

from extractors.schema import Graph
from model.metrics import edges
from model.paths import Child, child_of, common_prefix_len, dirs

# Pairwise Jaccard is O(k^2) in a container's child count, which is fine for
# every directory in the corpus except a wide root (date-fns has ~1200 children
# under one). Above this the pairs are strided deterministically rather than
# sampled randomly: the checked-in CSVs have to be reproducible, so anything
# that depends on an RNG is out.
MAX_PAIRS = 20_000


def neighbourhoods(
    graph: Graph, container: tuple[str, ...]
) -> tuple[dict[Child, frozenset[str]], dict[Child, frozenset[str]]]:
    """(out, in) external neighbours of each direct child of `container`.

    A child's out-neighbours are the nodes its files import from outside the
    child; its in-neighbours are the nodes importing into it from outside.
    Neighbours are node ids rather than contracted children, since the question
    is whether two siblings depend on the *same things*, and contracting the far
    end would call two files equivalent for both importing something, anywhere,
    under `src/`.
    """
    depth = len(container)
    out: dict[Child, set[str]] = defaultdict(set)
    into: dict[Child, set[str]] = defaultdict(set)
    for node in graph.nodes:
        if dirs(node.id)[:depth] == container:
            child = child_of(container, node.id)
            out.setdefault(child, set())
            into.setdefault(child, set())

    for u, v, _ in edges(graph):
        du, dv = dirs(u), dirs(v)
        u_in = du[:depth] == container
        v_in = dv[:depth] == container
        if u_in and v_in and child_of(container, u) == child_of(container, v):
            continue  # interior to one child: neither endpoint is a neighbour
        if u_in:
            out[child_of(container, u)].add(v)
        if v_in:
            into[child_of(container, v)].add(u)
    return (
        {c: frozenset(s) for c, s in out.items()},
        {c: frozenset(s) for c, s in into.items()},
    )


def median_jaccard(sets: Sequence[frozenset[str]], max_pairs: int = MAX_PAIRS) -> float | None:
    """Median pairwise Jaccard over `sets`, or None when there is no pair.

    Two empty sets score 0.0 rather than the conventional 1.0 -- see the module
    docstring. The median rather than the mean because one shared neighbour
    across an otherwise disjoint directory should not drag the score up: the
    claim being tested is that a *typical* pair of children is interchangeable.
    """
    n = len(sets)
    if n < 2:
        return None
    total = n * (n - 1) // 2
    stride = 1 + total // max_pairs
    scores = []
    for k, (i, j) in enumerate(_pairs(n)):
        if k % stride:
            continue
        a, b = sets[i], sets[j]
        union = len(a | b)
        scores.append(len(a & b) / union if union else 0.0)
    return median(scores)


def container_equivalence(
    graph: Graph, container: tuple[str, ...], max_pairs: int = MAX_PAIRS
) -> dict[str, Any]:
    """Median out- and in-neighbourhood Jaccard for one directory's children."""
    out, into = neighbourhoods(graph, container)
    order = sorted(out, key=str)
    return {
        "children": len(order),
        "out_jaccard": median_jaccard([out[c] for c in order], max_pairs),
        "in_jaccard": median_jaccard([into[c] for c in order], max_pairs),
    }


def structural_equivalence(graph: Graph, max_pairs: int = MAX_PAIRS) -> dict[str, dict[str, Any]]:
    """container_equivalence for every directory, keyed by POSIX path ("" is the root).

    One pass over the edges rather than one per container: the per-container
    version is the readable definition, this is the one report/ can afford to
    call on date-fns.
    """
    out: dict[tuple[str, ...], dict[Child, set[str]]] = defaultdict(dict)
    into: dict[tuple[str, ...], dict[Child, set[str]]] = defaultdict(dict)
    for node in graph.nodes:
        d = dirs(node.id)
        for k in range(len(d) + 1):
            child = child_of(d[:k], node.id)
            out[d[:k]].setdefault(child, set())
            into[d[:k]].setdefault(child, set())

    for u, v, _ in edges(graph):
        du, dv = dirs(u), dirs(v)
        shared = common_prefix_len(du, dv)
        # Above the LCA both endpoints sit in the *same* child, so the edge is
        # interior there and contributes nothing. From the LCA down they differ,
        # and every such container sees a real external neighbour.
        for k in range(shared, len(du) + 1):
            out[du[:k]][child_of(du[:k], u)].add(v)
        for k in range(shared, len(dv) + 1):
            into[dv[:k]][child_of(dv[:k], v)].add(u)

    result = {}
    for container in sorted(out):
        order = sorted(out[container], key=str)
        result["/".join(container)] = {
            "children": len(order),
            "out_jaccard": median_jaccard([frozenset(out[container][c]) for c in order], max_pairs),
            "in_jaccard": median_jaccard([frozenset(into[container][c]) for c in order], max_pairs),
        }
    return result


def _pairs(n: int):
    for i in range(n):
        for j in range(i + 1, n):
            yield i, j
