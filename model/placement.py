"""Local optimality: for each file, would moving it anywhere else pay?

plan.md's PR 4c, and the demanding version of the Phase 0 question. The
permutation baseline asks whether the real layout beats a shuffle; random is a
weak opponent, and a repo can beat one decisively while still having a third of
its files in the wrong directory. This asks instead: holding every other file
where it is, does any *single* file want to move?

The answer is also the artifact the shipped tool would emit -- a score plus a
short list of high-confidence moves -- so this doubles as a prototype of the
advisory output.

Two properties make the C sweep in report/calibrate.py cheap enough to be a
sweep at all:

- **The objective splits along C.** A move's effect is
  `delta = delta_edges - C * containers_removed`, and only the second term
  involves C. Destinations are *existing* directories, so a move can only empty
  containers, never create them. move_frontier() therefore evaluates each
  candidate once and records, per file, the cheapest move for each number of
  containers it would empty; every value of C afterwards is a lookup over a
  handful of numbers.
- **The edge term is a sum over containers, not over edges.** Since bit_cost
  charges one log2(branching) per container on an edge's descent,
  `Sigma_e bit_cost(e) = Sigma_D charge[D] * log2(branching[D])`, where charge[D]
  is the number of edges making a selection in D. Moving one file changes
  branching at only a handful of containers, so the edges *not* incident to it
  are repriced by a dot product over those containers rather than by being
  visited. Only the moved file's own edges are recomputed one by one.

Frozen paths (plan.md's escape hatch 2) enter here rather than in report/run.py's
measurement pass for a reason: 4b measures the layout as it stands, where nothing
moves and "can this move?" is never asked. Local optimality is --freeze's first
real consumer.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass
from typing import Any

import numpy as np

from extractors.schema import Graph
from model.graph import matches_any
from model.metrics import DEFAULT_C, charge_counts, edges
from model.paths import Child, branching, child_of, common_prefix_len, dirs

# A move must beat staying put by more than floating-point noise to count. The
# deltas are sums of log2 terms that cancel exactly in exact arithmetic (see
# split-neutrality in plan.md), so genuine zeros routinely arrive as +-1e-16.
_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class Move:
    """One candidate relocation, split into the two terms of the objective.

    `destination` is the target directory as a POSIX path, "" for the repo root.
    `containers_removed` is how many directories the move would empty out, which
    is the only part of the delta that depends on C -- hence the split.
    """

    file: str
    destination: str
    delta_edges: float
    containers_removed: int

    def delta(self, c: float) -> float:
        """Change in the total objective at this C. Negative means the move pays."""
        return self.delta_edges - c * self.containers_removed


@dataclass(frozen=True, slots=True)
class Split:
    """One candidate subdirectory: which children move down, and what it costs.

    Exactly one container appears, so the proposal pays while `C < -bits` --
    no group count to divide by.
    """

    members: tuple[str, ...]
    bits: float
    kind: str

    def delta(self, c: float) -> float:
        """Change in the total objective at this C. Negative means it pays."""
        return self.bits + c


def move_frontier(graph: Graph, freeze: Sequence[str] = ()) -> dict[str, tuple[Move, ...]]:
    """Per movable file, the cheapest move for each number of containers it empties.

    Keys are every file not matched by `freeze`; the value is that file's
    C-independent frontier, so `min(moves, key=lambda m: m.delta(c))` answers
    "where does this file want to go at this C" for any C without re-searching.

    Candidate destinations are every existing directory (the root included) that
    is not frozen and does not already hold a file of the same name. New
    directories are deliberately not candidates: this measures whether the
    current layout is stable under single-file moves, not what an optimizer
    would build from scratch.
    """
    tree = branching(graph)
    lg = {d: math.log2(b) for d, b in tree.items()}
    charge = charge_counts(graph)
    out_targets, in_sources = _incident(graph)
    frozen_files, frozen_dirs = freeze_sets(graph, freeze)
    taken = {(dirs(n.id), n.id.rsplit("/", 1)[-1]) for n in graph.nodes}
    destinations = tuple(d for d in tree if d not in frozen_dirs)

    frontier: dict[str, tuple[Move, ...]] = {}
    for f in (n.id for n in graph.nodes):
        if f in frozen_files:
            continue
        old_dir = dirs(f)
        name = f.rsplit("/", 1)[-1]
        out_dirs = [dirs(t) for t in out_targets[f]]
        in_dirs = [dirs(s) for s in in_sources[f]]

        # This file's own share of every charge count, under the current tree.
        # Subtracting it from `charge` leaves the edges whose descent paths the
        # move does not touch, which are exactly the ones repriced in bulk.
        own: Counter[tuple[str, ...]] = Counter()
        for dt in out_dirs:
            for i in range(common_prefix_len(old_dir, dt), len(dt) + 1):
                own[dt[:i]] += 1
        for ds in in_dirs:
            for i in range(common_prefix_len(ds, old_dir), len(old_dir) + 1):
                own[old_dir[:i]] += 1
        old_cost = sum(count * lg[d] for d, count in own.items())

        best: dict[int, Move] = {}
        for dest in destinations:
            if dest == old_dir or (dest, name) in taken:
                continue
            new_lg, emptied = _rebranch(tree, old_dir, dest)

            # Edges not incident to f: same descent paths, repriced containers.
            delta = 0.0
            for d, bits in new_lg.items():
                if d in emptied:
                    continue
                rest = charge.get(d, 0) - own.get(d, 0)
                if rest:
                    delta += rest * (bits - lg[d])

            # Edges incident to f: new descent paths, recomputed one by one.
            new_cost = 0.0
            for dt in out_dirs:
                for i in range(common_prefix_len(dest, dt), len(dt) + 1):
                    d = dt[:i]
                    new_cost += new_lg[d] if d in new_lg else lg[d]
            if in_dirs:
                suffix = _suffix_sums(dest, new_lg, lg)
                for ds in in_dirs:
                    new_cost += suffix[common_prefix_len(ds, dest)]
            delta += new_cost - old_cost

            removed = len(emptied)
            incumbent = best.get(removed)
            if incumbent is None or delta < incumbent.delta_edges:
                best[removed] = Move(f, "/".join(dest), delta, removed)
        frontier[f] = tuple(best[k] for k in sorted(best))
    return frontier


def local_optimality(
    graph: Graph,
    c: float = DEFAULT_C,
    freeze: Sequence[str] = (),
    frontier: dict[str, tuple[Move, ...]] | None = None,
) -> dict[str, Any]:
    """What fraction of files is already where the objective wants them, at this C.

    Pass `frontier` to reuse one move_frontier() call across several values of C
    -- the frontier does not depend on C, and computing it is the entire cost.

    A file with no legal destination (nowhere to go, or every directory frozen)
    counts as locally optimal: it is not evidence against the layout.
    """
    if frontier is None:
        frontier = move_frontier(graph, freeze)
    movers = []
    for moves in frontier.values():
        if not moves:
            continue
        best = min(moves, key=lambda m: m.delta(c))
        if best.delta(c) < -_EPSILON:
            movers.append(best)
    movers.sort(key=lambda m: (m.delta(c), m.file))
    considered = len(frontier)
    return {
        "c": c,
        "files_considered": considered,
        "frozen_files": len(graph.nodes) - considered,
        "locally_optimal": considered - len(movers),
        "fraction_locally_optimal": (considered - len(movers)) / considered if considered else None,
        "movers": [
            {
                "file": m.file,
                "destination": m.destination,
                "delta": m.delta(c),
                "delta_edges": m.delta_edges,
                "containers_removed": m.containers_removed,
            }
            for m in movers
        ],
    }


def sweep(
    graph: Graph, cs: Iterable[float], freeze: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """local_optimality() at each C, off a single move_frontier() call."""
    frontier = move_frontier(graph, freeze)
    return [local_optimality(graph, c, freeze, frontier) for c in cs]


# --- container stability ----------------------------------------------------
#
# Single-file moves cannot calibrate C, and not for want of data: destinations
# are existing directories, so a move can only ever *empty* containers. Its
# delta is `delta_edges - C * containers_removed` with containers_removed >= 0,
# every term is non-increasing in C, and so is the minimum over destinations.
# Once a file wants to move it wants to move at every larger C, which makes the
# locally-optimal fraction monotone non-increasing and its maximum always C = 0.
# The sweep plan.md specifies is therefore flat *by construction*, and reading
# that flatness as "the structure term is inert" -- plan.md's third outcome --
# would be wrong.
#
# What C actually arbitrates is a question about containers, and it is two-
# sided in principle: too large and a directory would rather dissolve into its
# parent, too small and it would rather split. Both are priced below in the
# objective's own units.
#
# Measured, both sides bind, but the bounds they set disagree across repos by
# 160x -- zod does not reach peak stability until C >= 8.4, vite has left its by
# C ~ 0.2. So C is a per-repo knob. That is PR 4c's answer; see plan.md.
#
# An earlier version concluded the split side never binds, which was an artifact
# of only ever offering the zero-cut partition into connected components: that
# proposes nothing for an internally connected directory, which is the majority
# case and includes every large split candidate in the corpus. best_split now
# searches spectral cuts too.


@dataclass(frozen=True, slots=True)
class Container:
    """One directory, priced against the two ways the objective can question it.

    `dissolve_bits` is the edge bits the directory saves by existing: dissolving
    it into its parent changes the edge term by -dissolve_bits and the structure
    term by -C, so it survives exactly while C <= dissolve_bits.

    `splits` holds every candidate subdirectory that pays at some C, cheapest
    first, each naming the children that would move down -- so a report can say
    *which files* belong in a subdirectory rather than only that some do.
    Several are kept on purpose: both sides of a cut are usually real
    proposals, and deciding which one a human would accept is a naming
    question, not a bit-counting one.
    """

    dir: str
    children: int
    components: int
    internal_edges: int
    external_entries: int
    dissolve_bits: float
    splits: tuple[Split, ...]

    @property
    def verdict(self) -> str:
        """`earns` / `neutral` / `costs` -- the C-free reading of dissolve_bits.

        The three are qualitatively different findings and collapsing them loses
        the only actionable one:

        - **earns** -- dissolving this directory would make addressing more
          expensive. It is buying encapsulation. Keep it for any C below what it
          earns.
        - **neutral** -- dissolving it changes the edge term by exactly nothing.
          Almost always a pass-through: the directory has one child, or it is its
          parent's only child, so it partitions nothing and log2 telescopes
          (`log2(k) = log2(m) + log2(k/m)`, plan.md's split-neutrality). The
          dependency graph has no opinion on these, in either direction. C alone
          decides them, which is why C's *sign* matters and its magnitude does
          not.
        - **costs** -- dissolving it would make addressing strictly cheaper. No
          value of C saves it. This is the actionable set, and it is small: 2-10%
          of directories across the reference corpus.
        """
        if self.dissolve_bits > _EPSILON:
            return "earns"
        return "neutral" if self.dissolve_bits >= -_EPSILON else "costs"

    @property
    def c_max(self) -> float:
        """Largest C at which this directory is still worth keeping."""
        return self.dissolve_bits

    @property
    def split_bits(self) -> float:
        """Edge-bit change of the cheapest candidate subdirectory, 0.0 if none."""
        return self.splits[0].bits if self.splits else 0.0

    @property
    def c_min(self) -> float:
        """Smallest C at which this directory resists gaining a subdirectory.

        Extracting a subset creates exactly one container, so a split pays while
        `C < -bits` -- no division by a group count, because there is only ever
        one new directory.
        """
        return max(0.0, -self.split_bits)

    def stable(self, c: float) -> bool:
        return self.c_min - _EPSILON <= c <= self.c_max + _EPSILON


def containers(graph: Graph, freeze: Sequence[str] = ()) -> list[Container]:
    """Every non-root directory, priced for dissolution and for splitting.

    Frozen directories are skipped: their shape is declared rather than derived,
    so they are not evidence either way about C. The root is skipped because it
    has no parent to dissolve into and exists in every candidate layout.
    """
    tree = branching(graph)
    charge = charge_counts(graph)
    _, frozen_dirs = freeze_sets(graph, freeze)
    children, internal, external = _container_edges(graph)

    out: list[Container] = []
    for d in sorted(tree):
        if not d or d in frozen_dirs:
            continue
        k = tree[d]
        parent = d[:-1]
        p = tree[parent]
        # Dissolving d moves its k children up into parent, which grows from p
        # to p - 1 + k. Edges that entered d stop paying for it; edges whose LCA
        # was d now make their selection in parent instead.
        lca_here = len(internal[d])
        b = charge.get(parent, 0)
        dissolve = (
            (b + lca_here) * math.log2(p - 1 + k)
            - b * math.log2(p)
            - charge.get(d, 0) * math.log2(k)
        )
        out.append(
            Container(
                dir="/".join(d),
                children=k,
                dissolve_bits=dissolve,
                **split_candidates(children[d], internal[d], external[d], k),
            )
        )
    return out


def container_stability(
    graph: Graph,
    c: float = DEFAULT_C,
    freeze: Sequence[str] = (),
    census: list[Container] | None = None,
) -> dict[str, Any]:
    """What fraction of directories the objective would leave alone at this C.

    wants_split and wants_dissolve are diagnostics, not a partition: a container
    whose c_min exceeds its c_max is in both at once, because it would rather
    split than stay at any C small enough to keep it and would rather dissolve
    at any C large enough to stop the split. Those are counted separately as
    never_stable -- no value of C rescues them, so they are not evidence about
    C, and a calibration that read them as "C is too small here" would be
    chasing directories the parameter cannot reach.
    """
    if census is None:
        census = containers(graph, freeze)
    stable = [x for x in census if x.stable(c)]
    wants_split = [x for x in census if c < x.c_min - _EPSILON]
    wants_dissolve = [x for x in census if c > x.c_max + _EPSILON]
    never = [x for x in census if x.c_min > x.c_max + _EPSILON]
    verdicts = Counter(x.verdict for x in census)
    return {
        "c": c,
        "containers": len(census),
        "stable": len(stable),
        "fraction_stable": len(stable) / len(census) if census else None,
        "unstable": len(census) - len(stable),
        "wants_split": len(wants_split),
        "wants_dissolve": len(wants_dissolve),
        "never_stable": len(never),
        # C-free, so identical at every C in a sweep. Reported alongside anyway:
        # `costs` is the actionable set and the reader should see it next to the
        # C-dependent numbers rather than have to go find it.
        "earns": verdicts["earns"],
        "neutral": verdicts["neutral"],
        "costs": verdicts["costs"],
        "costs_fraction": verdicts["costs"] / len(census) if census else None,
    }


def stability_sweep(
    graph: Graph, cs: Iterable[float], freeze: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """container_stability() at each C, off a single census."""
    census = containers(graph, freeze)
    return [container_stability(graph, c, freeze, census) for c in cs]


def _container_edges(
    graph: Graph,
) -> tuple[
    dict[tuple[str, ...], set[Child]],
    dict[tuple[str, ...], list[tuple[Child, Child]]],
    dict[tuple[str, ...], Counter[Child]],
]:
    """Per container: its direct children, the edges whose LCA it is, and the
    edges reaching into it from outside, counted per child they land in.

    The three populations are exactly the ones the split table in plan.md
    distinguishes, and they partition the edges that charge the container: an
    edge charges D either because D is its LCA or because it enters D from
    outside.
    """
    children: dict[tuple[str, ...], set[Child]] = defaultdict(set)
    for node in graph.nodes:
        d = dirs(node.id)
        for k in range(len(d) + 1):
            children[d[:k]].add(child_of(d[:k], node.id))

    internal: dict[tuple[str, ...], list[tuple[Child, Child]]] = defaultdict(list)
    external: dict[tuple[str, ...], Counter[Child]] = defaultdict(Counter)
    for u, v, _ in edges(graph):
        du, dv = dirs(u), dirs(v)
        k = common_prefix_len(du, dv)
        internal[dv[:k]].append((child_of(dv[:k], u), child_of(dv[:k], v)))
        for i in range(k + 1, len(dv) + 1):
            external[dv[:i]][child_of(dv[:i], v)] += 1
    return children, internal, external


def extract_bits(
    subset: AbstractSet[Child],
    links: list[tuple[Child, Child]],
    entries: Counter[Child],
    k: int,
) -> float:
    """Exact edge-bit change from moving `subset` into a new subdirectory of a
    container that currently holds k children.

    This is the "these files belong in a subdirectory" operation, and it is not
    the same as partitioning the directory into m groups. The remainder keeps
    living in the container itself rather than moving into a group of its own,
    so the container ends up with `k - s + 1` children and **exactly one new
    container is created** -- the structure term costs C once, not C*m. A
    partition into m groups is this operation applied m-1 times, and pricing it
    as a partition overcharges every split by up to C*(m-1).

    After the move a selection of size k becomes: `s` inside the new directory
    (when the container was the edge's LCA and both endpoints went into the
    subset -- the LCA moves down with them), `k - s + 1` when the target stayed
    behind, and both when the target moved but the edge's LCA did not.
    """
    s = len(subset)
    if not 1 <= s <= k - 1:
        return 0.0
    # s == 1 falls out as exactly 0.0 rather than being special-cased: the
    # remainder is k - 1 + 1 = k, so the parent swaps a file child for a
    # directory child and every term below is log2(k * 1 / k).
    remainder = k - s + 1
    bits = 0.0
    for child, count in entries.items():
        bits += count * math.log2(remainder * (s if child in subset else 1) / k)
    for cu, cv in links:
        if cv not in subset:
            bits += math.log2(remainder / k)
        elif cu in subset:
            bits += math.log2(s / k)
        else:
            bits += math.log2(remainder * s / k)
    return bits


def split_candidates(
    members: set[Child], links: list[tuple[Child, Child]], entries: Counter[Child], k: int
) -> dict[str, Any]:
    """The cheapest subdirectory this container could gain, among candidates searched.

    The objective is its own scoring function, so the only question is which
    subsets to put in front of it. Four, all deterministic and parameter-free:
    each connected component of the child subgraph, and each side of the
    Fiedler vector cut at zero and at its median.

    The Fiedler candidates are the ones that matter. Components only offer a
    subset when the directory is *already* internally disconnected, which in
    practice means it is a taxonomy rather than a split candidate, and they
    offer nothing at all for the case anyone would call a missing subdirectory:
    a large connected directory with a dense sub-cluster. vite's `node` -- 30
    children, 585 internal edges, one component -- is the clearest example in
    the corpus, and a components-only search proposes nothing for it and then
    reports it as unsplittable.

    Both ends of each cut are tried because they are not equivalent under this
    operation: extracting the dense half and extracting the sparse half create
    different trees at different prices.

    Subsets smaller than 2 are skipped. A subdirectory holding one file has
    branching 1, carries zero addressing information by construction, and is the
    exact thing container_information() reports as pure C overhead in date-fns;
    a "split" proposing one is really proposing to evict a single hot file into
    a box to shorten its address, which is a move recommendation in a split's
    clothing. Without the guard the sign cut peels one file off nearly every
    large directory -- 397 + 1 for date-fns's `fp`, 29 + 1 for vite's `node`.
    """
    components = _component_partition(members, links)
    fiedler = _fiedler(members, links)
    median = _median(fiedler)
    candidates: list[tuple[str, set[Child]]] = [
        (f"component-{g}", {c for c in members if components[c] == g})
        for g in sorted(set(components.values()))
    ]
    for name, at in (("bisection", 0.0), ("median-bisection", median)):
        low = {c for c in members if fiedler[c] <= at}
        candidates += [(name, low), (name, members - low)]

    found: dict[frozenset[Child], Split] = {}
    for name, subset in candidates:
        # No shape constraints. A single child prices at exactly 0.0 -- the
        # parent swaps a file for a directory, so its branching is unchanged,
        # and the new directory's log2(1) is zero -- so it is +C and never pays,
        # without needing to be excluded. And a *majority* subset is a perfectly
        # real proposal: burying 382 cold files so 16 hot ones sit at the top is
        # the same tree as promoting the 16, and which phrasing is better advice
        # is not something the bit count knows. Both sides of every cut are
        # offered and ranked; choosing among them is a judgement, and judgement
        # is what the naming pass is for.
        if not subset or len(subset) > k - 1:
            continue
        bits = extract_bits(subset, links, entries, k)
        if bits >= -_EPSILON:
            continue
        key = frozenset(subset)
        if key not in found or bits < found[key].bits:
            found[key] = Split(tuple(sorted(_label(c) for c in subset)), bits, name)
    ranked = tuple(sorted(found.values(), key=lambda s: (s.bits, s.members)))
    return {
        "components": len(set(components.values())),
        "internal_edges": len(links),
        "external_entries": sum(entries.values()),
        "splits": ranked,
    }


def _label(child: Child) -> str:
    """A child as a path: its own id for a file, the directory path for a subtree.

    The trailing slash is not decoration -- it is what keeps a file `a/b` and a
    subdirectory `a/b/` distinct as dictionary keys.
    """
    _, value = child
    return value if isinstance(value, str) else "/".join(value) + "/"


def _component_partition(
    members: set[Child], links: list[tuple[Child, Child]]
) -> dict[Child, int]:
    roots = _components(members, links)
    index = {r: i for i, r in enumerate(sorted(set(roots.values()), key=str))}
    return {c: index[r] for c, r in roots.items()}


def _fiedler(members: set[Child], links: list[tuple[Child, Child]]) -> dict[Child, float]:
    """The child graph's Fiedler vector -- the standard min-ratio-cut relaxation.

    The eigenvector of the second-smallest Laplacian eigenvalue orders the
    children so that cutting the sequence anywhere separates two loosely coupled
    halves; where to cut is left to the caller, since the sign and the median
    give different and both-worth-trying answers. Directions are dropped: for
    "what should live together", an import is an association whichever way it
    points.

    All-zero when there is nothing to cut (fewer than two children, or no
    internal edges), which yields a single group and so no candidate.
    """
    order = sorted(members, key=str)
    n = len(order)
    if n < 2 or not links:
        return dict.fromkeys(order, 0.0)
    index = {c: i for i, c in enumerate(order)}
    adjacency = np.zeros((n, n))
    for cu, cv in links:
        i, j = index[cu], index[cv]
        adjacency[i, j] += 1
        adjacency[j, i] += 1
    laplacian = np.diag(adjacency.sum(1)) - adjacency
    vector = np.linalg.eigh(laplacian)[1][:, 1]
    return {c: float(vector[index[c]]) for c in order}


def _cut(members: set[Child], fiedler: Mapping[Child, float], at: float) -> dict[Child, int]:
    """Two groups, split at `at` along the Fiedler ordering."""
    return {c: int(fiedler[c] > at) for c in sorted(members, key=str)}


def _median(fiedler: Mapping[Child, float]) -> float:
    """Median of the Fiedler vector -- the balanced cut, as against the sign cut."""
    values = sorted(fiedler.values())
    if not values:
        return 0.0
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def _components(members: set[Child], links: list[tuple[Child, Child]]) -> dict[Child, Child]:
    """Union-find over a container's children; returns child -> component root."""
    parent = {m: m for m in members}

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
    return {m: find(m) for m in members}


def freeze_sets(
    graph: Graph, freeze: Sequence[str]
) -> tuple[frozenset[str], frozenset[tuple[str, ...]]]:
    """(frozen files, frozen directories) for a set of --freeze globs.

    A directory is frozen when *every* file beneath it is, which contracts
    `--freeze 'src/routes/**'` back to the subtree the user meant: `src/routes`
    and everything under it is closed to arrivals, while `src` -- which also
    holds ordinary code -- stays open. Defining it as "contains a frozen file"
    instead would freeze the root the moment anything anywhere was frozen.

    Frozen files neither move nor receive; their edges and their branching stay
    in the objective, which is the entire difference from --exclude.
    """
    if not freeze:
        return frozenset(), frozenset()
    frozen = {n.id for n in graph.nodes if matches_any(n.id, freeze)}
    total: Counter[tuple[str, ...]] = Counter()
    locked: Counter[tuple[str, ...]] = Counter()
    for node in graph.nodes:
        d = dirs(node.id)
        for k in range(len(d) + 1):
            total[d[:k]] += 1
            if node.id in frozen:
                locked[d[:k]] += 1
    return frozenset(frozen), frozenset(d for d, n in total.items() if locked[d] == n)


def _incident(graph: Graph) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(targets of, sources into) each file."""
    out: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    into: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    for u, v, _ in edges(graph):
        out[u].append(v)
        into[v].append(u)
    return out, into


def _rebranch(
    tree: dict[tuple[str, ...], int],
    old_dir: tuple[str, ...],
    dest: tuple[str, ...],
) -> tuple[dict[tuple[str, ...], float], set[tuple[str, ...]]]:
    """Containers whose log2(branching) the move changes, and those it empties.

    The arrival is applied before the departure cascades, which is what keeps a
    destination that is also an ancestor of the source honest: moving `a/b/c/f`
    to `a` empties `a/b/c` and `a/b`, but `a` itself survives because it has just
    gained a file. Applying the cascade first would delete `a` and then resurrect
    it.

    Emptied containers get a placeholder 0.0 rather than log2(0): nothing but the
    moved file was ever inside them, so no surviving edge reads their value.
    """
    counts = {dest: tree[dest] + 1}
    emptied: set[tuple[str, ...]] = set()
    d = old_dir
    while True:
        counts[d] = (counts[d] if d in counts else tree[d]) - 1
        if counts[d] > 0 or not d:
            break
        emptied.add(d)
        d = d[:-1]
    new_lg = {d: (math.log2(n) if n > 0 else 0.0) for d, n in counts.items()}
    return new_lg, emptied


def _suffix_sums(
    dest: tuple[str, ...],
    new_lg: dict[tuple[str, ...], float],
    lg: dict[tuple[str, ...], float],
) -> list[float]:
    """suffix[k] = bits to name a file in `dest` from an importer whose LCA is at depth k."""
    suffix = [0.0] * (len(dest) + 2)
    for i in range(len(dest), -1, -1):
        d = dest[:i]
        suffix[i] = suffix[i + 1] + (new_lg[d] if d in new_lg else lg[d])
    return suffix
