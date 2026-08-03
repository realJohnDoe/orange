"""Acceptance check: the real implementation must reproduce the recorded numbers.

Before model/ existed, the barrel splice and the cohesion metric were validated
with throwaway scripts against the checked-in corpus, and the results were
written into plan.md as findings. Those findings are now load-bearing -- zod's
large splice delta is what proves date-fns's small one is a repo-convention fact
rather than a broken splice -- so the shipped code has to land on the same
numbers, not merely on plausible ones.

All of these are integer-cost numbers, which is what plan.md quotes throughout;
the bit cost postdates them and has no recorded corpus values to match yet.

Tolerances are the precision the source documents quote (whole percents, two
decimals), not a fudge factor.
"""

import pytest

from model.graph import splice_barrels
from model.metrics import cost_histogram, directory_cohesion, integer_edge_cost


def percent_histogram(graph) -> list[int]:
    f = cost_histogram(graph)["all"]["fractions"]
    return [round(f[b] * 100) for b in ("0", "1", "2", "3+")]


@pytest.mark.parametrize(
    "repo,unspliced,spliced",
    [
        # plan.md, "The barrel-splice delta was validated ad hoc".
        ("zod", [56, 43, 1, 0], [13, 86, 0, 0]),
        ("date-fns", [30, 37, 32, 1], [33, 32, 34, 1]),
    ],
)
def test_splice_reproduces_the_recorded_cost_histograms(
    corpus_graph, repo: str, unspliced: list[int], spliced: list[int]
) -> None:
    graph = corpus_graph(repo)
    assert percent_histogram(graph) == unspliced
    assert percent_histogram(splice_barrels(graph)) == spliced


@pytest.mark.parametrize(
    "repo,mean_cost,split_rate",
    [
        # plan.md, "Phase 0 results" (the "real" column of each pair).
        ("zod", 0.44, 0.50),
        ("date-fns", 1.04, 0.21),
        ("vite", 0.39, 0.59),
        ("tanstack-router", 0.28, 0.50),
    ],
)
def test_reproduces_the_recorded_phase_0_table(
    corpus_graph, repo: str, mean_cost: float, split_rate: float
) -> None:
    graph = corpus_graph(repo)
    assert integer_edge_cost(graph)["mean"] == pytest.approx(mean_cost, abs=0.005)
    assert directory_cohesion(graph)["split_rate"] == pytest.approx(split_rate, abs=0.005)


@pytest.mark.parametrize("repo", ["zod", "date-fns", "vite", "tanstack-router", "rich", "flask"])
def test_splice_is_idempotent_on_the_real_corpus(corpus_graph, repo: str) -> None:
    once = splice_barrels(corpus_graph(repo))
    assert splice_barrels(once) == once


def test_the_python_corpus_still_trips_the_depth_gate(corpus_graph) -> None:
    # plan.md predicted this before any TS data existed; it is why the Python
    # corpus never got to weigh in on the hypothesis.
    from model.metrics import depth_histogram

    assert depth_histogram(corpus_graph("requests"))["informative"] is False
