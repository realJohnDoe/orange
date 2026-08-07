"""report/adjudicate.py -- PR 7's pilot: can a model tell a finding from a false positive?

    uv run python -m report.adjudicate build   --output report/out/adjudication
    uv run python -m report.adjudicate run     --output report/out/adjudication --model claude-haiku-4-5
    uv run python -m report.adjudicate score   --output report/out/adjudication

    # what CI runs: deterministic, offline, no model, non-zero only on new findings
    uv run python -m report.adjudicate build --output report/out/ci --baseline report/baseline.json

Everything before this PR measured. This one asks whether the measurements are
*usable*, which is the question every open risk in plan.md now routes through
and which no further measurement answers.

**The three findings all fail the same way.** `costs` says a directory should not
exist, and the corpus's largest such verdict (zod's `v4/locales`) is a legitimate
taxonomy. `splits` offers both sides of every cut and at most one is sensible.
Each needs a judgement the bit count cannot make, and the judgement is the same
one the naming pass has to make anyway -- which is why the adjudicator and the
namer are one component rather than two.

**Two signals, compared, not one.** plan.md's original idea was naming alone.
PR 4c found a second, free, deterministic one -- structural equivalence
(`model/equivalence.py`), where a taxonomy's members have near-identical
neighbourhoods and a junk drawer's are disjoint. Both are scored against the same
hand-labelled set here. If the cheap signal reproduces the model's judgement, the
shipped tool runs the cheap one and keeps the model for naming; the point of
running both is that finding out costs one afternoon and assuming costs a
product.

**The equivalence score is deliberately withheld from the prompt.** Two signals
that have seen each other's answers are one signal. `--include-equivalence`
exists to measure how much the model gains when it is *told*, which is a
different experiment and not the one scored by default.

The adjudicator is addressed through a `Backend` rather than an SDK call because
the interesting question is whether the *cheapest* model suffices, and "cheapest"
has to be a flag. `cli` shells out to `claude -p`, which needs no API key --
the CI-usable configuration is no model at all, so the model path should not
force a credential the linter itself never needs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Any

import typer

from extractors.schema import Graph
from model.equivalence import structural_equivalence
from model.graph import filter_nodes
from model.graph import reroot as reroot_graph
from model.graph import splice_barrels as splice_all_barrels
from model.metrics import DEFAULT_C, edges
from model.paths import child_of, dirs
from model.placement import Container, containers
from report.run import load_graphs

LABELS_PATH = Path(__file__).resolve().parent / "labels.json"
RECORDED_VERDICTS = Path(__file__).resolve().parent / "verdicts" / "claude-haiku-4-5.json"
BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"

# How many child names, importers or sample edges a packet quotes. A packet has
# to fit comfortably in a cheap model's context alongside its instructions, and
# date-fns's `fp` has 398 children -- listing all of them would drown the
# question in a filename dump without adding evidence the first twenty lack.
QUOTE_LIMIT = 24


@dataclass(frozen=True, slots=True)
class Finding:
    """One question put to the adjudicator, with the evidence needed to answer it.

    `id` is `<repo>:<kind>:<dir>` -- stable across runs so labels stay attached
    to the finding rather than to a row number, which is what lets the corpus
    grow (PR 9) without invalidating the labels already collected.
    """

    id: str
    repo: str
    kind: str  # "costs" | "split"
    dir: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def question(self) -> str:
        if self.kind == "costs":
            return (
                "Is this directory a junk drawer (a bag of unrelated things that "
                "should be dissolved into its parent) or a taxonomy (a coherent "
                "category of parallel siblings that should stay)?"
            )
        return (
            "Which of the proposed subdirectories, if any, is one a maintainer of "
            "this repository would actually accept -- and what should it be named?"
        )


def build_findings(
    graph: Graph, c: float = DEFAULT_C, freeze: Sequence[str] = ()
) -> list[Finding]:
    """Every `costs` verdict and every splittable directory in one graph.

    Split candidates are grouped by directory rather than emitted one per
    candidate, because "which of these, if any" is the question -- asking about
    each cut in isolation invites accepting two mutually exclusive proposals for
    the same directory.

    `freeze` is plan.md's second escape hatch and it is not a convenience here:
    91 of this corpus's 98 `costs` rows are one of two date-fns conventions, so
    whether the conventions are declared is the single largest term in the
    tool's precision. Measuring both configurations is the point.
    """
    census = {x.dir: x for x in containers(graph, freeze)}
    equivalence = structural_equivalence(graph)
    context = _Context(graph)

    out: list[Finding] = []
    for path, container in sorted(census.items()):
        if container.verdict == "costs":
            out.append(_costs_finding(graph, container, context, equivalence.get(path, {})))
        paying = [s for s in container.splits if s.delta(c) < 0]
        if paying:
            out.append(
                _split_finding(graph, container, context, equivalence.get(path, {}), paying, c)
            )
    return out


def _costs_finding(
    graph: Graph, container: Container, context: _Context, equivalence: dict[str, Any]
) -> Finding:
    path = tuple(container.dir.split("/")) if container.dir else ()
    return Finding(
        id=f"{graph.repo}:costs:{container.dir}",
        repo=graph.repo,
        kind="costs",
        dir=container.dir,
        evidence={
            "children": _names(context.children[path]),
            "child_count": container.children,
            "components": container.components,
            "internal_edges": container.internal_edges,
            "external_entries": container.external_entries,
            # Rounded because the packet is read, not recomputed from: fifteen
            # significant figures of a log2 sum invite the reader to treat a
            # difference of 1e-13 as meaning something.
            "dissolve_bits": _round(container.dissolve_bits),
            "siblings": _names(context.siblings(path)),
            "top_importers": context.top_importers(path),
            "shared_targets": context.shared_targets(path),
            "equivalence": equivalence,
        },
    )


def _split_finding(
    graph: Graph,
    container: Container,
    context: _Context,
    equivalence: dict[str, Any],
    paying: Sequence[Any],
    c: float,
) -> Finding:
    path = tuple(container.dir.split("/")) if container.dir else ()
    members = _names(context.children[path])
    return Finding(
        id=f"{graph.repo}:split:{container.dir}",
        repo=graph.repo,
        kind="split",
        dir=container.dir,
        evidence={
            "children": members,
            "child_count": container.children,
            "internal_edges": container.internal_edges,
            "external_entries": container.external_entries,
            "candidates": [
                {
                    "rank": rank,
                    "delta_bits": _round(s.delta(c)),
                    "kind": s.kind,
                    "moves": list(s.members[:QUOTE_LIMIT]),
                    "moves_total": len(s.members),
                    "stays": _truncate(
                        [m for m in _names(context.children[path]) if m not in set(s.members)]
                    ),
                }
                for rank, s in enumerate(paying)
            ],
            "equivalence": equivalence,
        },
    )


class _Context:
    """Per-graph lookups the packets need that the census does not carry."""

    def __init__(self, graph: Graph) -> None:
        self.graph = graph
        collected: dict[tuple[str, ...], set[str]] = defaultdict(set)
        for node in graph.nodes:
            d = dirs(node.id)
            for k in range(len(d) + 1):
                # The same contraction as model.paths.child_of, rendered as a
                # path: a subdirectory is one child, and the trailing slash is
                # what keeps a file `a/b` distinct from a directory `a/b/`.
                collected[d[:k]].add(node.id if len(d) == k else "/".join(d[: k + 1]) + "/")
        self.children = {k: sorted(v) for k, v in collected.items()}
        self.edges = list(edges(graph))

    def siblings(self, container: tuple[str, ...]) -> list[str]:
        """What else lives beside this directory -- the alternative it is competing with."""
        if not container:
            return []
        return [c for c in self.children[container[:-1]] if c != "/".join(container) + "/"]

    def top_importers(self, container: tuple[str, ...]) -> list[dict[str, Any]]:
        """Which directories reach into this one, and how often.

        A taxonomy is typically addressed from one place that dispatches over it;
        a junk drawer is addressed from everywhere. Cost cannot see the
        difference -- both are just external entries -- but a reader can.
        """
        depth = len(container)
        counts: Counter[str] = Counter()
        for u, v, _ in self.edges:
            if dirs(v)[:depth] == container and dirs(u)[:depth] != container:
                counts["/".join(dirs(u)) or "."] += 1
        return [{"dir": d, "edges": n} for d, n in counts.most_common(8)]

    def shared_targets(self, container: tuple[str, ...]) -> list[dict[str, Any]]:
        """Targets imported by more than one child, and by how many children.

        The readable form of the equivalence question -- 52 of 52 children
        importing the same three modules is a taxonomy in plain sight -- and it
        is what lets the packet carry the *evidence* for equivalence while
        withholding the score computed from it.

        Counted per distinct child rather than per edge, and scoped the same way
        `model.equivalence` scopes a neighbourhood: a target in a sibling child
        counts, a target inside the importer's own child does not.
        """
        depth = len(container)
        importers: dict[str, set[str]] = defaultdict(set)
        for u, v, _ in self.edges:
            du, dv = dirs(u), dirs(v)
            if du[:depth] != container:
                continue
            child = child_of(container, u)
            if dv[:depth] == container and child_of(container, v) == child:
                continue
            importers[v].add(str(child))
        ranked = sorted(importers.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        return [
            {"target": t, "children_importing": len(who)} for t, who in ranked[:8] if len(who) > 1
        ]


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _names(items: Iterable[str]) -> list[str]:
    return _truncate(sorted(items))


def _truncate(items: list[str]) -> list[str]:
    if len(items) <= QUOTE_LIMIT:
        return items
    return [*items[:QUOTE_LIMIT], f"... and {len(items) - QUOTE_LIMIT} more"]


# --- the prompt -------------------------------------------------------------


_COSTS_CONTRACT = """Answer with a single JSON object and nothing else:

{"verdict": "junk_drawer" | "taxonomy" | "convention" | "unclear",
 "name": "<a better directory name, or null if the directory should be dissolved>",
 "confidence": "high" | "low",
 "reason": "<one sentence>"}

Only "junk_drawer" is a finding worth printing; the other three all mean leave
the directory alone.

- "junk_drawer": the children are genuinely unrelated to each other and to any
  single idea -- a bag of leftovers. Dissolve it into the parent.
- "taxonomy": the children belong to one idea, either as parallel instances of a
  category (per-locale tables, per-format parsers) or as a small cohesive
  module. Parallel instances count even when they never import each other.
- "convention": the layout is dictated by a repo-wide convention rather than by
  this directory's contents -- one directory per exported function, test
  fixtures, generated code, a fixed set of files every locale must provide. The
  giveaway is that many sibling directories have the identical shape.
- "unclear": the evidence does not support any of the above."""

_SPLIT_CONTRACT = """Answer with a single JSON object and nothing else:

{"choice": <rank of the candidate you would accept, or null to reject all>,
 "name": "<directory name for the chosen subset, or null>",
 "confidence": "high" | "low",
 "reason": "<one sentence>"}

Accept a candidate only if the files it moves share an idea you can put a name
to -- a name a maintainer would recognise, not "utils", "helpers", "misc",
"common" or "shared". If the only honest name is one of those, the cut does not
carve a real module and you should reject it."""


def prompt(finding: Finding, include_equivalence: bool = False) -> str:
    """The complete packet an adjudicator sees. Self-contained by construction.

    Nothing here says which answer the tool is hoping for, and the structural
    equivalence score is withheld unless asked for -- see the module docstring.
    """
    evidence = dict(finding.evidence)
    if not include_equivalence:
        evidence.pop("equivalence", None)
    contract = _COSTS_CONTRACT if finding.kind == "costs" else _SPLIT_CONTRACT
    return "\n".join(
        [
            "You are reviewing the output of a linter that scores a repository's",
            "directory structure against its import graph. The linter has flagged",
            "one directory. It cannot tell a real finding from a false positive;",
            "that is your job.",
            "",
            f"Repository: {finding.repo}",
            f"Directory:  {finding.dir or '<repository root>'}",
            "",
            "Question:",
            finding.question,
            "",
            "Evidence:",
            "```json",
            json.dumps(evidence, indent=2, sort_keys=True),
            "```",
            "",
            contract,
        ]
    )


# --- backends ---------------------------------------------------------------

type Backend = Callable[[str], str]


def cli_backend(model: str, timeout: int = 180) -> Backend:
    """Shell out to `claude -p`, which authenticates from an existing login.

    Chosen over the SDK because the whole hypothesis under test is that a *cheap*
    model suffices, and requiring an API key to find that out puts a credential
    in front of an experiment whose answer decides whether the credential is ever
    needed. The CLI must be logged in; a stale token surfaces as a 401 in the
    returned JSON rather than as a non-zero exit, so it is checked for.
    """
    executable = shutil.which("claude")
    if executable is None:
        raise typer.BadParameter("`claude` is not on PATH; use --backend none")

    def run(text: str) -> str:
        proc = subprocess.run(
            [executable, "-p", "--model", model, "--output-format", "json"],
            input=text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[:400]}")
        payload = json.loads(proc.stdout)
        if payload.get("is_error"):
            raise RuntimeError(f"claude reported an error: {payload.get('result')}")
        return str(payload.get("result", ""))

    return run


def parse_verdict(raw: str) -> dict[str, Any]:
    """The JSON object out of a model's reply, tolerant of prose around it.

    A model that wraps its answer in a fence or a sentence has still answered;
    failing the whole run over formatting would measure instruction-following
    rather than judgement, which is not the experiment.
    """
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        return {"error": "no JSON object in reply", "raw": raw[:500]}
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        return {"error": f"unparseable JSON: {exc}", "raw": raw[start : end + 1][:500]}
    return parsed if isinstance(parsed, dict) else {"error": "not an object", "raw": raw[:500]}


def adjudicate(
    findings: Sequence[Finding], backend: Backend, include_equivalence: bool = False
) -> list[dict[str, Any]]:
    """One verdict per finding. A failure is recorded, not raised.

    A backend that dies on finding 40 of 98 should still leave 39 usable
    verdicts and a visible reason for the rest: a partial run is evidence, and
    losing it to an exception costs a whole pass over the corpus.
    """
    out = []
    for finding in findings:
        row: dict[str, Any] = {"id": finding.id, "kind": finding.kind}
        try:
            row.update(parse_verdict(backend(prompt(finding, include_equivalence))))
        except Exception as exc:  # noqa: BLE001 -- recorded, see docstring
            row["error"] = f"{type(exc).__name__}: {exc}"
        out.append(row)
    return out


# --- the deterministic signal, as a predictor -------------------------------

# Above this median Jaccard a directory's children are called parallel siblings.
# Not fitted: FINDINGS.md's seven hand-labelled cases sit at the ends (1.000 for
# locale tables, 0.000 for junk drawers), so any threshold strictly inside (0, 1)
# reproduced them and picking one by optimisation would have been fitting noise
# between two clusters. That reasoning was sound and the premise was wrong -- on
# the full labelled set the scores are not at the ends -- so score() reports the
# threshold sweep rather than this constant defending itself.
EQUIVALENCE_THRESHOLD = 0.5

# `out` is the rule as FINDINGS.md defined it: children that import the same
# things are parallel siblings. `max` also accepts children that are *imported
# by* the same thing, which is what a fixed per-instance layout looks like from
# the graph -- date-fns's 87 locale `_lib` directories each hold five parts
# pulled in by one index.ts, scoring 1.000 in and only ~0.4 out. Both are scored
# because the difference between them is most of the signal's usable precision.
EQUIVALENCE_RULES = ("out", "max")


def equivalence_verdict(
    finding: Finding, threshold: float = EQUIVALENCE_THRESHOLD, rule: str = "out"
) -> str:
    """What structural equivalence alone says about a `costs` finding."""
    scores = finding.evidence.get("equivalence", {})
    out, into = scores.get("out_jaccard"), scores.get("in_jaccard")
    if out is None:
        return "unclear"
    value = out if rule == "out" else max(out, into or 0.0)
    return "taxonomy" if value > threshold else "junk_drawer"


# --- scoring ----------------------------------------------------------------


def score(
    findings: Sequence[Finding],
    verdicts: Sequence[dict[str, Any]],
    labels: dict[str, dict[str, Any]],
    threshold: float = EQUIVALENCE_THRESHOLD,
) -> dict[str, Any]:
    """Both signals against the labels, per finding kind.

    `junk_drawer` is the positive class throughout: it is the actionable verdict,
    the one a linter would print, and therefore the one whose precision decides
    whether the tool is worth running. Reporting accuracy instead would let a
    signal that answers "taxonomy" to everything score 70% on a corpus that is
    mostly taxonomies.
    """
    by_id = {f.id: f for f in findings}
    said = {v["id"]: v for v in verdicts}

    costs = [f for f in findings if f.kind == "costs" and f.id in labels]
    splits = [f for f in findings if f.kind == "split" and f.id in labels]
    # The model is scored only where it was asked. Adjudicating a subset is the
    # normal case -- 91 of this corpus's 98 `costs` rows are two date-fns
    # conventions, and paying a model to say "convention" 91 times measures
    # nothing -- but averaging its hits over rows it never saw would report a
    # sampling decision as an accuracy.
    asked_costs = [f for f in costs if f.id in said]
    asked_splits = [f for f in splits if f.id in said]

    return {
        "labelled": {"costs": len(costs), "split": len(splits)},
        "unlabelled": sorted(f.id for f in findings if f.id not in labels),
        "equivalence": {
            rule: _score_costs(
                costs, {f.id: equivalence_verdict(f, threshold, rule) for f in costs}, labels
            )
            for rule in EQUIVALENCE_RULES
        },
        "model": {
            "adjudicated": {"costs": len(asked_costs), "split": len(asked_splits)},
            "costs": _score_costs(
                asked_costs, {f.id: said[f.id].get("verdict") for f in asked_costs}, labels
            ),
            "split": _score_splits(asked_splits, said, labels),
            # What the same rows score under the deterministic signal, so the two
            # are compared on identical questions rather than on the model's
            # sample against the whole corpus.
            "equivalence_on_the_same_rows": {
                rule: _score_costs(
                    asked_costs,
                    {f.id: equivalence_verdict(f, threshold, rule) for f in asked_costs},
                    labels,
                )
                for rule in EQUIVALENCE_RULES
            },
        },
        # What the same signals score once the convention-governed rows are gone.
        # Not a second experiment: `convention` is exactly the class plan.md's
        # --freeze escape hatch exists to remove, and 91 of 98 rows are in it, so
        # quoting only the raw precision would attribute to the discriminator a
        # failure that belongs to an undeclared config. Quoting only this one
        # would hide that the config has to be written first.
        "frozen": {
            rule: _score_costs(
                [f for f in costs if labels[f.id]["label"] != "convention"],
                {f.id: equivalence_verdict(f, threshold, rule) for f in costs},
                labels,
            )
            for rule in EQUIVALENCE_RULES
        },
        "label_counts": dict(Counter(labels[f.id]["label"] for f in costs)),
        "sensitivity": {
            rule: {
                f"{t:.2f}": {
                    k: v
                    for k, v in _score_costs(
                        costs,
                        {f.id: equivalence_verdict(f, t, rule) for f in costs},
                        labels,
                    ).items()
                    if k in ("precision", "recall")
                }
                for t in (0.1, 0.25, 0.4, 0.5, 0.75, 0.9)
            }
            for rule in EQUIVALENCE_RULES
        },
        "missing_verdicts": sorted(f.id for f in findings if f.id not in said),
        "unused": sorted(set(labels) - set(by_id)),
    }


def _score_costs(
    findings: Sequence[Finding], predicted: dict[str, Any], labels: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Scored on the binary decision, with exact-label agreement reported beside it.

    The binary question -- print this finding or not -- is the one the tool acts
    on, and it is the only one both signals can answer: structural equivalence
    has no vocabulary for `convention`, so grading it multi-class would penalise
    it for a distinction it was never asked to draw. Exact agreement is still
    worth seeing for the model, which does have that vocabulary, so both are
    reported rather than one being chosen.
    """
    tp = fp = fn = tn = 0
    exact = 0
    disagreements = []
    for f in findings:
        truth = labels[f.id]["label"]
        guess = predicted.get(f.id)
        exact += guess == truth
        if guess == "junk_drawer" and truth == "junk_drawer":
            tp += 1
        elif guess == "junk_drawer":
            fp += 1
            disagreements.append({"id": f.id, "said": guess, "label": truth})
        elif truth == "junk_drawer":
            fn += 1
            disagreements.append({"id": f.id, "said": guess, "label": truth})
        else:
            tn += 1
    n = len(findings)
    return {
        "n": n,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "agreement": (tp + tn) / n if n else None,
        "exact_label_agreement": exact / n if n else None,
        "disagreements": disagreements,
    }


def _score_splits(
    findings: Sequence[Finding],
    said: dict[str, dict[str, Any]],
    labels: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Did the model pick the candidate the label picked?

    Scored three ways because they fail differently: agreeing on *which* cut,
    agreeing on whether to cut *at all*, and -- the one plan.md actually cares
    about -- whether the name it proposed is one of the words that mean "I could
    not name this".
    """
    exact = same_decision = 0
    unnameable = 0
    rows = []
    for f in findings:
        truth = labels[f.id].get("choice")
        guess = said.get(f.id, {}).get("choice")
        name = (said.get(f.id, {}).get("name") or "").strip().strip("/").lower()
        exact += guess == truth
        same_decision += (guess is None) == (truth is None)
        unnameable += name in {"utils", "helpers", "misc", "common", "shared", "lib", "core"}
        rows.append({"id": f.id, "said": guess, "label": truth, "name": name or None})
    n = len(findings)
    return {
        "n": n,
        "exact_choice": exact,
        "exact_rate": exact / n if n else None,
        "same_decision": same_decision,
        "same_decision_rate": same_decision / n if n else None,
        "unnameable_names": unnameable,
        "detail": rows,
    }


# --- CLI --------------------------------------------------------------------

app = typer.Typer(add_completion=False, help=__doc__)


def _load(
    lang: str | None,
    repos: list[str],
    c: float,
    splice: bool,
    reroot: bool,
    freeze: Sequence[str] = (),
    exclude: Sequence[str] = (),
) -> list[Finding]:
    graphs = load_graphs(lang, repos)
    if not graphs:
        raise typer.BadParameter("no extracted graphs matched --lang/--repo")
    out: list[Finding] = []
    for graph in graphs:
        g = filter_nodes(graph, list(exclude))
        g = reroot_graph(g) if reroot else g
        if splice:
            g = splice_all_barrels(g)
        out.extend(build_findings(g, c, freeze))
    return out


@app.command()
def build(
    output: Annotated[Path, typer.Option(help="directory for this run's artifacts")],
    lang: Annotated[str | None, typer.Option(help="only include repos in this language")] = None,
    repo: Annotated[list[str], typer.Option(help="only include these repos; repeatable")] = [],
    c: Annotated[float, typer.Option(help="bits a container must save to justify existing")] = (
        DEFAULT_C
    ),
    splice_barrels: Annotated[bool, typer.Option()] = True,
    reroot: Annotated[bool, typer.Option()] = True,
    freeze: Annotated[
        list[str],
        typer.Option(help="glob of convention-governed node ids to stop asking about"),
    ] = [],
    exclude: Annotated[
        list[str], typer.Option(help="glob of node ids to drop from the graph entirely")
    ] = [],
    include_equivalence: Annotated[
        bool, typer.Option(help="show the deterministic signal to the adjudicator too")
    ] = False,
    baseline: Annotated[
        Path | None,
        typer.Option(help="accepted finding ids; exit non-zero if the run adds any"),
    ] = None,
    update_baseline: Annotated[
        bool, typer.Option(help="rewrite --baseline from this run instead of checking it")
    ] = False,
) -> None:
    """Write the candidate set: one packet per finding, ready to adjudicate.

    This is the whole CI story, and it deliberately involves no model: pricing
    the tree is deterministic and offline, so a repository whose structure has
    not changed produces no new questions and needs no adjudicator at all.
    plan.md's third escape hatch -- ratchet, not threshold -- is `--baseline`,
    and it works on finding *ids* rather than a count because "one finding
    appeared and one was fixed" has to fail.
    """
    findings = _load(lang, repo, c, splice_barrels, reroot, freeze, exclude)
    output.mkdir(parents=True, exist_ok=True)
    write_findings(findings, output / "findings.jsonl")
    prompts = output / "prompts"
    prompts.mkdir(exist_ok=True)
    for f in findings:
        (prompts / f"{_slug(f.id)}.md").write_text(
            prompt(f, include_equivalence), encoding="utf-8"
        )
    counts = Counter(f.kind for f in findings)
    typer.echo(f"{len(findings)} findings ({counts['costs']} costs, {counts['split']} split)")

    if baseline is None:
        return
    added, removed = compare_baseline(findings, baseline)
    if update_baseline:
        write_baseline(findings, baseline)
        typer.echo(f"baseline updated: +{len(added)} -{len(removed)} -> {baseline}")
        return
    for finding_id in removed:
        typer.echo(f"  fixed: {finding_id}")
    if not added:
        typer.echo("no new findings")
        return
    for finding_id in added:
        typer.echo(f"  NEW: {finding_id}")
    typer.echo(
        f"{len(added)} new finding(s) to adjudicate; "
        f"see {output / 'prompts'} or run `adjudicate run`"
    )
    raise typer.Exit(1)


@app.command()
def run(
    output: Annotated[Path, typer.Option(help="directory holding findings.jsonl")],
    model: Annotated[str, typer.Option(help="model id for the adjudicator")] = "claude-haiku-4-5",
    backend: Annotated[str, typer.Option(help="cli | none")] = "cli",
    include_equivalence: Annotated[bool, typer.Option()] = False,
    kind: Annotated[str | None, typer.Option(help="only adjudicate this finding kind")] = None,
) -> None:
    """Ask an adjudicator every question in findings.jsonl; write verdicts.json."""
    findings = [f for f in read_findings(output / "findings.jsonl") if kind in (None, f.kind)]
    if backend == "none":
        raise typer.BadParameter("--backend none has nothing to run; see `build`")
    verdicts = adjudicate(findings, cli_backend(model), include_equivalence)
    path = output / f"verdicts-{model}.json"
    path.write_text(json.dumps(verdicts, indent=2, sort_keys=True), encoding="utf-8")
    failed = sum(1 for v in verdicts if "error" in v)
    typer.echo(f"{len(verdicts)} verdicts ({failed} failed) -> {path}")


@app.command(name="score")
def score_cmd(
    output: Annotated[Path, typer.Option(help="directory holding findings.jsonl")],
    verdicts: Annotated[Path | None, typer.Option(help="verdicts JSON to score")] = None,
    labels: Annotated[Path, typer.Option(help="hand-labelled ground truth")] = LABELS_PATH,
) -> None:
    """Score both signals against the labels and write score.json."""
    findings = read_findings(output / "findings.jsonl")
    # Falls back to the recorded run so `build` then `score` reproduces the
    # numbers in FINDINGS.md without a model or a credential.
    path = verdicts or (output / "verdicts-claude-haiku-4-5.json")
    if not path.is_file():
        path = RECORDED_VERDICTS
    rows = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    result = score(findings, rows, load_labels(labels))
    (output / "score.json").write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
    )
    for rule, s in result["equivalence"].items():
        frozen = result["frozen"][rule]
        typer.echo(
            f"costs / equivalence({rule}): P={_pct(s['precision'])} R={_pct(s['recall'])} "
            f"({s['true_positive']} true, {s['false_positive']} false of {s['n']}) "
            f"| conventions declared: P={_pct(frozen['precision'])} of {frozen['n']}"
        )
    if not rows:
        typer.echo(f"no verdicts at {path}; the model columns are empty, not zero")
        return
    model = result["model"]["costs"]
    typer.echo(
        f"costs / model: P={_pct(model['precision'])} R={_pct(model['recall'])} "
        f"exact={model['exact_label_agreement']:.0%} of {model['n']} adjudicated"
    )
    for rule, s in result["model"]["equivalence_on_the_same_rows"].items():
        typer.echo(
            f"costs / equivalence({rule}) on those same {s['n']}: "
            f"P={_pct(s['precision'])} exact={_pct(s['exact_label_agreement'])}"
        )
    split = result["model"]["split"]
    typer.echo(
        f"split / model: picks the labelled candidate {split['exact_choice']}/{split['n']}, "
        f"agrees whether to cut {split['same_decision']}/{split['n']}, "
        f"{split['unnameable_names']} unnameable names"
    )


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _slug(finding_id: str) -> str:
    return finding_id.replace("/", "_").replace(":", "__") or "root"


def write_findings(findings: Sequence[Finding], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for finding in findings:
            f.write(json.dumps(asdict(finding), sort_keys=True) + "\n")


def compare_baseline(findings: Sequence[Finding], path: Path) -> tuple[list[str], list[str]]:
    """(new ids, ids no longer raised) against an accepted baseline.

    A missing baseline is an empty one, so the first run on a repository reports
    everything as new. That is the honest reading -- nothing has been accepted
    yet -- and it is also the prompt to run `--update-baseline` once.
    """
    accepted = (
        set(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else set()
    )
    current = {f.id for f in findings}
    return sorted(current - accepted), sorted(accepted - current)


def write_baseline(findings: Sequence[Finding], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sorted(f.id for f in findings), indent=2) + "\n", encoding="utf-8"
    )


def read_findings(path: Path) -> list[Finding]:
    return [
        Finding(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_labels(path: Path = LABELS_PATH) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    app()
