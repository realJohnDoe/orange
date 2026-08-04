"""Unit tests for report/calibrate.py.

The verdict function is the part that can quietly lie -- it turns four curves
into one sentence, and the sentence is what gets read -- so each of its four
outcomes is exercised against synthetic rows whose shape makes the right answer
obvious. Everything else is checked end to end against the real corpus.
"""

from __future__ import annotations

import csv

import pytest
import typer

from report.calibrate import (
    best_shared_c,
    c_grid,
    earnings,
    main,
    peak,
    verdict,
    write_calibration_csv,
)
from model.placement import containers
from tests.fixtures.graphs import plan_md_tree


def rows(repo: str, values: dict[float, float], splits: int = 0) -> list[dict]:
    """One curve as calibrate rows: C -> fraction stable."""
    return [
        {
            "repo": repo,
            "c": c,
            "containers": 100,
            "stable": round(f * 100),
            "fraction_stable": f,
            "wants_split": splits,
            "wants_dissolve": 100 - round(f * 100) - splits,
            "never_stable": 0,
            "files_considered": 100,
            "fraction_locally_optimal": f,
        }
        for c, f in sorted(values.items())
    ]


# --- c_grid ------------------------------------------------------------------


def test_c_grid_is_geometric_and_hits_both_ends() -> None:
    grid = c_grid(0.125, 128.0, 11)
    assert grid[0] == pytest.approx(0.125)
    assert grid[-1] == pytest.approx(128.0)
    ratios = [b / a for a, b in zip(grid, grid[1:])]
    assert ratios == pytest.approx([ratios[0]] * len(ratios))


def test_c_grid_degenerates_to_a_single_point() -> None:
    assert c_grid(4.0, 64.0, 1) == [4.0]


# --- peak --------------------------------------------------------------------


def test_peak_brackets_a_plateau_rather_than_naming_one_argmax() -> None:
    p = peak(rows("r", {1.0: 0.2, 2.0: 0.9, 4.0: 0.9, 8.0: 0.3}), "fraction_stable", 100)
    assert p["peak_fraction"] == 0.9
    assert (p["c_lo"], p["c_hi"]) == (2.0, 4.0)
    assert p["lift"] == pytest.approx(0.7)
    assert p["flat"] is False


def test_peak_tolerance_is_one_whole_container() -> None:
    # 0.39 vs 0.40 is one directory out of 100, which is not a resolvable peak.
    p = peak(rows("r", {1.0: 0.39, 2.0: 0.40, 4.0: 0.30}), "fraction_stable", 100)
    assert (p["c_lo"], p["c_hi"]) == (1.0, 2.0)


def test_peak_calls_a_curve_flat_when_it_barely_moves() -> None:
    p = peak(rows("r", {1.0: 0.500, 2.0: 0.502, 4.0: 0.501}), "fraction_stable", 100)
    assert p["flat"] is True


# --- verdict -----------------------------------------------------------------


def test_verdict_flat_when_no_curve_moves() -> None:
    data = rows("a", {1.0: 0.5, 2.0: 0.5, 4.0: 0.5}) + rows("b", {1.0: 0.3, 2.0: 0.3, 4.0: 0.3})
    peaks = {
        r: peak([x for x in data if x["repo"] == r], "fraction_stable", 100) for r in ("a", "b")
    }
    assert verdict(peaks, data).startswith("FLAT")


def test_verdict_one_sided_when_every_plateau_runs_off_the_bottom_of_the_grid() -> None:
    # Both curves are highest at the smallest C tested: the sweep bounds C from
    # above and not at all from below, which must not be reported as a shared
    # optimum.
    data = rows("a", {1.0: 0.9, 2.0: 0.5, 4.0: 0.1}) + rows("b", {1.0: 0.8, 2.0: 0.4, 4.0: 0.1})
    peaks = {
        r: peak([x for x in data if x["repo"] == r], "fraction_stable", 100) for r in ("a", "b")
    }
    assert verdict(peaks, data).startswith("ONE-SIDED")


def test_verdict_shared_when_an_interior_plateau_is_common_to_every_repo() -> None:
    data = rows("a", {1.0: 0.1, 2.0: 0.9, 4.0: 0.9, 8.0: 0.2}) + rows(
        "b", {1.0: 0.1, 2.0: 0.8, 4.0: 0.8, 8.0: 0.2}
    )
    peaks = {
        r: peak([x for x in data if x["repo"] == r], "fraction_stable", 100) for r in ("a", "b")
    }
    assert verdict(peaks, data).startswith("SHARED")


def test_verdict_spread_when_interior_peaks_do_not_overlap() -> None:
    data = rows("a", {1.0: 0.1, 2.0: 0.9, 4.0: 0.2, 8.0: 0.1}) + rows(
        "b", {1.0: 0.1, 2.0: 0.2, 4.0: 0.9, 8.0: 0.1}
    )
    peaks = {
        r: peak([x for x in data if x["repo"] == r], "fraction_stable", 100) for r in ("a", "b")
    }
    assert verdict(peaks, data).startswith("SPREAD")


def test_best_shared_c_weights_repos_equally() -> None:
    data = rows("a", {1.0: 1.0, 2.0: 0.0}) + rows("b", {1.0: 0.0, 2.0: 0.9})
    c, mean = best_shared_c(data)
    assert (c, mean) == (1.0, 0.5)


# --- earnings -----------------------------------------------------------------


def test_earnings_counts_only_directories_worth_more_than_zero_bits() -> None:
    census = containers(plan_md_tree())
    e = earnings(census)
    assert e["containers"] == len(census)
    assert e["earning"] == sum(1 for c in census if c.dissolve_bits > 0)
    if e["earning"]:
        assert e["p25"] <= e["median"] <= e["p75"]


# --- main(), against the real corpus ------------------------------------------


def test_main_writes_the_expected_artifacts(tmp_path) -> None:
    out = tmp_path / "calibration"
    main(output=out, repo=["zod", "tanstack-router"], steps=5)
    assert (out / "calibration.md").is_file()
    written = list(csv.DictReader((out / "calibration.csv").open(encoding="utf-8")))
    assert {r["repo"] for r in written} == {"zod", "tanstack-router"}
    assert len(written) == 10  # 2 repos x 5 grid points


def test_main_raises_when_nothing_matches(tmp_path) -> None:
    with pytest.raises(typer.BadParameter):
        main(output=tmp_path / "empty", repo=["not-a-real-repo"])


def test_write_calibration_csv_round_trips(tmp_path) -> None:
    path = tmp_path / "calibration.csv"
    write_calibration_csv(rows("a", {1.0: 0.5, 2.0: 0.25}), path)
    written = list(csv.DictReader(path.open(encoding="utf-8")))
    assert [r["fraction_stable"] for r in written] == ["0.5", "0.25"]
