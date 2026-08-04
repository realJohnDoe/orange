"""report/run.py -- one report variant per invocation.

    uv run python -m report.run --output report/out/all
    uv run python -m report.run --output report/out/no-tests \\
        --exclude '**/*.test.ts' --exclude '**/__tests__/**' --exclude '**/test_*.py'

Rather than sweeping variants internally, each invocation applies one set of
--exclude / --splice-barrels / --lang / --repo choices to every graph it loads
and writes the results under --output. This is what "extract once, filter in
analysis" (plan.md) looks like at the report layer: the checked-in graphs
never change, and comparing "all" vs. "no-tests" is comparing two directories.

--exclude fixes the two confounds recorded in plan.md's "What we've learned":
zod is 59% test files, and date-fns's 937 single-file directories are a
filename convention rather than real containment. Nothing before this CLI
could remove either from a measurement.

--freeze (PR 4c) is the other half of that pair and does something different:
it keeps a file's edges and its directory's branching in the cost while
declaring its *location* off-limits, which only bites once something asks
"could this move?". Excluding a convention-governed subtree changes the answer
for files you did not exclude -- a util imported only by frozen routes would
look unimported, and the depth tiebreak would bury it -- so the two flags are
not interchangeable. See plan.md, PR 4c.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer

from corpus.sync import load_manifest
from extractors.classify import is_face
from extractors.schema import Graph, load
from model.graph import filter_nodes
from model.graph import reroot as reroot_graph
from model.graph import splice_barrels as splice_all_barrels
from model.metrics import DEFAULT_C, all_metrics, edges
from model.paths import bit_cost, branching, common_prefix_len, cost, dirs
from model.placement import Container, container_stability, containers, local_optimality

GRAPHS_DIR = Path(__file__).resolve().parents[1] / "corpus" / "graphs"

# A repo whose unresolved-import ratio exceeds this is a data-quality problem,
# not the normal noise of external packages the TS extractor can't resolve
# without node_modules: every repo in plan.md's "Validation numbers" sits
# under 30%, while the mis-pinned-typescript hazard that silently zeroed out
# all .ts parsing (plan.md, "What we've learned" #1) would show up near 100%.
UNRESOLVED_RATIO_THRESHOLD = 0.5


def load_graphs(lang: str | None, repos: list[str]) -> list[Graph]:
    """Every extracted graph matching --lang/--repo, in manifest order.

    Silently skips manifest entries with no corpus/graphs/<repo>.json.gz --
    meridian2 is in the manifest but not yet extracted (PR 6).
    """
    entries = load_manifest()
    if lang is not None:
        entries = [e for e in entries if e.lang == lang]
    if repos:
        wanted = set(repos)
        entries = [e for e in entries if e.name in wanted]
    graphs = []
    for entry in entries:
        path = GRAPHS_DIR / f"{entry.name}.json.gz"
        if path.is_file():
            graphs.append(load(path))
    return graphs


def unresolved_ratio(graph: Graph) -> float | None:
    """Fraction of import statements the extractor saw but could not resolve.

    Matches extractors/ts/extract.py's own ratio: against raw import
    *statements* (a node's imports before edges() dedupes them), since that is
    what Stats.unresolved_imports counts against.
    """
    seen = sum(len(n.imports) for n in graph.nodes)
    total = seen + graph.stats.unresolved_imports
    return graph.stats.unresolved_imports / total if total else None


@dataclass(frozen=True, slots=True)
class WorstEdge:
    repo: str
    source: str
    target: str
    integer_cost: int
    bit_cost: float
    gateway: str
    face_hit: bool


def worst_edges(graph: Graph, tree: dict[tuple[str, ...], int], top_n: int) -> list[WorstEdge]:
    """The top_n edges by bit cost, gateway and face-hit computed the same way
    as model.metrics.cross_face_entries so the two numbers agree."""
    rows = []
    for u, v, _ in edges(graph):
        dv = dirs(v)
        k = common_prefix_len(dirs(u), dv)
        gateway = ""
        face_hit = False
        if len(dv) - k >= 1:
            gateway_tuple = dv[: k + 1]
            gateway = "/".join(gateway_tuple)
            face_hit = dv == gateway_tuple and is_face(v, graph.lang)
        rows.append(
            WorstEdge(
                repo=graph.repo,
                source=u,
                target=v,
                integer_cost=cost(u, v),
                bit_cost=bit_cost(u, v, tree),
                gateway=gateway,
                face_hit=face_hit,
            )
        )
    rows.sort(key=lambda r: r.bit_cost, reverse=True)
    return rows[:top_n]


def build_report(
    graph: Graph, c: float, top_n: int, freeze: Sequence[str] = ()
) -> tuple[dict[str, Any], list[WorstEdge], list[dict[str, Any]], list[Container]]:
    """all_metrics() plus the unresolved-ratio flag, the worst edges, movers, containers.

    The per-file and per-directory detail is kept out of the metrics dict and
    returned alongside it: those belong in CSVs, while the metrics dict is the
    per-repo summary that gets serialized as JSON.
    """
    metrics = all_metrics(graph, c)
    ratio = unresolved_ratio(graph)
    metrics["unresolved_ratio"] = ratio
    metrics["flagged"] = ratio is not None and ratio > UNRESOLVED_RATIO_THRESHOLD
    placement = local_optimality(graph, c, freeze)
    movers = placement.pop("movers")
    metrics["local_optimality"] = placement
    census = containers(graph, freeze)
    metrics["container_stability"] = container_stability(graph, c, freeze, census)
    tree = branching(graph)
    return metrics, worst_edges(graph, tree, top_n), movers, census


# name, extractor -- every summary.csv / summary.md column, in display order.
_SUMMARY_COLUMNS: tuple[tuple[str, Any], ...] = (
    ("repo", lambda m: m["repo"]),
    ("lang", lambda m: m["lang"]),
    ("nodes", lambda m: m["nodes"]),
    ("edges", lambda m: m["total_bit_cost"]["edges"]),
    ("cost_0", lambda m: m["cost_histogram"]["all"]["fractions"]["0"]),
    ("cost_1", lambda m: m["cost_histogram"]["all"]["fractions"]["1"]),
    ("cost_2", lambda m: m["cost_histogram"]["all"]["fractions"]["2"]),
    ("cost_3+", lambda m: m["cost_histogram"]["all"]["fractions"]["3+"]),
    ("mean_integer_cost", lambda m: m["integer_edge_cost"]["mean"]),
    ("bits_per_edge", lambda m: m["total_bit_cost"]["bits_per_edge"]),
    ("compression_ratio", lambda m: m["total_bit_cost"]["compression_ratio"]),
    ("split_rate", lambda m: m["directory_cohesion"]["split_rate"]),
    ("genuine_split_rate", lambda m: m["directory_cohesion"]["genuine_split_rate"]),
    ("locally_optimal", lambda m: m["local_optimality"]["fraction_locally_optimal"]),
    ("frozen_files", lambda m: m["local_optimality"]["frozen_files"]),
    ("single_child_dirs", lambda m: m["container_information"]["single_child_fraction"]),
    ("dirs_earning", lambda m: m["container_stability"]["earns"]),
    ("dirs_neutral", lambda m: m["container_stability"]["neutral"]),
    ("dirs_costing", lambda m: m["container_stability"]["costs"]),
    ("depth_informative", lambda m: m["depth_histogram"]["informative"]),
    ("rho_fanin_ge_1", lambda m: m["depth_vs_fanin"]["rho_fanin_ge_1"]),
    ("unresolved_ratio", lambda m: m["unresolved_ratio"]),
    ("flagged", lambda m: m["flagged"]),
)


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def write_summary_csv(rows: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(name for name, _ in _SUMMARY_COLUMNS)
        for row in rows:
            writer.writerow(_fmt(getter(row)) for _, getter in _SUMMARY_COLUMNS)


def write_summary_md(rows: list[dict[str, Any]], path: Path, c: float) -> None:
    lines = [
        f"# Report summary (C = {c})",
        "",
        f"Repos above the unresolved-import threshold ({UNRESOLVED_RATIO_THRESHOLD:.0%}) are "
        "flagged rather than silently averaged in.",
        "",
        "| " + " | ".join(name for name, _ in _SUMMARY_COLUMNS) + " |",
        "| " + " | ".join("---" for _ in _SUMMARY_COLUMNS) + " |",
    ]
    for row in rows:
        cells = [_fmt(getter(row)) for _, getter in _SUMMARY_COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_worst_edges_csv(worst: list[WorstEdge], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["repo", "source", "target", "integer_cost", "bit_cost", "gateway", "face_hit"]
        )
        for w in worst:
            writer.writerow(
                [w.repo, w.source, w.target, w.integer_cost, f"{w.bit_cost:.4f}", w.gateway, w.face_hit]
            )


def write_movers_csv(movers: list[dict[str, Any]], path: Path) -> None:
    """Every file the objective would rather see somewhere else, worst first."""
    columns = ("repo", "file", "destination", "delta", "delta_edges", "containers_removed")
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for m in movers:
            writer.writerow(_fmt(m[col]) for col in columns)


def write_containers_csv(census: list[tuple[str, Container]], path: Path) -> None:
    """Every directory priced for its own existence, worst first.

    Ordered by dissolve_bits ascending, so the `costs` verdicts -- the ones no
    value of C rescues, and the only rows that are a finding rather than a
    measurement -- are at the top of the file.
    """
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "repo",
                "dir",
                "verdict",
                "dissolve_bits",
                "children",
                "components",
                "internal_edges",
                "external_entries",
                "split_bits",
                "split_candidates",
                "c_min",
                "c_max",
            ]
        )
        for repo, x in sorted(census, key=lambda row: (row[1].c_max, row[0], row[1].dir)):
            writer.writerow(
                [
                    repo,
                    x.dir,
                    x.verdict,
                    _fmt(x.dissolve_bits),
                    x.children,
                    x.components,
                    x.internal_edges,
                    x.external_entries,
                    _fmt(x.split_bits),
                    len(x.splits),
                    _fmt(x.c_min),
                    _fmt(x.c_max),
                ]
            )


def write_splits_csv(census: list[tuple[str, Container]], path: Path, c: float) -> None:
    """Every subdirectory a directory could gain at this C, and who moves into it.

    This is the "these files belong in a subdirectory" finding, and unlike the
    dissolve verdict it names the members, so the recommendation is actionable
    rather than merely a score. *All* paying candidates are emitted, ranked per
    directory -- both sides of a cut are usually real proposals and the bit
    count cannot say which one a maintainer would accept. Picking among them is
    a naming judgement, and the consumer that does the naming can do the
    picking.
    """
    rows = [
        (repo, x, rank, s)
        for repo, x in census
        for rank, s in enumerate(x.splits)
        if s.delta(c) < 0
    ]
    rows.sort(key=lambda row: (row[3].bits, row[0], row[1].dir))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["repo", "dir", "rank", "delta", "split_bits", "size", "of", "kind", "child"]
        )
        for repo, x, rank, split in rows:
            for child in split.members:
                writer.writerow(
                    [repo, x.dir, rank, _fmt(split.delta(c)), _fmt(split.bits),
                     len(split.members), x.children, split.kind, child]
                )


def main(
    output: Annotated[Path, typer.Option(help="directory for this run's artifacts")],
    exclude: Annotated[
        list[str],
        typer.Option(help="glob of node ids to drop from the graph entirely; repeatable"),
    ] = [],
    freeze: Annotated[
        list[str],
        typer.Option(
            help="glob of node ids that stay in the cost but may not be moved; repeatable"
        ),
    ] = [],
    splice_barrels: Annotated[
        bool,
        typer.Option(help="rewire edges through barrel files to their real target"),
    ] = True,
    reroot: Annotated[
        bool,
        typer.Option(help="strip the directory prefix every file shares before measuring"),
    ] = True,
    lang: Annotated[
        str | None, typer.Option(help="only include repos in this language (py, ts)")
    ] = None,
    repo: Annotated[
        list[str],
        typer.Option(help="only include these repos; repeatable (default: every extracted repo)"),
    ] = [],
    c: Annotated[
        float, typer.Option(help="bits a container must save to justify existing")
    ] = DEFAULT_C,
    top_n: Annotated[int, typer.Option(help="worst-cost edges to record per repo")] = 20,
) -> None:
    graphs = load_graphs(lang, repo)
    if not graphs:
        raise typer.BadParameter("no extracted graphs matched --lang/--repo")

    output.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict[str, Any]] = []
    all_worst: list[WorstEdge] = []
    all_movers: list[dict[str, Any]] = []
    all_containers: list[tuple[str, Container]] = []
    for graph in graphs:
        g = filter_nodes(graph, exclude)
        if reroot:
            g = reroot_graph(g)
        if splice_barrels:
            g = splice_all_barrels(g)
        metrics, worst, movers, census = build_report(g, c, top_n, freeze)
        summary_rows.append(metrics)
        all_worst.extend(worst)
        all_movers.extend(dict(m, repo=g.repo) for m in movers)
        all_containers.extend((g.repo, x) for x in census)
        (output / f"{g.repo}.json").write_text(
            json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8"
        )
        flag = " [FLAGGED]" if metrics["flagged"] else ""
        stability = metrics["container_stability"]
        print(
            f"{g.repo:16} {metrics['nodes']:5} nodes "
            f"{metrics['total_bit_cost']['edges']:5} edges | dirs "
            f"{stability['earns']:4} earn {stability['neutral']:4} neutral "
            f"{stability['costs']:3} cost {stability['wants_split']:3} want splitting{flag}"
        )

    summary_rows.sort(key=lambda m: m["repo"])
    write_summary_csv(summary_rows, output / "summary.csv")
    write_summary_md(summary_rows, output / "summary.md", c)
    write_worst_edges_csv(all_worst, output / "worst-edges.csv")
    write_movers_csv(all_movers, output / "movers.csv")
    write_containers_csv(all_containers, output / "containers.csv")
    write_splits_csv(all_containers, output / "splits.csv", c)


if __name__ == "__main__":
    typer.run(main)
