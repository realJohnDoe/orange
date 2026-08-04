"""Local optimality and container stability, checked against brute force.

The incremental machinery in model/placement.py is the whole point of the
module -- it reprices a move without touching the edges the move does not
affect -- so most of these tests are differential: apply the move for real,
recompute the objective from scratch with model.metrics, and demand the
analytic delta match to floating-point precision. A hand-derived expected
number would only pin one case; the oracle pins every case in the fixture and,
for the small repos, every case in the corpus.

`apply_move`, `dissolve` and `split` below build the rearranged graph the naive
way. They are test-only on purpose: nothing in the shipped code ever needs to
materialize a candidate layout, which is exactly what makes the sweep cheap.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import replace

import pytest

from extractors.schema import Graph
from model.metrics import charge_counts, container_information, edges, total_bit_cost
from model.paths import bit_cost, branching, child_of, dirs
from model.placement import (
    Move,
    _container_edges,
    _label,
    container_stability,
    containers,
    freeze_sets,
    local_optimality,
    move_frontier,
    extract_bits,
    stability_sweep,
    sweep,
)
from tests.fixtures.graphs import graph, node, plan_md_tree, two_clusters

# --- test-only oracles ------------------------------------------------------


def _rename(graph_: Graph, mapping: dict[str, str]) -> Graph:
    """Apply an id rewrite, disambiguating any basename it would collide.

    The cost depends only on the shape of the tree, so a `~` prefix on a
    colliding filename preserves every branching factor while keeping the graph
    schema-valid.
    """
    used = {n.id for n in graph_.nodes if n.id not in mapping}
    for src in sorted(mapping):
        dst = mapping[src]
        while dst in used:
            head, _, tail = dst.rpartition("/")
            dst = f"{head}/~{tail}" if head else f"~{tail}"
        mapping[src] = dst
        used.add(dst)
    nodes = tuple(
        replace(
            n,
            id=mapping.get(n.id, n.id),
            imports=tuple(mapping.get(t, t) for t in n.imports),
            type_only=tuple(mapping.get(t, t) for t in n.type_only),
        )
        for n in graph_.nodes
    )
    return replace(graph_, nodes=nodes)


def apply_move(graph_: Graph, file: str, destination: str) -> Graph:
    name = file.rsplit("/", 1)[-1]
    return _rename(graph_, {file: f"{destination}/{name}" if destination else name})


def apply_dissolve(graph_: Graph, d: tuple[str, ...]) -> Graph:
    """Move every child of d up into d's parent, keeping subdirectories distinct."""
    parent = d[:-1]
    siblings = {
        dirs(n.id)[len(parent)]
        for n in graph_.nodes
        if dirs(n.id)[: len(parent)] == parent and len(dirs(n.id)) > len(parent)
    }
    renamed: dict[str, str] = {}
    mapping = {}
    for n in graph_.nodes:
        p = dirs(n.id)
        if p[: len(d)] != d:
            continue
        tail = p[len(d) :]
        if tail:
            head = renamed.get(tail[0])
            if head is None:
                head = tail[0]
                while head in siblings:
                    head = "~" + head
                siblings.add(head)
                renamed[tail[0]] = head
            tail = (head, *tail[1:])
        mapping[n.id] = "/".join((*parent, *tail, n.id.rsplit("/", 1)[-1]))
    return _rename(graph_, mapping)


def apply_extract(graph_: Graph, d: tuple[str, ...], subset) -> Graph:
    """Move the children in `subset` into a new subdirectory of d; rest stays put.

    `subset` holds child *labels*, matching Container.split_members -- a file
    child is labelled by its id, a subdirectory child by its path with a
    trailing slash.
    """
    mapping = {}
    for n in graph_.nodes:
        p = dirs(n.id)
        if p[: len(d)] != d or _label(child_of(d, n.id)) not in subset:
            continue
        mapping[n.id] = "/".join((*d, "__extracted", *p[len(d) :], n.id.rsplit("/", 1)[-1]))
    return _rename(graph_, mapping)


def edge_bits(graph_: Graph) -> float:
    return total_bit_cost(graph_, 0.0)["bits"]


def objective(graph_: Graph, c: float) -> float:
    return total_bit_cost(graph_, c)["objective"]


# --- the charge-count identity the whole module rests on ---------------------


@pytest.mark.parametrize("repo", ["zod", "date-fns", "vite", "tanstack-router", "rich"])
def test_charge_counts_reproduce_the_edge_term(corpus_graph, repo: str) -> None:
    g = corpus_graph(repo)
    tree = branching(g)
    by_container = sum(n * math.log2(tree[d]) for d, n in charge_counts(g).items())
    by_edge = sum(bit_cost(u, v, tree) for u, v, _ in edges(g))
    assert by_container == pytest.approx(by_edge, abs=1e-6)


def test_edges_drops_self_edges(corpus_graph) -> None:
    # rich/box.py and rich/live.py import themselves; charging log2(branching)
    # to address a file from inside itself is a selection nobody makes.
    assert [(u, v) for u, v, _ in edges(corpus_graph("rich")) if u == v] == []


# --- move deltas, against the oracle ----------------------------------------


def test_move_delta_matches_a_rebuilt_graph_on_every_fixture_destination() -> None:
    g = plan_md_tree()
    base = objective(g, 8.0)
    for file, moves in move_frontier(g).items():
        for m in moves:
            actual = objective(apply_move(g, file, m.destination), 8.0) - base
            assert m.delta(8.0) == pytest.approx(actual, abs=1e-9)


def test_frontier_is_the_true_minimum_over_every_destination(corpus_graph) -> None:
    # Exhaustive: flask is small enough to price every (file, directory) pair
    # the slow way and confirm the frontier really is the minimum, not just
    # self-consistent.
    g = corpus_graph("flask")
    tree, base = branching(g), objective(g, 8.0)
    taken = {(dirs(n.id), n.id.rsplit("/", 1)[-1]) for n in g.nodes}
    frontier = move_frontier(g)
    for n in g.nodes:
        old_dir, name = dirs(n.id), n.id.rsplit("/", 1)[-1]
        brute = [
            objective(apply_move(g, n.id, "/".join(d)), 8.0) - base
            for d in tree
            if d != old_dir and (d, name) not in taken
        ]
        if brute:
            best = min(m.delta(8.0) for m in frontier[n.id])
            assert best == pytest.approx(min(brute), abs=1e-9)


def test_move_delta_matches_a_rebuilt_graph_on_a_real_repo(corpus_graph) -> None:
    g = corpus_graph("zod")
    base = objective(g, 8.0)
    frontier = move_frontier(g)
    rng = random.Random(0)
    candidates = [m for moves in frontier.values() for m in moves]
    for m in rng.sample(candidates, 40):
        actual = objective(apply_move(g, m.file, m.destination), 8.0) - base
        assert m.delta(8.0) == pytest.approx(actual, abs=1e-9)


def test_a_lone_file_in_its_own_directory_can_empty_it() -> None:
    #   solo/only.ts is the sole occupant of solo/, so moving it out deletes
    #   the directory: one container removed, and the C term with it.
    g = graph(node("root.ts"), node("solo/only.ts", imports=("root.ts",)))
    moves = {m.destination: m for m in move_frontier(g)["solo/only.ts"]}
    assert moves[""].containers_removed == 1


def test_destinations_exclude_a_directory_holding_the_same_filename() -> None:
    g = graph(node("a/dup.ts"), node("b/dup.ts"), node("b/other.ts"))
    assert [m.destination for m in move_frontier(g)["a/dup.ts"]] == [""]


# --- local optimality --------------------------------------------------------


def test_local_optimality_is_monotone_non_increasing_in_c(corpus_graph) -> None:
    # The structural claim behind report/calibrate.py's ONE-SIDED verdict: a
    # move can only ever empty containers, so raising C can never talk a file
    # out of moving.
    fractions = [
        r["fraction_locally_optimal"] for r in sweep(corpus_graph("vite"), [0.0, 1.0, 8.0, 1000.0])
    ]
    assert fractions == sorted(fractions, reverse=True)


def test_local_optimality_counts_a_file_with_nowhere_to_go_as_optimal() -> None:
    g = graph(node("only.ts"))
    result = local_optimality(g, 8.0)
    assert result["files_considered"] == 1
    assert result["fraction_locally_optimal"] == 1.0


def test_movers_carry_their_destination_and_are_worst_first() -> None:
    result = local_optimality(plan_md_tree(), 8.0)
    deltas = [m["delta"] for m in result["movers"]]
    assert deltas == sorted(deltas)
    assert all(m["delta"] < 0 for m in result["movers"])


# --- freeze ------------------------------------------------------------------


def test_freeze_closes_a_fully_frozen_subtree_but_not_the_root() -> None:
    g = graph(
        node("src/app.ts"),
        node("src/routes/index.tsx"),
        node("src/routes/posts/$id.tsx"),
    )
    frozen, closed = freeze_sets(g, ["src/routes/**"])
    assert frozen == {"src/routes/index.tsx", "src/routes/posts/$id.tsx"}
    # src/routes and everything under it is closed; src and the root are not,
    # because they still hold code whose placement is a real question.
    assert ("src", "routes") in closed and ("src", "routes", "posts") in closed
    assert ("src",) not in closed and () not in closed


def test_freeze_removes_files_from_the_frontier_and_directories_from_destinations() -> None:
    g = graph(
        node("src/app.ts", imports=("src/routes/index.tsx",)),
        node("src/routes/index.tsx"),
    )
    frontier = move_frontier(g, freeze=["src/routes/**"])
    assert "src/routes/index.tsx" not in frontier
    assert "src/routes" not in {m.destination for m in frontier["src/app.ts"]}


def test_freezing_everything_leaves_nothing_to_place() -> None:
    g = plan_md_tree()
    assert move_frontier(g, freeze=["**"]) == {}
    assert local_optimality(g, 8.0, freeze=["**"])["frozen_files"] == len(g.nodes)


def test_freeze_keeps_frozen_edges_in_the_cost_unlike_exclude() -> None:
    # The distinction plan.md draws: a util reached only from a frozen subtree
    # still has an importer, so it is still priced against that importer.
    g = graph(
        node("src/util.ts"),
        node("src/routes/index.tsx", imports=("src/util.ts",)),
    )
    assert total_bit_cost(g)["edges"] == 1
    assert container_stability(g, 8.0, freeze=["src/routes/**"])["containers"] == 1


# --- containers: dissolve and split ------------------------------------------


@pytest.mark.parametrize("repo", ["flask", "rich", "tanstack-router"])
def test_dissolve_bits_match_a_rebuilt_graph(corpus_graph, repo: str) -> None:
    g = corpus_graph(repo)
    base = edge_bits(g)
    for c in containers(g):
        d = tuple(c.dir.split("/"))
        assert c.dissolve_bits == pytest.approx(edge_bits(apply_dissolve(g, d)) - base, abs=1e-6)


@pytest.mark.parametrize("repo", ["rich", "zod", "vite", "tanstack-router"])
def test_split_bits_match_a_rebuilt_graph(corpus_graph, repo: str) -> None:
    """The subdirectory the container actually proposes must price exactly."""
    g = corpus_graph(repo)
    base = edge_bits(g)
    checked = 0
    for c in containers(g):
        d = tuple(c.dir.split("/"))
        for split in c.splits:
            rebuilt = edge_bits(apply_extract(g, d, set(split.members))) - base
            assert split.bits == pytest.approx(rebuilt, abs=1e-6)
            checked += 1
    assert checked, f"{repo} proposed no split, so this proves nothing"


@pytest.mark.parametrize("repo", ["rich", "zod", "tanstack-router"])
def test_arbitrary_subsets_price_exactly(corpus_graph, repo: str) -> None:
    """extract_bits is exact for subsets nothing would ever propose.

    best_split only hands over subsets that pay, so pricing those is not on its
    own evidence that the formula is right in general. These are deliberately
    arbitrary -- every other child in sort order -- which exercises the
    cross-boundary terms a connected component never reaches.
    """
    g = corpus_graph(repo)
    base = edge_bits(g)
    members, internal, external = _container_edges(g)
    checked = 0
    for c in containers(g):
        if c.children < 4:
            continue
        d = tuple(c.dir.split("/"))
        subset = {ch for i, ch in enumerate(sorted(members[d], key=str)) if i % 2}
        predicted = extract_bits(subset, internal[d], external[d], c.children)
        rebuilt = edge_bits(apply_extract(g, d, {_label(ch) for ch in subset})) - base
        assert predicted == pytest.approx(rebuilt, abs=1e-6)
        checked += 1
    assert checked, f"{repo} has no container with 4+ children"


def test_verdict_separates_earning_neutral_and_costing_directories(corpus_graph) -> None:
    for repo in ["zod", "vite", "date-fns", "tanstack-router"]:
        census = containers(corpus_graph(repo))
        by_verdict = Counter(c.verdict for c in census)
        assert sum(by_verdict.values()) == len(census)
        # The actionable set is small in every reference repo; if this ever
        # stops being true the tool has become a rewrite proposal, not a linter.
        assert by_verdict["costs"] / len(census) < 0.15
        # And the neutral middle is the biggest or second-biggest group: most
        # directory boundaries are addressing-neutral, which is the ceiling on
        # what the dependency graph can say about placement at all.
        assert by_verdict["neutral"] > 0


def test_neutral_directories_are_pass_throughs(corpus_graph) -> None:
    # log2 telescopes exactly when a container partitions nothing, so a neutral
    # verdict should almost always mean "one child, or its parent's only child".
    g = corpus_graph("date-fns")
    tree = branching(g)
    neutral = [c for c in containers(g) if c.verdict == "neutral"]
    pass_through = [
        c
        for c in neutral
        if c.children == 1 or tree[tuple(c.dir.split("/"))[:-1]] == 1
    ]
    assert len(neutral) > 800
    assert len(pass_through) == len(neutral)


def test_a_container_with_one_component_has_no_split_to_make() -> None:
    g = graph(node("pkg/a.ts", imports=("pkg/b.ts",)), node("pkg/b.ts"), node("root.ts"))
    pkg = next(c for c in containers(g) if c.dir == "pkg")
    assert pkg.components == 1
    assert pkg.split_bits == 0.0
    assert pkg.c_min == 0.0


def test_two_clusters_wants_a_subdirectory_below_its_c_min() -> None:
    #   pkg/ holds {a1,a2}, {b1,b2} and a lonely leaf. The proposal is to move
    #   one connected pair down, not to partition all five -- the leaf stays.
    pkg = next(c for c in containers(two_clusters()) if c.dir == "pkg")
    assert pkg.components == 3
    assert pkg.splits
    assert len(pkg.splits[0].members) == 2
    assert pkg.c_min > 0
    assert not pkg.stable(pkg.c_min / 2)


def test_a_container_is_unstable_above_what_it_earns() -> None:
    g = plan_md_tree()
    for c in containers(g):
        assert c.stable(c.c_max * 0.9 + c.c_min * 0.1) or c.c_min > c.c_max
        assert not c.stable(c.c_max * 2 + 1)


def test_stability_sweep_accounts_for_every_container(corpus_graph) -> None:
    for row in stability_sweep(corpus_graph("vite"), [0.5, 8.0]):
        assert row["stable"] + row["unstable"] == row["containers"]
        # split and dissolve overlap only on containers no C can rescue.
        overlap = row["wants_split"] + row["wants_dissolve"] - row["unstable"]
        assert 0 <= overlap <= row["never_stable"]


# --- container_information: plan.md's named date-fns check --------------------


def test_single_child_containers_carry_zero_addressing_information(corpus_graph) -> None:
    for repo in ["zod", "date-fns", "vite"]:
        assert container_information(corpus_graph(repo))["single_child_bits"] == 0.0


def test_date_fns_is_mostly_single_child_containers_and_zod_vite_are_not(corpus_graph) -> None:
    # plan.md's named falsification check. date-fns's one-function-per-directory
    # convention should show up as containers that carry nothing and cost C;
    # if this ever stops being true of date-fns and false of the others, the
    # bit cost is not measuring what the plan claims.
    date_fns = container_information(corpus_graph("date-fns"))
    assert date_fns["single_child"] > 800
    assert date_fns["single_child_fraction"] > 0.6
    assert date_fns["single_child_share_of_objective"] > 0.15
    for repo in ["zod", "vite"]:
        other = container_information(corpus_graph(repo))
        assert other["single_child_fraction"] < 0.45
        assert other["single_child_share_of_objective"] < 0.1


# --- Move ---------------------------------------------------------------------


def test_move_delta_splits_the_two_objective_terms() -> None:
    m = Move(file="a/x.ts", destination="b", delta_edges=3.0, containers_removed=2)
    assert m.delta(0.0) == 3.0
    assert m.delta(1.5) == 0.0
    assert m.delta(8.0) == -13.0
