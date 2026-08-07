"""The adjudication harness: packets, parsing, and scoring.

The adjudicator itself is a model, so nothing here asserts what it says. What is
testable -- and what an experiment about model judgement absolutely has to get
right -- is everything around it: that the packet is self-contained, that it does
not leak the answer, that a sloppily formatted reply is still counted, and that
the scorer computes precision the way the prose claims. A scoring bug would be
indistinguishable from a result.
"""

from __future__ import annotations

import json

import pytest

from model.graph import reroot, splice_barrels
from report.adjudicate import (
    EQUIVALENCE_RULES,
    Finding,
    adjudicate,
    build_findings,
    compare_baseline,
    equivalence_verdict,
    load_labels,
    parse_verdict,
    prompt,
    read_findings,
    score,
    write_baseline,
    write_findings,
)
from tests.fixtures.graphs import graph, node


def junk_drawer_repo():
    """One directory the objective dislikes, so build_findings has something to find.

    `t` holds three mutually unrelated files reached from three different
    places, which is the shape that produces a `costs` verdict.
    """
    return graph(
        node("t/a.ts"),
        node("t/b.ts"),
        node("t/c.ts"),
        node("p/x.ts", imports=("t/a.ts",)),
        node("q/y.ts", imports=("t/b.ts",)),
        node("r/z.ts", imports=("t/c.ts",)),
    )


def finding(
    id: str = "repo:costs:t",
    kind: str = "costs",
    dir: str = "t",
    evidence: dict | None = None,
) -> Finding:
    return Finding(id=id, repo="repo", kind=kind, dir=dir, evidence=evidence or {})


# --- findings ---------------------------------------------------------------


def test_ids_are_stable_and_addressable() -> None:
    """Labels are keyed by id, so an id that moves with a row number loses them."""
    ids = {f.id for f in build_findings(junk_drawer_repo())}
    assert "fixture:costs:t" in ids


def test_split_candidates_are_grouped_by_directory(corpus_graph) -> None:
    """One question per directory, not one per cut.

    Both sides of a cut are usually offered, and asking about each separately
    invites accepting two mutually exclusive proposals for the same directory.
    """
    findings = build_findings(splice_barrels(reroot(corpus_graph("zod"))))
    splits = [f for f in findings if f.kind == "split"]
    assert len(splits) == len({f.dir for f in splits})
    v4 = next(f for f in splits if f.dir == "v4")
    assert len(v4.evidence["candidates"]) > 1


def test_freeze_removes_a_directory_from_the_question_set(corpus_graph) -> None:
    g = splice_barrels(reroot(corpus_graph("date-fns")))
    before = {f.id for f in build_findings(g)}
    after = {f.id for f in build_findings(g, freeze=["locale/*/_lib/**"])}
    assert "date-fns:costs:locale/af/_lib" in before
    assert "date-fns:costs:locale/af/_lib" not in after
    assert "date-fns:costs:_lib" in after  # a real finding survives the freeze


# --- the packet -------------------------------------------------------------


def test_packet_withholds_the_deterministic_signal_by_default() -> None:
    """Two signals that have seen each other's answers are one signal."""
    f = finding(evidence={"equivalence": {"out_jaccard": 1.0}, "children": ["t/a.ts"]})
    assert "out_jaccard" not in prompt(f)
    assert "out_jaccard" in prompt(f, include_equivalence=True)


def test_packet_carries_the_question_and_the_contract() -> None:
    f = finding(evidence={"children": ["t/a.ts"]})
    text = prompt(f)
    assert "junk drawer" in text
    assert '"verdict"' in text
    assert "t/a.ts" in text


def test_packet_never_names_the_verdict_the_tool_produced() -> None:
    """The linter's own word for the finding must not appear as a hint.

    `costs` is the census verdict that put this directory in front of the
    adjudicator; showing it would be asking a leading question.
    """
    f = build_findings(junk_drawer_repo())[0]
    assert "costs" not in prompt(f).replace(f.id, "")


def test_split_packet_offers_rejection() -> None:
    f = finding(kind="split", id="repo:split:t", evidence={"candidates": []})
    assert "null to reject all" in prompt(f)


# --- reply parsing ----------------------------------------------------------


def test_parses_a_bare_object() -> None:
    assert parse_verdict('{"verdict": "taxonomy"}')["verdict"] == "taxonomy"


def test_parses_an_object_wrapped_in_prose_and_a_fence() -> None:
    """Formatting failures would otherwise be scored as judgement failures."""
    raw = 'Sure! Here you go:\n```json\n{"verdict": "junk_drawer"}\n```\nHope that helps.'
    assert parse_verdict(raw)["verdict"] == "junk_drawer"


def test_records_an_unparseable_reply_rather_than_raising() -> None:
    assert "error" in parse_verdict("I would rather not.")
    assert "error" in parse_verdict('{"verdict": ')


def test_a_backend_failure_is_recorded_per_finding() -> None:
    """A backend that dies partway must still leave the verdicts it produced."""
    findings = [finding(id="a"), finding(id="b")]
    calls = {"n": 0}

    def flaky(_: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return '{"verdict": "taxonomy"}'

    rows = adjudicate(findings, flaky)
    assert "error" in rows[0]
    assert rows[1]["verdict"] == "taxonomy"


# --- the deterministic predictor -------------------------------------------


@pytest.mark.parametrize("rule", EQUIVALENCE_RULES)
def test_high_out_jaccard_reads_as_a_taxonomy_under_every_rule(rule) -> None:
    f = finding(evidence={"equivalence": {"out_jaccard": 1.0, "in_jaccard": 0.0}})
    assert equivalence_verdict(f, rule=rule) == "taxonomy"


def test_the_in_rule_rescues_a_fixed_per_instance_layout() -> None:
    """The whole difference between the two rules, on the shape that motivated it."""
    f = finding(evidence={"equivalence": {"out_jaccard": 0.4, "in_jaccard": 1.0}})
    assert equivalence_verdict(f, rule="out") == "junk_drawer"
    assert equivalence_verdict(f, rule="max") == "taxonomy"


def test_a_directory_with_no_pairs_is_unclear() -> None:
    f = finding(evidence={"equivalence": {"out_jaccard": None, "in_jaccard": None}})
    assert equivalence_verdict(f) == "unclear"


# --- scoring ----------------------------------------------------------------


def _scored(labels, predicted_out_jaccard):
    findings = [
        finding(id=fid, evidence={"equivalence": {"out_jaccard": j, "in_jaccard": j}})
        for fid, j in predicted_out_jaccard.items()
    ]
    return score(findings, [], labels)["equivalence"]["out"]


def test_precision_counts_junk_drawer_as_the_positive_class() -> None:
    """A signal that answers `taxonomy` to everything must not score well.

    On a corpus that is mostly taxonomies, accuracy would reward exactly that,
    which is why the reported numbers are precision and recall on the
    actionable verdict instead.
    """
    labels = {
        "a": {"label": "junk_drawer"},
        "b": {"label": "taxonomy"},
        "c": {"label": "taxonomy"},
    }
    always_taxonomy = _scored(labels, {"a": 1.0, "b": 1.0, "c": 1.0})
    assert always_taxonomy["precision"] is None  # never predicted the positive class
    assert always_taxonomy["recall"] == 0.0
    assert always_taxonomy["agreement"] == pytest.approx(2 / 3)


def test_precision_and_recall_on_a_mixed_prediction() -> None:
    labels = {
        "tp": {"label": "junk_drawer"},
        "fp": {"label": "taxonomy"},
        "fn": {"label": "junk_drawer"},
        "tn": {"label": "convention"},
    }
    s = _scored(labels, {"tp": 0.0, "fp": 0.0, "fn": 1.0, "tn": 1.0})
    assert (s["true_positive"], s["false_positive"], s["false_negative"], s["true_negative"]) == (
        1,
        1,
        1,
        1,
    )
    assert s["precision"] == 0.5
    assert s["recall"] == 0.5


def test_convention_rows_are_excluded_from_the_frozen_score() -> None:
    """The `--freeze` counterfactual: precision among rows a config cannot remove."""
    labels = {
        "real": {"label": "junk_drawer"},
        "conv": {"label": "convention"},
    }
    findings = [
        finding(id=fid, evidence={"equivalence": {"out_jaccard": 0.0, "in_jaccard": 0.0}})
        for fid in labels
    ]
    result = score(findings, [], labels)
    assert result["equivalence"]["out"]["precision"] == 0.5  # both called junk_drawer
    assert result["frozen"]["out"]["precision"] == 1.0  # the convention row is gone
    assert result["frozen"]["out"]["n"] == 1


def test_split_scoring_reports_choice_and_decision_separately() -> None:
    """Picking the wrong cut and refusing to cut are different failures."""
    labels = {"s1": {"choice": 1}, "s2": {"choice": None}}
    findings = [finding(id=fid, kind="split") for fid in labels]
    verdicts = [
        {"id": "s1", "choice": 0, "name": "utils"},
        {"id": "s2", "choice": None, "name": None},
    ]
    s = score(findings, verdicts, labels)["model"]["split"]
    assert s["exact_choice"] == 1  # only s2 matched exactly
    assert s["same_decision"] == 2  # both agreed on whether to cut at all
    assert s["unnameable_names"] == 1


def test_missing_and_unused_labels_are_reported_not_silently_dropped() -> None:
    findings = [finding(id="known")]
    result = score(findings, [], {"other": {"label": "taxonomy"}})
    assert result["unlabelled"] == ["known"]
    assert result["unused"] == ["other"]


# --- the ratchet ------------------------------------------------------------


def test_a_missing_baseline_reports_everything_as_new(tmp_path) -> None:
    """First run on a repo: nothing has been accepted, so nothing is grandfathered."""
    findings = [finding(id="a"), finding(id="b")]
    added, removed = compare_baseline(findings, tmp_path / "absent.json")
    assert added == ["a", "b"]
    assert removed == []


def test_the_ratchet_fires_on_a_new_id_even_when_one_was_fixed(tmp_path) -> None:
    """A count-based ratchet would pass this; that is why it compares ids.

    One finding appeared and one was fixed, so the total is unchanged and the
    repository is not the same repository.
    """
    path = tmp_path / "baseline.json"
    write_baseline([finding(id="old"), finding(id="kept")], path)
    added, removed = compare_baseline([finding(id="new"), finding(id="kept")], path)
    assert added == ["new"]
    assert removed == ["old"]


def test_baseline_round_trips_and_is_stable(tmp_path) -> None:
    path = tmp_path / "baseline.json"
    findings = [finding(id="b"), finding(id="a")]
    write_baseline(findings, path)
    assert json.loads(path.read_text(encoding="utf-8")) == ["a", "b"]
    assert compare_baseline(findings, path) == ([], [])


def test_the_checked_in_baseline_matches_the_checked_in_corpus() -> None:
    """CI has to be green on the repo as it stands, or the ratchet is noise."""
    from report.adjudicate import BASELINE_PATH, _load

    findings = _load(None, [], 8.0, splice=True, reroot=True)
    assert compare_baseline(findings, BASELINE_PATH) == ([], [])


# --- round trip -------------------------------------------------------------


def test_findings_round_trip_through_jsonl(tmp_path) -> None:
    original = build_findings(junk_drawer_repo())
    path = tmp_path / "findings.jsonl"
    write_findings(original, path)
    assert read_findings(path) == original


def test_the_checked_in_labels_cover_the_checked_in_candidate_set() -> None:
    """The acceptance criterion: a verdict for every finding the corpus produces.

    This is what makes the reported precision a statement about the corpus
    rather than about whichever rows happened to get labelled.
    """
    from report.adjudicate import _load

    findings = _load(None, [], 8.0, splice=True, reroot=True)
    labels = load_labels()
    assert {f.id for f in findings} == set(labels)
    for f in findings:
        entry = labels[f.id]
        assert entry["why"]
        if f.kind == "costs":
            assert entry["label"] in {"junk_drawer", "taxonomy", "convention", "unclear"}
        else:
            choice = entry["choice"]
            assert choice is None or 0 <= choice < len(f.evidence["candidates"])


def test_labels_json_is_sorted_and_stable() -> None:
    """Checked in, so it has to diff cleanly when the corpus grows."""
    from report.adjudicate import LABELS_PATH

    text = LABELS_PATH.read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"
