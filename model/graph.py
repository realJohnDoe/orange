"""Pure transforms over a loaded Graph.

Every function here takes a Graph and returns a new one; nothing mutates, so a
report run can compose variants (spliced vs. unspliced, tests included vs.
excluded) off a single cached extraction. This is the "extract once, filter in
analysis" half of the plan's extractor contract -- the extractors record what
the source says, these transforms decide what a given measurement counts.

The three transforms answer three different questions:

- filter_nodes  -- which files should this measurement see at all?
- splice_barrels -- what does an import through a re-export file really reach?
- value_edges_only -- does the picture change if types don't count as coupling?
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import PurePosixPath

from extractors.schema import Graph, Node


def filter_nodes(graph: Graph, exclude: Sequence[str]) -> Graph:
    """Drop nodes whose id matches any glob in exclude, and every edge into them.

    Globs are matched with PurePosixPath.full_match (3.13+), so '**' spans
    directory separators: '**/*.test.ts', 'src/**/__tests__/**'.

    Dropping the incoming edges is what makes this safe: Graph rejects an edge
    pointing at a node that doesn't exist, so a filter that only removed nodes
    would raise rather than silently produce a broken graph.
    """
    if not exclude:
        return graph
    dropped = {n.id for n in graph.nodes if matches_any(n.id, exclude)}
    if not dropped:
        return graph
    nodes = tuple(
        replace(
            n,
            imports=tuple(t for t in n.imports if t not in dropped),
            type_only=tuple(t for t in n.type_only if t not in dropped),
        )
        for n in graph.nodes
        if n.id not in dropped
    )
    return replace(graph, nodes=nodes)


def splice_barrels(graph: Graph) -> Graph:
    """Rewire every edge through a barrel to what the barrel actually re-exports.

    Without this, `a -> @/feature/index.ts -> b` records the cheap distance to
    the barrel's face instead of the real descent to b, and the barrel becomes an
    artificial hub with enormous fan-in (see "Barrel handling" in plan.md).

    Splicing is transitive (barrels re-export barrels) and cycle-guarded: a
    barrel cycle contributes nothing rather than recursing forever. Barrel nodes
    are removed from the result, which makes the transform idempotent -- a second
    call finds no barrels and returns the graph unchanged.

    Type-only propagation: a spliced edge is type-only when every path from the
    importer to the target is type-only somewhere along it. One all-value path is
    a real value dependency, so the merged edge is a value edge.
    """
    barrels = {n.id for n in graph.nodes if n.is_barrel}
    if not barrels:
        return graph

    by_id = {n.id: n for n in graph.nodes}
    memo: dict[str, dict[str, bool]] = {}

    def resolve(barrel_id: str, stack: set[str]) -> tuple[dict[str, bool], bool]:
        """Non-barrel targets reachable through barrel_id -> is the edge type-only.

        Returns (targets, hit_cycle). A result computed while an ancestor was on
        the stack may be missing whatever the cycle cut off, so it is only
        memoized when no cycle was hit.
        """
        cached = memo.get(barrel_id)
        if cached is not None:
            return cached, False
        if barrel_id in stack:
            return {}, True
        stack.add(barrel_id)
        out: dict[str, bool] = {}
        hit_cycle = False
        node = by_id[barrel_id]
        type_only = set(node.type_only)
        for target in node.imports:
            edge_is_type_only = target in type_only
            if target in barrels:
                reached, cycled = resolve(target, stack)
                hit_cycle = hit_cycle or cycled
                for final, final_is_type_only in reached.items():
                    _merge(out, final, edge_is_type_only or final_is_type_only)
            else:
                _merge(out, target, edge_is_type_only)
        stack.discard(barrel_id)
        if not hit_cycle:
            memo[barrel_id] = out
        return out, hit_cycle

    nodes: list[Node] = []
    for node in graph.nodes:
        if node.is_barrel:
            continue
        out: dict[str, bool] = {}
        type_only = set(node.type_only)
        for target in node.imports:
            edge_is_type_only = target in type_only
            if target in barrels:
                reached, _ = resolve(target, set())
                for final, final_is_type_only in reached.items():
                    # A barrel re-exporting its own importer would otherwise
                    # splice into a self-edge, which is not a dependency.
                    if final == node.id:
                        continue
                    _merge(out, final, edge_is_type_only or final_is_type_only)
            else:
                _merge(out, target, edge_is_type_only)
        imports = tuple(sorted(out))
        nodes.append(
            replace(
                node,
                imports=imports,
                type_only=tuple(t for t in imports if out[t]),
            )
        )
    return replace(graph, nodes=tuple(nodes))


def value_edges_only(graph: Graph) -> Graph:
    """Drop type-only edges, for the type-vs-value diagnostic.

    plan.md weights type imports the same as any other import; this exists to
    measure how much that choice costs, not to change it.
    """
    nodes = tuple(
        replace(
            n,
            imports=tuple(t for t in n.imports if t not in set(n.type_only)),
            type_only=(),
        )
        for n in graph.nodes
    )
    return replace(graph, nodes=nodes)


def matches_any(node_id: str, patterns: Sequence[str]) -> bool:
    """Does this node id match any of these globs? Shared by --exclude and --freeze.

    Public because model.placement needs the identical matching semantics: the
    two flags select different things to *do* with a file, not different ways of
    naming it (plan.md, PR 4c).
    """
    path = PurePosixPath(node_id)
    return any(path.full_match(p) for p in patterns)


def _merge(out: dict[str, bool], target: str, is_type_only: bool) -> None:
    out[target] = out.get(target, True) and is_type_only
