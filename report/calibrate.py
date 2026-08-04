"""report/calibrate.py -- is C a constant of well-structured code, or a knob?

    uv run python -m report.calibrate --output report/out/calibration

plan.md's headline PR 4c experiment. C is the objective's only free parameter:
the bits a container must save to justify existing. Sweep it and ask, at each
value, how much of a well-structured repo the objective would leave alone.

**The measure is container stability, not file-level local optimality.** The
sweep plan.md specifies -- local optimality against single-file moves -- cannot
calibrate C, and the reason is structural rather than empirical: destinations
are existing directories, so a move can only ever empty containers, its delta
`delta_edges - C * containers_removed` is non-increasing in C for every
candidate, and so is the minimum over them. Once a file wants to move it wants
to move at every larger C. The locally-optimal fraction is therefore monotone
non-increasing with its maximum pinned at C = 0, and no interior peak can exist.
It is reported below because it is worth seeing, but it identifies nothing.

What C actually arbitrates is a question about containers, and that one is
two-sided (model.placement.containers):

- too large, and a directory would rather dissolve into its parent -- the
  addressing it saves does not cover the C it costs;
- too small, and a directory would rather split into its connected components --
  the addressing a split would save exceeds the C the new containers cost.

Each directory is stable over an interval of C, so the fraction of stable
directories has a genuine interior maximum, and the three outcomes plan.md lists
are recoverable from it (see verdict()): a shared plateau across repos means C is
a constant of well-structured code; disjoint per-repo plateaus mean it is a knob;
a flat curve means the structure term is inert.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Annotated, Any

import typer

from model.graph import filter_nodes
from model.graph import reroot as reroot_graph
from model.graph import splice_barrels as splice_all_barrels
from model.metrics import DEFAULT_C, container_information
from model.placement import (
    Container,
    container_stability,
    containers,
    local_optimality,
    move_frontier,
)
from report.run import load_graphs

# Both ends are far outside anything plan.md's rule of thumb suggests -- from
# m = 2, a directory must keep W > 2C internal edges uncut to justify splitting
# in two, which puts a plausible C in the single digits for repos this size. The
# grid is geometric because C is a scale rather than an offset: the question is
# which order of magnitude, and a linear grid would spend most of its points
# above the range where anything moves.
DEFAULT_C_MIN = 0.125
DEFAULT_C_MAX = 128.0
DEFAULT_STEPS = 29

# A curve with less range than this over the whole grid is flat: the structure
# term is not moving the answer, which is plan.md's third outcome.
FLAT_TOLERANCE = 0.01

# Floor on how close to the maximum still counts as "at the peak". The real
# tolerance is one whole directory -- these are fractions over a population of
# 10 to 1300 directories, and a plateau edge decided by a single one of them is
# noise, not a measurement. A repo with few enough containers therefore reports
# a very wide plateau, which is the honest answer: it cannot resolve C.
PEAK_TOLERANCE = 0.005


def c_grid(c_min: float, c_max: float, steps: int) -> list[float]:
    """Geometric grid from c_min to c_max inclusive."""
    if steps < 2:
        return [c_min]
    ratio = (c_max / c_min) ** (1 / (steps - 1))
    return [c_min * ratio**i for i in range(steps)]


def peak(rows: list[dict[str, Any]], key: str, population: int) -> dict[str, Any]:
    """Where one repo's curve tops out, and by how much.

    `c_lo`/`c_hi` bracket the plateau rather than naming a single argmax: the
    curve steps as whole directories change their minds, so the maximum is
    typically attained over a range of C and picking one end would overstate the
    precision. `lift` is the curve's full range over the grid -- how much of the
    answer the structure term is responsible for at all, and the thing that has
    to be non-trivial before a peak means anything.
    """
    values = [row[key] or 0.0 for row in rows]
    best = max(values)
    tolerance = max(PEAK_TOLERANCE, 1 / population) if population else PEAK_TOLERANCE
    # 1e-12 so that "exactly one directory below the peak" lands inside the
    # plateau rather than on the wrong side of 0.4 - 0.1 == 0.30000000000000004.
    at_peak = [row["c"] for row, v in zip(rows, values) if v >= best - tolerance - 1e-12]
    return {
        "peak_fraction": best,
        "c_lo": min(at_peak),
        "c_hi": max(at_peak),
        "c_mid": math.sqrt(min(at_peak) * max(at_peak)),
        "lift": best - min(values),
        "flat": best - min(values) < FLAT_TOLERANCE,
        "tolerance": tolerance,
    }


def best_shared_c(rows: list[dict[str, Any]]) -> tuple[float, float]:
    """The C maximizing mean stability across repos, and that mean.

    plan.md's fallback when the per-repo plateaus do not intersect: pick one
    number and note the sensitivity. Repos are weighted equally rather than by
    container count, so date-fns's 1295 directories do not decide it alone.
    """
    by_c: dict[float, list[float]] = {}
    for row in rows:
        by_c.setdefault(row["c"], []).append(row["fraction_stable"] or 0.0)
    means = {c: sum(v) / len(v) for c, v in by_c.items()}
    best = max(means, key=lambda c: means[c])
    return best, means[best]


def verdict(peaks: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> str:
    """plan.md's three outcomes, plus the one it did not anticipate.

    The fourth is ONE-SIDED: the curve moves with C, but every repo's maximum
    sits at the bottom of the grid because the split side of the bracket never
    binds. A plateau whose left edge is the smallest C tested is not a
    measurement of C, it is the sweep running out of room, and reporting it as a
    shared optimum would turn "we found no lower bound" into "we found a
    constant".
    """
    if not peaks:
        return "No repos swept."
    pick, mean = best_shared_c(rows)
    if all(p["flat"] for p in peaks.values()):
        return (
            "FLAT. Container stability does not move with C in any repo, so the "
            "structure term is inert and the model reduces to the edge term alone. "
            "That is a finding against 'directories must earn their existence'."
        )
    lo = max(p["c_lo"] for p in peaks.values())
    hi = min(p["c_hi"] for p in peaks.values())
    grid_min = min(row["c"] for row in rows)
    if all(p["c_lo"] <= grid_min for p in peaks.values()):
        splits = max(row["wants_split"] for row in rows if row["c"] == grid_min)
        return (
            f"ONE-SIDED. Every repo's peak plateau reaches the smallest C tested "
            f"({grid_min:.3g}), so the sweep bounds C from above -- the tightest "
            f"upper bound is C <= {hi:.3g} -- and not at all from below: at "
            f"C = {grid_min:.3g} the largest number of directories wanting to split "
            f"in any repo is {splits}. C is not identified by this corpus; see the "
            "earnings table for the spread in what directories are actually worth."
        )
    if lo <= hi:
        return (
            f"SHARED. Every repo attains its peak container stability somewhere in "
            f"C = [{lo:.3g}, {hi:.3g}], so C = {math.sqrt(lo * hi):.3g} sits inside "
            "every repo's plateau at once. C behaves like a constant of "
            f"well-structured code. Mean stability peaks at C = {pick:.3g} "
            f"({mean:.3f})."
        )
    mids = sorted(p["c_mid"] for p in peaks.values())
    return (
        f"SPREAD. Per-repo peaks run from C = {mids[0]:.3g} to C = {mids[-1]:.3g} "
        f"({mids[-1] / mids[0]:.3g}x) with no C inside every plateau. Mean stability "
        f"across repos peaks at C = {pick:.3g} ({mean:.3f}), which is the number to "
        "use if one has to be picked; the spread is the sensitivity."
    )


def repo_rows(
    graph: Any, grid: list[float], freeze: list[str]
) -> tuple[list[dict[str, Any]], list[Container]]:
    """One row per C: container stability, plus file-level local optimality.

    Both censuses are built once and re-evaluated at each C -- neither the
    per-container interval nor the per-file move frontier depends on it.
    """
    census = containers(graph, freeze)
    frontier = move_frontier(graph, freeze)
    rows = []
    for c in grid:
        row = container_stability(graph, c, freeze, census)
        placement = local_optimality(graph, c, freeze, frontier)
        row["repo"] = graph.repo
        row["files_considered"] = placement["files_considered"]
        row["fraction_locally_optimal"] = placement["fraction_locally_optimal"]
        rows.append(row)
    return rows, census


def earnings(census: list[Container]) -> dict[str, Any]:
    """What directories earn in edge bits, before C is considered at all.

    A directory's dissolve_bits is the addressing its existence saves; C is only
    ever compared against that, so the distribution of dissolve_bits over a repo
    is the C-free half of the calibration and the one number that is directly
    comparable across repos. Directories at or below zero are not a question
    about C: flattening them into their parent would already be cheaper in edge
    bits alone.
    """
    earned = sorted(x.dissolve_bits for x in census if x.dissolve_bits > 0)
    return {
        "containers": len(census),
        "earning": len(earned),
        "earning_fraction": len(earned) / len(census) if census else None,
        "p25": earned[len(earned) // 4] if earned else None,
        "median": earned[len(earned) // 2] if earned else None,
        "p75": earned[3 * len(earned) // 4] if earned else None,
    }


def write_calibration_csv(rows: list[dict[str, Any]], path: Path) -> None:
    columns = (
        "repo",
        "c",
        "containers",
        "stable",
        "fraction_stable",
        "wants_split",
        "wants_dissolve",
        "never_stable",
        "files_considered",
        "fraction_locally_optimal",
    )
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(_fmt(row[col]) for col in columns)


def write_calibration_md(
    rows: list[dict[str, Any]],
    peaks: dict[str, dict[str, Any]],
    flat: dict[str, dict[str, Any]],
    earned: dict[str, dict[str, Any]],
    single_child: dict[str, dict[str, Any]],
    path: Path,
) -> None:
    repos = sorted(peaks)
    grid = sorted({row["c"] for row in rows})
    by_key = {(row["repo"], row["c"]): row for row in rows}

    lines = [
        "# Calibrating C",
        "",
        "Fraction of directories the objective would leave alone -- neither dissolve "
        "into their parent nor split into their connected components -- as C sweeps.",
        "",
        "| C | " + " | ".join(repos) + " |",
        "| --- | " + " | ".join("---" for _ in repos) + " |",
    ]
    for c in grid:
        cells = [_fmt(by_key[(r, c)]["fraction_stable"]) for r in repos]
        lines.append("| " + _fmt(c) + " | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Which way the pressure runs",
        "",
        "Directories wanting to split (C too small) against directories wanting to "
        "dissolve (C too large). If one column is empty at every C, that side of the "
        "bracket is not binding and C is only being constrained from one direction. "
        "The two overlap only on directories no C can rescue (`never_stable` in the "
        "CSV), which want to split below the C that would keep them and dissolve "
        "above it.",
        "",
        "| C | " + " | ".join(f"{r} split/dissolve" for r in repos) + " |",
        "| --- | " + " | ".join("---" for _ in repos) + " |",
    ]
    for c in grid:
        cells = [
            f"{by_key[(r, c)]['wants_split']} / {by_key[(r, c)]['wants_dissolve']}"
            for r in repos
        ]
        lines.append("| " + _fmt(c) + " | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## What directories earn, before C",
        "",
        "A directory's dissolve cost in edge bits is the addressing its existence "
        "buys, and C is only ever compared against that number. Directories at or "
        "below zero are not a question about C at all: flattening them into their "
        "parent is already cheaper in edge bits alone.",
        "",
        "| repo | containers | earning > 0 bits | share | p25 | median | p75 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in repos:
        e = earned[r]
        lines.append(
            f"| {r} | {e['containers']} | {e['earning']} | "
            f"{_fmt(e['earning_fraction'])} | {_fmt(e['p25'])} | "
            f"{_fmt(e['median'])} | {_fmt(e['p75'])} |"
        )

    lines += [
        "",
        "## Peaks",
        "",
        "| repo | containers | peak stable | C at peak | lift | flat? |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in repos:
        p = peaks[r]
        n = by_key[(r, grid[0])]["containers"]
        lines.append(
            f"| {r} | {n} | {p['peak_fraction']:.3f} | "
            f"[{p['c_lo']:.3g}, {p['c_hi']:.3g}] | {p['lift']:.3f} | {p['flat']} |"
        )

    lines += ["", "## Verdict", "", verdict(peaks, rows), ""]

    lines += [
        "## File-level local optimality, for the record",
        "",
        "Monotone non-increasing in C by construction (see the module docstring), "
        "so it cannot have an interior peak and does not calibrate anything. Its "
        "range over the whole grid:",
        "",
        "| repo | files | locally optimal at C_min | at C_max | range |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in repos:
        lo_row = by_key[(r, grid[0])]
        hi_row = by_key[(r, grid[-1])]
        lines.append(
            f"| {r} | {lo_row['files_considered']} | "
            f"{_fmt(lo_row['fraction_locally_optimal'])} | "
            f"{_fmt(hi_row['fraction_locally_optimal'])} | {flat[r]['lift']:.3f} |"
        )

    lines += [
        "",
        "## Single-child containers",
        "",
        "plan.md's named check: date-fns's one-function-per-directory convention "
        "should show up as containers carrying zero addressing information "
        "(log2(1) = 0), and zod/vite should not.",
        "",
        f"| repo | containers | single-child | share | bits they carry | "
        f"share of objective at C={DEFAULT_C:g} |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in repos:
        ci = single_child[r]
        lines.append(
            f"| {r} | {ci['containers']} | {ci['single_child']} | "
            f"{_fmt(ci['single_child_fraction'])} | {ci['single_child_bits']:.1f} | "
            f"{_fmt(ci['single_child_share_of_objective'])} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(
    output: Annotated[Path, typer.Option(help="directory for this sweep's artifacts")],
    exclude: Annotated[
        list[str],
        typer.Option(help="glob of node ids to drop from the graph entirely; repeatable"),
    ] = [],
    freeze: Annotated[
        list[str],
        typer.Option(help="glob of node ids that stay in the cost but may not move; repeatable"),
    ] = [],
    splice_barrels: Annotated[
        bool, typer.Option(help="rewire edges through barrel files to their real target")
    ] = True,
    reroot: Annotated[
        bool, typer.Option(help="strip the directory prefix every file shares before measuring")
    ] = True,
    lang: Annotated[str | None, typer.Option(help="only sweep repos in this language")] = None,
    repo: Annotated[list[str], typer.Option(help="only sweep these repos; repeatable")] = [],
    c_min: Annotated[float, typer.Option(help="smallest C on the grid")] = DEFAULT_C_MIN,
    c_max: Annotated[float, typer.Option(help="largest C on the grid")] = DEFAULT_C_MAX,
    steps: Annotated[int, typer.Option(help="points on the geometric C grid")] = DEFAULT_STEPS,
) -> None:
    graphs = load_graphs(lang, repo)
    if not graphs:
        raise typer.BadParameter("no extracted graphs matched --lang/--repo")

    output.mkdir(parents=True, exist_ok=True)
    grid = c_grid(c_min, c_max, steps)
    rows: list[dict[str, Any]] = []
    peaks: dict[str, dict[str, Any]] = {}
    flat: dict[str, dict[str, Any]] = {}
    earned: dict[str, dict[str, Any]] = {}
    single_child: dict[str, dict[str, Any]] = {}
    for graph in graphs:
        g = filter_nodes(graph, exclude)
        if reroot:
            g = reroot_graph(g)
        if splice_barrels:
            g = splice_all_barrels(g)
        these, census = repo_rows(g, grid, freeze)
        rows.extend(these)
        peaks[g.repo] = peak(these, "fraction_stable", these[0]["containers"])
        flat[g.repo] = peak(these, "fraction_locally_optimal", these[0]["files_considered"])
        earned[g.repo] = earnings(census)
        single_child[g.repo] = container_information(g)
        p, e = peaks[g.repo], earned[g.repo]
        print(
            f"{g.repo:16} peak {p['peak_fraction']:.3f} of containers stable at "
            f"C in [{p['c_lo']:.3g}, {p['c_hi']:.3g}], lift {p['lift']:.3f}; "
            f"{_fmt(e['earning_fraction'])} earn any bits at all"
        )

    write_calibration_csv(rows, output / "calibration.csv")
    write_calibration_md(rows, peaks, flat, earned, single_child, output / "calibration.md")
    print()
    print(verdict(peaks, rows))


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


if __name__ == "__main__":
    typer.run(main)
