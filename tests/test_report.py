"""Unit tests for report/run.py.

Corpus-level acceptance (does --exclude actually remove the confounds
plan.md records) is exercised against the real checked-in graphs; everything
else uses tests/fixtures/graphs.py so the expected numbers can be derived by
hand, the same convention as test_metrics.py.
"""

from __future__ import annotations

import csv
import math

import pytest
import typer

from extractors.schema import Graph, Stats
from model.paths import branching
from report.run import (
    build_report,
    load_graphs,
    main,
    unresolved_ratio,
    worst_edges,
    write_summary_csv,
    write_summary_md,
    write_worst_edges_csv,
)
from tests.fixtures.graphs import graph, node, plan_md_tree

# --- load_graphs ------------------------------------------------------------


def test_load_graphs_filters_by_lang() -> None:
    graphs = load_graphs(lang="py", repos=[])
    assert {g.repo for g in graphs} == {"flask", "requests", "rich"}


def test_load_graphs_filters_by_repo() -> None:
    graphs = load_graphs(lang=None, repos=["zod", "vite"])
    assert {g.repo for g in graphs} == {"zod", "vite"}


def test_load_graphs_skips_unextracted_manifest_entries() -> None:
    # meridian2 is in corpus/manifest.toml but has no corpus/graphs/ file yet.
    graphs = load_graphs(lang=None, repos=["meridian2"])
    assert graphs == []


def test_load_graphs_with_no_filters_returns_every_extracted_repo() -> None:
    graphs = load_graphs(lang=None, repos=[])
    assert {g.repo for g in graphs} == {
        "flask",
        "requests",
        "rich",
        "zod",
        "date-fns",
        "vite",
        "tanstack-router",
    }


# --- unresolved_ratio ---------------------------------------------------------


def test_unresolved_ratio_matches_extract_pys_calculation() -> None:
    g = graph(node("a.ts", imports=("b.ts", "b.ts")), node("b.ts"))
    g = Graph(
        repo=g.repo,
        lang=g.lang,
        commit=g.commit,
        extractor=g.extractor,
        roots=g.roots,
        nodes=g.nodes,
        stats=Stats(unresolved_imports=2, external_imports_dropped=0),
    )
    # 2 raw import statements (a.ts -> b.ts twice) + 2 unresolved = ratio 0.5.
    assert unresolved_ratio(g) == pytest.approx(0.5)


def test_unresolved_ratio_is_none_with_no_imports_seen() -> None:
    g = graph(node("a.ts"))
    assert unresolved_ratio(g) is None


# --- worst_edges --------------------------------------------------------------


def test_worst_edges_ranks_the_strangers_interior_edge_highest() -> None:
    g = plan_md_tree()
    worst = worst_edges(g, branching(g), top_n=1)
    assert len(worst) == 1
    top = worst[0]
    # b/x -> a/sub/deep/y: cost 3, stranger's interior -- highest of both costs.
    assert (top.source, top.target) == ("b/x.ts", "a/sub/deep/y.ts")
    assert top.integer_cost == 3
    assert top.gateway == "a"
    assert top.face_hit is False


def test_worst_edges_face_hit_true_when_landing_on_the_target_directorys_face() -> None:
    g = plan_md_tree()
    worst = worst_edges(g, branching(g), top_n=10)
    by_pair = {(w.source, w.target): w for w in worst}
    # b/x -> a/index: cost 1, sibling's face -- gateway is a, and the target
    # *is* a's face.
    entered_at_face = by_pair[("b/x.ts", "a/index.ts")]
    assert entered_at_face.gateway == "a"
    assert entered_at_face.face_hit is True
    # a/x -> a/sub/deep/y: cost 2, reaches past a/sub's face into its interior.
    reached_past_face = by_pair[("a/x.ts", "a/sub/deep/y.ts")]
    assert reached_past_face.gateway == "a/sub"
    assert reached_past_face.face_hit is False


def test_worst_edges_respects_top_n() -> None:
    g = plan_md_tree()
    assert len(worst_edges(g, branching(g), top_n=2)) == 2


def test_worst_edges_are_sorted_descending_by_bit_cost() -> None:
    g = plan_md_tree()
    worst = worst_edges(g, branching(g), top_n=10)
    costs = [w.bit_cost for w in worst]
    assert costs == sorted(costs, reverse=True)


# --- build_report --------------------------------------------------------------


def test_build_report_flags_a_repo_over_the_unresolved_threshold() -> None:
    g = graph(node("a.ts", imports=("b.ts",)), node("b.ts"))
    g = Graph(
        repo=g.repo,
        lang=g.lang,
        commit=g.commit,
        extractor=g.extractor,
        roots=g.roots,
        nodes=g.nodes,
        stats=Stats(unresolved_imports=10, external_imports_dropped=0),
    )
    metrics, _, _ = build_report(g, c=8.0, top_n=5)
    assert metrics["unresolved_ratio"] == pytest.approx(10 / 11)
    assert metrics["flagged"] is True


def test_build_report_does_not_flag_a_clean_repo() -> None:
    metrics, _, _ = build_report(plan_md_tree(), c=8.0, top_n=5)
    assert metrics["unresolved_ratio"] == pytest.approx(0.0)
    assert metrics["flagged"] is False


def test_build_report_returns_at_most_top_n_worst_edges() -> None:
    _, worst, _ = build_report(plan_md_tree(), c=8.0, top_n=2)
    assert len(worst) == 2


# --- CSV / Markdown writers -----------------------------------------------------


def test_write_summary_csv_has_one_row_per_repo(tmp_path) -> None:
    metrics_a, _, _ = build_report(plan_md_tree(), c=8.0, top_n=1)
    path = tmp_path / "summary.csv"
    write_summary_csv([metrics_a], path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["repo"] == "fixture"
    assert rows[0]["nodes"] == "6"


def test_write_summary_md_contains_a_header_row_and_one_row_per_repo(tmp_path) -> None:
    metrics_a, _, _ = build_report(plan_md_tree(), c=8.0, top_n=1)
    path = tmp_path / "summary.md"
    write_summary_md([metrics_a], path, c=8.0)
    text = path.read_text(encoding="utf-8")
    assert "| repo |" in text
    assert "| fixture |" in text


def test_write_worst_edges_csv_round_trips(tmp_path) -> None:
    g = plan_md_tree()
    worst = worst_edges(g, branching(g), top_n=3)
    path = tmp_path / "worst-edges.csv"
    write_worst_edges_csv(worst, path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 3
    assert rows[0]["source"] == "b/x.ts"
    assert rows[0]["target"] == "a/sub/deep/y.ts"
    assert float(rows[0]["bit_cost"]) == pytest.approx(worst[0].bit_cost, abs=1e-3)


# --- main(), against the real corpus --------------------------------------------


def test_main_writes_the_expected_artifacts(tmp_path) -> None:
    out = tmp_path / "all"
    main(output=out, repo=["zod"])
    assert (out / "zod.json").is_file()
    assert (out / "summary.csv").is_file()
    assert (out / "summary.md").is_file()
    assert (out / "worst-edges.csv").is_file()


def test_main_raises_when_nothing_matches(tmp_path) -> None:
    with pytest.raises(typer.BadParameter):
        main(output=tmp_path / "empty", repo=["not-a-real-repo"])


def test_main_exclude_removes_test_files_from_zod(tmp_path) -> None:
    all_out = tmp_path / "all"
    no_tests_out = tmp_path / "no-tests"
    main(output=all_out, repo=["zod"])
    main(output=no_tests_out, repo=["zod"], exclude=["**/*.test.ts", "**/tests/**"])

    import json

    with_tests = json.loads((all_out / "zod.json").read_text(encoding="utf-8"))
    without_tests = json.loads((no_tests_out / "zod.json").read_text(encoding="utf-8"))
    # plan.md: zod is 59% test files under */tests/*.
    assert without_tests["nodes"] < with_tests["nodes"]
    assert without_tests["nodes"] / with_tests["nodes"] < 0.6


def test_main_no_splice_barrels_matches_the_unspliced_histogram(tmp_path) -> None:
    # plan.md's recorded unspliced zod histogram: 56/43/1/0.
    import json

    out = tmp_path / "unspliced"
    main(output=out, repo=["zod"], splice_barrels=False)
    metrics = json.loads((out / "zod.json").read_text(encoding="utf-8"))
    fractions = metrics["cost_histogram"]["all"]["fractions"]
    assert round(fractions["0"] * 100) == 56
    assert round(fractions["1"] * 100) == 43


# --- local optimality and --freeze ----------------------------------------------


def test_build_report_returns_movers_and_a_local_optimality_summary() -> None:
    metrics, _, movers = build_report(plan_md_tree(), c=8.0, top_n=1)
    summary = metrics["local_optimality"]
    assert "movers" not in summary  # detail belongs in the CSV, not the JSON
    assert summary["locally_optimal"] + len(movers) == summary["files_considered"]
    assert all(m["file"] and "delta" in m for m in movers)


def test_build_report_freeze_removes_files_from_the_placement_question() -> None:
    frozen, _, movers = build_report(plan_md_tree(), c=8.0, top_n=1, freeze=["a/**"])
    # a/ holds 4 of the 6 files; they stay in the cost but stop being candidates.
    assert frozen["local_optimality"]["frozen_files"] == 4
    assert frozen["local_optimality"]["files_considered"] == 2
    assert frozen["total_bit_cost"]["edges"] == 6  # every edge still counted
    assert all(not m["file"].startswith("a/") for m in movers)


def test_main_writes_movers_csv(tmp_path) -> None:
    out = tmp_path / "all"
    main(output=out, repo=["tanstack-router"])
    rows = list(csv.DictReader((out / "movers.csv").open(encoding="utf-8")))
    assert rows and {r["repo"] for r in rows} == {"tanstack-router"}
    assert all(float(r["delta"]) < 0 for r in rows)


def test_load_graphs_and_worst_edges_agree_on_units() -> None:
    # Sanity check that bit_cost here is finite and non-negative for real data.
    g = load_graphs(lang=None, repos=["zod"])[0]
    worst = worst_edges(g, branching(g), top_n=5)
    assert all(math.isfinite(w.bit_cost) and w.bit_cost >= 0 for w in worst)
