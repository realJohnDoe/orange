---
name: dep-structure-review
description: Adjudicate findings from the dependency-structure linter — decide whether a flagged directory is a junk drawer worth dissolving or a legitimate taxonomy, pick among proposed subdirectory splits, and name the ones worth doing. Use when CI reports new structure findings, when a `findings.jsonl` / `containers.csv` / `splits.csv` needs reviewing, or when asked to review a repository's directory structure against its import graph.
---

# Adjudicating dependency-structure findings

The linter measures; it cannot judge. It prices every directory against the
import graph and flags the ones that cost addressing bits, but the bit count
cannot tell zod's `v4/locales` (52 legitimate per-language tables) from vite's
`shared` (four unrelated clusters in a bag). That judgement is this skill's job,
and it is the only step between the tool's output and advice a maintainer would
act on.

**PR 7 measured how well this works.** Claude Haiku 4.5, given only the packets
this skill hands you, reached 75% precision and 100% recall on the actionable
verdict — against 43% precision for the best purely-graph-based signal on the
same rows. Use the cheapest model you have; do not skip the judgement step.

## When to run

Run when the linter reports findings the baseline did not already contain. It is
a per-finding cost, not a per-commit one: a repository whose structure has not
changed produces no new findings and needs no adjudication at all.

## Producing the questions

```bash
uv run python -m report.adjudicate build --output report/out/adjudication
```

This writes `findings.jsonl` (one record per question) and `prompts/*.md` (the
same records rendered as self-contained packets). Pass `--freeze` for
convention-governed subtrees and `--exclude` for anything not part of the system
being measured — see "Declare conventions first" below, which is worth more than
any amount of care in the judging.

## Answering

For each finding, read its packet and answer its contract. Two kinds:

**`costs` — should this directory exist?** Answer `junk_drawer`, `taxonomy`,
`convention` or `unclear`. Only `junk_drawer` is a finding worth printing; the
other three all mean leave it alone.

- **junk_drawer** — the children are unrelated to each other and to any single
  idea. Dissolve it. Tells: many components, high external traffic from many
  different directories, no target imported by more than a couple of children,
  and a name like `helpers`, `utils`, `shared`, `common`, `misc` or `_lib`.
- **taxonomy** — the children belong to one idea, either as parallel instances
  of a category (per-locale tables, per-format parsers, per-platform shims) or
  as a small cohesive module. Parallel instances count *even though they never
  import each other* — that is exactly why the cost function misreads them. The
  tell is a target that nearly every child imports.
- **convention** — the layout is dictated by a repo-wide convention rather than
  by this directory's contents: one directory per exported function, test
  fixtures, generated code, a fixed set of files every instance must provide.
  The giveaway is that many sibling directories have the identical shape. These
  belong in a `--freeze` config, not in a report.
- **unclear** — the evidence supports none of the above. Two files with nothing
  in common is usually this rather than a junk drawer.

**`split` — which of these subdirectories, if any?** Pick a candidate's rank, or
`null` to reject them all, and name the chosen subset.

**The naming test is the decision procedure, not a decoration.** If the best name
you can find for the files a cut moves is `utils`, `helpers`, `misc`, `common`,
`shared`, `lib` or `core`, the cut does not carve a real module — reject it. A
cut that names itself (`live` for rich's live/progress/spinner/status cluster) is
the shape you are looking for.

**Naming is necessary and not sufficient.** The corpus's sharpest false positive
is a proposal to group date-fns's eight `en-*` locales as `en/`: perfectly
nameable, and still wrong, because `date-fns/locale/en-US` is a published entry
point and the cut is a breaking API change. Before accepting, check that the
files being moved are not published paths, framework-owned locations, or
otherwise addressed from outside the repository. Over-acceptance is the measured
failure mode here — Haiku accepted five cuts a maintainer would reject and
rejected none it should have taken.

## Reporting

Emit one line per accepted finding, with the reason, and stay quiet about the
rest. A `convention` verdict is worth surfacing once as a suggested `--freeze`
pattern rather than repeatedly as a finding.

## Declare conventions first

The single largest term in this tool's usefulness is not the judging, it is the
config. On the reference corpus 91 of 98 `costs` rows were one of two date-fns
conventions; declaring them cut the list from 98 rows to 6 and lifted precision
from 3% to 50% before any model was involved. If you find yourself adjudicating
dozens of near-identical directories, stop and write a freeze pattern instead.

## Do not

- Do not read the repository to answer. The packet is self-contained by design,
  and a judgement that needed the source is one the shipped tool cannot make.
- Do not accept a split because its bit saving is large. The corpus's largest
  proposal (−652 bits, zod's `v4`) is "move everything down one level", which is
  not a module.
- Do not treat `costs` as an instruction. It is the census verdict that raised
  the question, not an answer to it.
