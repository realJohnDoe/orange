"""Pure transforms over a loaded Graph.

Every function here takes a Graph and returns a new one; nothing mutates, so a
report run can compose variants (spliced vs. unspliced, tests included vs.
excluded) off a single cached extraction. This is the "extract once, filter in
analysis" half of the plan's extractor contract -- the extractors record what
the source says, these transforms decide what a given measurement counts.

The four transforms answer four different questions:

- filter_nodes  -- which files should this measurement see at all?
- reroot -- where does the analyzed system actually begin?
- splice_barrels -- what does an import through a re-export file really reach?
- value_edges_only -- does the picture change if types don't count as coupling?
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import PurePosixPath

from extractors.schema import Graph, Node
from model.paths import dirs


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


def reroot(graph: Graph) -> Graph:
    """Strip the directory prefix every file shares, so the root is where the code is.

    Node ids are checkout-relative, so a repo analyzed at `packages/zod/src`
    carries that prefix on all 286 of them. Those levels are not a decision the
    repo made about where to put files -- they are where the analyzed subtree
    happens to begin -- and by construction each has exactly one child, so under
    the bit cost they carry zero addressing information.

    Leaving them in is not harmless, because they make the *root* nearly empty.
    A root of branching 1 costs log2(2) = 1 bit to add a file to, so it is
    nearly-free parking, and model.placement duly recommends hoisting every
    widely-shared file into it -- 93 of zod's 123 movers before this transform
    existed. That is an artifact of where the checkout was rooted, not a finding.

    The longest common prefix is exactly the maximal single-child chain from the
    root, so this removes those levels and nothing else; edge bits are unchanged
    (log2(1) = 0 at every stripped level) and only the container count and the
    root's branching move.
    """
    if not graph.nodes:
        return graph
    prefix = dirs(graph.nodes[0].id)
    for node in graph.nodes[1:]:
        d = dirs(node.id)
        n = 0
        while n < min(len(prefix), len(d)) and prefix[n] == d[n]:
            n += 1
        prefix = prefix[:n]
        if not prefix:
            return graph
    cut = len("/".join(prefix)) + 1
    moved = {n.id: n.id[cut:] for n in graph.nodes}
    nodes = tuple(
        replace(
            n,
            id=moved[n.id],
            imports=tuple(moved[t] for t in n.imports),
            type_only=tuple(moved[t] for t in n.type_only),
        )
        for n in graph.nodes
    )
    return replace(graph, nodes=nodes, roots=(".",))


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
