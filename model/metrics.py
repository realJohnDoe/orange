"""Phase 0 metrics: what the cost model says about a repo's *actual* layout.

One function per metric, each taking a Graph and returning a plain dict so
report/ can serialize it without knowing anything about the shapes here. No
placement engine, no dominators, no symbol resolution -- these measure the
layout that exists, which is the whole of Phase 0 (see plan.md).

The metrics come straight from plan.md's PR 4a list. Two things there are not
metrics and are marked as such: the depth histogram is a *gate* -- in a repo
where nearly every file sits at the same depth, the depth-vs-fan-in correlation
is measuring noise and must be reported as uninformative rather than as evidence
-- and directory cohesion is a *diagnostic*, deliberately not a term in the
objective (plan.md rejects connected-component repulsion as one).

total_bit_cost is the objective; everything else here describes the layout.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from typing import Any

import numpy as np

from extractors.classify import is_face
from extractors.schema import Graph
from model.paths import Child, bit_cost, branching, child_of, common_prefix_len, cost, dirs

# Above this share of files at a single depth, the depth vector is effectively
# constant and any correlation against it is noise. requests trips this; that is
# the point of having the gate at all.
_DEGENERATE_DEPTH_SHARE = 0.95

_COST_BUCKETS = ("0", "1", "2", "3+")

# Bits a container must save to justify existing. Provisional and deliberately
# round: plan.md's PR 4c calibrates it by sweeping it against local optimality
# across the reference corpus, and nothing before then should read a meaning
# into the particular value. The rule of thumb it implies (from m = 2: a
# directory must keep W > 2C internal edges uncut to justify splitting in two)
# is the only reason 8 rather than 0.5 or 500.
DEFAULT_C = 8.0


def cost_histogram(graph: Graph) -> dict[str, Any]:
    """Fraction of edges at cost 0 / 1 / 2 / 3+, overall and split by edge kind."""
    all_edges = list(edges(graph))
    return {
        "all": _bucket(cost(u, v) for u, v, _ in all_edges),
        "type_only": _bucket(cost(u, v) for u, v, t in all_edges if t),
        "value": _bucket(cost(u, v) for u, v, t in all_edges if not t),
    }


def integer_edge_cost(graph: Graph) -> dict[str, Any]:
    """Mean/median/p90 integer edge cost -- continuity with the published numbers.

    Superseded as the objective by total_bit_cost, and kept because every Phase 0
    result in plan.md is quoted in these units. Mean cost per edge is not
    comparable across repos (it scales with depth, hence with size); the
    compression ratio in total_bit_cost is the number that is.
    """
    costs = [cost(u, v) for u, v, _ in edges(graph)]
    if not costs:
        return {"edges": 0, "total": 0, "mean": None, "median": None, "p90": None}
    array = np.array(costs, dtype=float)
    return {
        "edges": len(costs),
        "total": int(array.sum()),
        "mean": float(array.mean()),
        "median": float(np.median(array)),
        "p90": float(np.percentile(array, 90)),
    }


def total_bit_cost(graph: Graph, c: float = DEFAULT_C) -> dict[str, Any]:
    """The objective: Sigma bit_cost(e), the structure term, and the ratio between.

    The two terms are reported apart on purpose -- `C · |containers|` is the only
    place the one free parameter enters, so folding it into a single total would
    hide exactly the sensitivity PR 4c exists to measure.

    `compression_ratio` is the scale-free score: the tree's bits per edge divided
    by the conditional entropy H(v|u), which is the floor any code for this edge
    distribution must pay. 1.0 means the directory tree is an optimal code for
    this dependency graph; 1.7 means it spends 70% more bits than the graph
    requires. Unlike mean edge cost this is comparable across repos of different
    sizes, which is the whole reason plan.md wants it.

    Read 1.0 as an asymptote, not a reachable score: H(v|u) conditions on the
    importer and so charges nothing for finding it, while the tree is one shared
    code that must address importers and targets alike. A directory holding an
    importer and its `d` targets charges log2(d+1) against a floor of log2(d).
    The gap shrinks as repos grow, but comparisons between repos are the point
    and those are unaffected.

    The floor is 0 when every file imports at most one target -- there is nothing
    to disambiguate, so no code can do better than free and the ratio is
    undefined rather than infinite.
    """
    all_edges = list(edges(graph))
    tree = branching(graph)
    total = sum(bit_cost(u, v, tree) for u, v, _ in all_edges)
    containers = len(_all_dirs(graph))
    floor = conditional_entropy(graph)
    return {
        "edges": len(all_edges),
        "bits": total,
        "bits_per_edge": _ratio(total, len(all_edges)),
        "containers": containers,
        "structure_bits": c * containers,
        "c": c,
        "objective": total + c * containers,
        "entropy_floor": floor,
        "compression_ratio": (
            total / (len(all_edges) * floor) if all_edges and floor > 0 else None
        ),
    }


def conditional_entropy(graph: Graph) -> float:
    """H(v|u) in bits: how much a perfect code would need per edge.

    Each distinct edge is one observation, so an importer with `d` targets
    contributes log2(d) bits for each of them -- a file importing one thing
    carries no uncertainty, and the entropy of the whole graph is the
    edge-weighted average of those.
    """
    out_degree = Counter(u for u, _, _ in edges(graph))
    n_edges = sum(out_degree.values())
    if not n_edges:
        return 0.0
    return sum(d * math.log2(d) for d in out_degree.values()) / n_edges


def cross_face_entries(graph: Graph) -> dict[str, Any]:
    """How many directories are entered from outside -- the barrel demand.

    For an edge costing >= 1, the *gateway* is the first directory crossed on the
    descent: that is the container whose face the rule would require to exist.
    The *penetrated* set is every directory on that descent, which counts how
    deep outsiders reach rather than just how many doors they open.

    face_hit_fraction separates "entered through the front door" (the target is
    that directory's index.ts / __init__.py) from "reached past the face into the
    interior" -- the same cost, very different structural meaning.
    """
    gateways: set[tuple[str, ...]] = set()
    penetrated: set[tuple[str, ...]] = set()
    entries = 0
    face_hits = 0
    for u, v, _ in edges(graph):
        dv = dirs(v)
        k = common_prefix_len(dirs(u), dv)
        if len(dv) - k < 1:
            continue
        entries += 1
        gateway = dv[: k + 1]
        gateways.add(gateway)
        for depth in range(k + 1, len(dv) + 1):
            penetrated.add(dv[:depth])
        if dv == gateway and is_face(v, graph.lang):
            face_hits += 1
    n_dirs = len(_all_dirs(graph))
    return {
        "entries": entries,
        "gateway_dirs": len(gateways),
        "gateway_dirs_per_dir": _ratio(len(gateways), n_dirs),
        "penetrated_dirs": len(penetrated),
        "penetrated_dirs_per_dir": _ratio(len(penetrated), n_dirs),
        "face_hits": face_hits,
        "face_hit_fraction": _ratio(face_hits, entries),
        "directories": n_dirs,
    }


def depth_vs_fanin(graph: Graph) -> dict[str, Any]:
    """Spearman rho between a file's directory depth and its distinct importers.

    plan.md predicts strongly negative: shared things float up. Reported twice
    because the zero-fan-in files (nothing imports them) are a large, structurally
    different population -- they are free to sit anywhere, so including them
    measures something slightly different from ranking the files that are
    actually depended upon.

    Read alongside depth_histogram: if that says uninformative, rho here is noise.
    """
    importers: dict[str, set[str]] = {n.id: set() for n in graph.nodes}
    for u, v, _ in edges(graph):
        if u != v:
            importers[v].add(u)
    depth = np.array([len(dirs(n.id)) for n in graph.nodes], dtype=float)
    fanin = np.array([len(importers[n.id]) for n in graph.nodes], dtype=float)
    imported = fanin >= 1
    return {
        "rho_all": spearman(depth, fanin),
        "n_all": int(len(depth)),
        "rho_fanin_ge_1": spearman(depth[imported], fanin[imported]),
        "n_fanin_ge_1": int(imported.sum()),
    }


def depth_histogram(graph: Graph) -> dict[str, Any]:
    """File counts per directory depth, and whether the spread is informative."""
    counts: dict[int, int] = defaultdict(int)
    for node in graph.nodes:
        counts[len(dirs(node.id))] += 1
    n = len(graph.nodes)
    modal_share = max(counts.values()) / n if n else 1.0
    return {
        "counts": {str(d): counts[d] for d in sorted(counts)},
        "distinct_depths": len(counts),
        "modal_share": modal_share,
        "informative": len(counts) >= 2 and modal_share <= _DEGENERATE_DEPTH_SHARE,
    }


def directory_cohesion(graph: Graph) -> dict[str, Any]:
    """Is each directory's induced subgraph over its direct children connected?

    Cost cannot see this: two mutually unrelated files in one directory cost 0
    wherever they sit, so nothing in Sigma cost ever pressures them apart. Children
    are the directory's own files plus each immediate subdirectory contracted to a
    single node, so a cohesive subtree counts once rather than swamping the test.

    A split is *genuine* when at least two components have >= 2 members: one real
    cluster plus a few isolated leaves is the common benign shape (independent
    test files, per-locale data tables), while two real clusters is a placement
    candidate. Even a genuine split is not automatically a finding -- see the
    zsf.ts / _unicode_data ambiguity in plan.md's open questions.
    """
    children: dict[tuple[str, ...], set[Child]] = defaultdict(set)
    for node in graph.nodes:
        d = dirs(node.id)
        for k in range(len(d) + 1):
            children[d[:k]].add(child_of(d[:k], node.id))

    links: dict[tuple[str, ...], list[tuple[Child, Child]]] = defaultdict(list)
    for u, v, _ in edges(graph):
        du, dv = dirs(u), dirs(v)
        for k in range(common_prefix_len(du, dv) + 1):
            container = du[:k]
            cu, cv = child_of(container, u), child_of(container, v)
            if cu != cv:
                links[container].append((cu, cv))

    considered = 0
    splits: list[dict[str, Any]] = []
    genuine = 0
    for container, members in sorted(children.items()):
        if len(members) < 2:
            continue
        considered += 1
        sizes = _component_sizes(members, links[container])
        if len(sizes) == 1:
            continue
        is_genuine = sum(1 for s in sizes if s >= 2) >= 2
        genuine += is_genuine
        splits.append(
            {
                "dir": "/".join(container),
                "children": len(members),
                "components": sizes,
                "genuine": is_genuine,
            }
        )
    return {
        "directories_considered": considered,
        "splits": len(splits),
        "split_rate": _ratio(len(splits), considered),
        "genuine_splits": genuine,
        "genuine_split_rate": _ratio(genuine, considered),
        "detail": splits,
    }


def all_metrics(graph: Graph, c: float = DEFAULT_C) -> dict[str, Any]:
    """Every metric for one graph, in the shape report/ serializes per repo."""
    return {
        "repo": graph.repo,
        "lang": graph.lang,
        "commit": graph.commit,
        "nodes": len(graph.nodes),
        "cost_histogram": cost_histogram(graph),
        "total_bit_cost": total_bit_cost(graph, c),
        "integer_edge_cost": integer_edge_cost(graph),
        "cross_face_entries": cross_face_entries(graph),
        "depth_vs_fanin": depth_vs_fanin(graph),
        "depth_histogram": depth_histogram(graph),
        "directory_cohesion": directory_cohesion(graph),
    }


def edges(graph: Graph) -> Iterator[tuple[str, str, bool]]:
    """(importer, target, is_type_only) for every distinct edge, in node order.

    Deduplicated on purpose. Extractors record one entry per import *statement*,
    so a file that imports the same module twice -- typically once as `import
    type` and once for a value -- lists that target twice. That is a fact about
    the source text, not two dependencies: the containment tree has one edge
    there, and counting it twice would weight such files more heavily in every
    metric. Because the duplicate is dropped here rather than at extraction,
    the cached graphs stay a faithful record of what the extractor saw.

    An edge is type-only only when *every* statement behind it is type-only; a
    single value import makes the whole edge a value dependency, matching how
    model.graph.splice_barrels merges the type-only flag across paths.
    """
    for node in graph.nodes:
        statements = Counter(node.imports)
        type_statements = Counter(node.type_only)
        seen: set[str] = set()
        for target in node.imports:
            if target in seen:
                continue
            seen.add(target)
            yield node.id, target, type_statements[target] == statements[target]


def spearman(x: np.ndarray, y: np.ndarray) -> float | None:
    """Spearman rho with average ranks for ties; None when it is undefined.

    Undefined means fewer than two points, or one vector constant (every rank
    equal, so there is no variance to correlate). Cross-checked against
    scipy.stats.spearmanr in the tests -- scipy is a dev dependency, so it may
    not be imported here.
    """
    if len(x) < 2:
        return None
    rx, ry = _rank(x), _rank(y)
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def _component_sizes(members: set[Child], links: list[tuple[Child, Child]]) -> list[int]:
    """Connected-component sizes of the undirected induced subgraph, largest first."""
    parent: dict[Child, Child] = {m: m for m in members}

    def find(c: Child) -> Child:
        root = c
        while parent[root] != root:
            root = parent[root]
        while parent[c] != root:
            parent[c], c = root, parent[c]
        return root

    for a, b in links:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    sizes: dict[Child, int] = defaultdict(int)
    for m in members:
        sizes[find(m)] += 1
    return sorted(sizes.values(), reverse=True)


def _all_dirs(graph: Graph) -> set[tuple[str, ...]]:
    """Every directory containing at least one file, root excluded."""
    out: set[tuple[str, ...]] = set()
    for node in graph.nodes:
        d = dirs(node.id)
        for k in range(1, len(d) + 1):
            out.add(d[:k])
    return out


def _bucket(costs: Iterator[int]) -> dict[str, Any]:
    counts = dict.fromkeys(_COST_BUCKETS, 0)
    total = 0
    for c in costs:
        counts[_COST_BUCKETS[min(c, 3)]] += 1
        total += 1
    return {
        "edges": total,
        "counts": counts,
        "fractions": {b: _ratio(counts[b], total) for b in _COST_BUCKETS},
    }


def _rank(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="stable")
    ordered = values[order]
    ranks = np.empty(len(values), dtype=float)
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def _ratio(numerator: float, denominator: int) -> float | None:
    return numerator / denominator if denominator else None
