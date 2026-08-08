# Reworking PR 7 into four small PRs

## Context

PR 7 (branch `claude/pr7-llm-optional-validation-eccf0f`, pushed, **not merged**) answered its
question — a cheap model separates real findings from false positives at 75% precision — but got
four things wrong that only became visible once it existed:

1. **The skill only works inside this repo.** It shells out to `uv run python -m report.adjudicate`,
   and the tool itself cannot analyse any repository that is not a pinned row in
   `corpus/manifest.toml`.
2. **Three checked-in files where there should be one config.** `report/baseline.json` (the CI
   ratchet), `report/labels.json` (ground truth) and `report/verdicts/*.json` (a recorded run) are
   three views of "how should this repo's findings be treated", which is one per-repo config file.
3. **The eval measured the wrong run.** It scored a model on the same finding set the tool emits
   after configuration. The question worth asking is whether a model could have *written* the
   config: run with no config at all, get the full finding set, and check the model's
   recommendations against what the repo's config actually says.
4. **`unclear` was scored as a miss.** It is a legitimate model answer and a strictly better one
   than a confident error. It must never appear in a config, but it must rank above `wrong`.

This plan lands 1–4 minus the distribution half. **Scope: PRs A–D, then reassess.** PRs E and F
(making the tool runnable against an arbitrary repo, and shipping the skill as a plugin) are
specified at the end but not started.

**The current branch stays unmerged as a reference** — its `labels.json` and recorded verdicts are
the raw material for the corpus configs in PR C. PRs A–D are cut fresh from `main`.

---

## The vocabulary fix

`costs` and `split` are not parallel words, which is why the pair reads wrong. `split` names an
action; `costs` names a *pricing verdict* (`model/placement.py:288`, `earns` / `neutral` / `costs`)
that the finding kind borrowed.

The two operations are exact inverses, and `model/placement.py` already prices them as a symmetric
pair — `dissolve_bits` (line 272) and `extract_bits` (line 475):

| finding | operation | inverse of | priced by |
| --- | --- | --- | --- |
| **`merge`** | delete this directory; its children move up into the parent | `split` | `dissolve_bits` |
| **`split`** | create a subdirectory; a subset of the parent's children move down | `merge` | `extract_bits` |

`merge(split(P, S)) = P` exactly. So: **rename the finding kind `costs` → `merge`.** The pricing
verdict `earns` / `neutral` / `costs` stays as it is in `containers.csv` — it is about bits and it is
correct. The sentence that separates them is *"a directory whose pricing verdict is `costs` produces
a `merge` finding"*.

Finding ids become `merge:<dir>` and `split:<dir>`. The root renders as `merge:.` / `split:.`, not a
trailing colon.

---

## PR A — the structural-equivalence signal

Lift `model/equivalence.py` + `tests/test_equivalence.py` out of the spike unchanged, plus the two
Jaccard columns in `containers.csv` (`report/run.py::write_containers_csv`).

**No precision numbers.** Those depend on ground truth, which does not exist until PR C. What this
PR can state, and should, is the mechanism: `FINDINGS.md`'s out-Jaccard rule was validated on seven
hand-picked directories that all sit at 0.000 or 1.000, and date-fns's 87 `locale/*/_lib`
directories sit at **out 0.42 / in 1.00** — a fixed per-instance layout is equivalent from the
importer's side, not the target's. `tests/test_equivalence.py` already pins both facts.

Files: `model/equivalence.py` (new), `tests/test_equivalence.py` (new), `report/run.py` (two
columns), `FINDINGS.md` (the correction, no numbers).

**Acceptance:** the six `FINDINGS.md` table values reproduce exactly; `structural_equivalence` agrees
with `container_equivalence` on every directory of every corpus repo.

---

## PR B — one coordinate system, one vocabulary

Small, surgical, and a hard prerequisite for PR C: **config ids must not move when the config
changes.** Two defects make them move today.

**1. `reroot` is derived from the surviving node set**, so an exclude glob renames every finding in
the repo. Measured on tanstack-router:

```
config-free                            ->  router-core/src/ssr/serializer
--exclude 'packages/react-router/**'   ->  ssr/serializer
```

**Fix:** derive the reroot prefix from the *unfiltered* graph, then filter. The pipeline order in
all three CLIs changes from `filter → reroot → splice` to `reroot → filter → splice`. Bit costs are
unchanged either way — the prefix is provably a maximal single-child chain — so only ids move.

**2. `--exclude` globs match pre-reroot ids; `--freeze` globs match post-reroot ids.** Two glob
lists in two coordinate systems, about to be placed side by side in one TOML file. The reorder
collapses them: after it, **every glob and every finding id is a rerooted id**. Migration risk is
nil — the only anchored glob in current use (`locale/*/_lib/**`) is already post-reroot.

**3. Rename `costs` → `merge`** per the section above, so ids change exactly once rather than twice.

Files: `model/graph.py::reroot` (accept an explicit prefix), `report/run.py`, `report/calibrate.py`,
`report/adjudicate.py` (pipeline order + kind name), regenerate `report/out/*`.

**Acceptance:** every number in `tests/test_corpus_metrics.py` is unchanged (this is the proof that
the reorder is id-only); tanstack-router's ids are identical with and without an exclude glob.

---

## PR C — the per-repo config, replacing three files

### Schema

`corpus/configs/<repo>.toml` for the pinned corpus (whose checkouts are gitignored); the identical
file lives at `<repo>/dep-structure.toml` for a real repository once PR E exists.

```toml
version = 1
repo    = "date-fns"

[analysis]
c              = 8.0
splice_barrels = true
root_prefix    = "pkgs/core/src"   # optional; default = derived from the pinned graph

[[exclude]]                        # removes nodes; CHANGES which findings exist
glob = "**/test.ts"
why  = "date-fns colocates one test.ts beside every index.ts. Not part of the system being measured."

[[freeze]]                         # keeps nodes in the cost; forbids placement; removes only rows
glob = "locale/*/_lib/**"
why  = "87 locale directories each hold the identical five parts assembled by one index.ts."

[[finding]]
id      = "merge:_lib"
verdict = "junk_drawer"            # merge: junk_drawer (do it) | taxonomy (don't)
why     = "Twelve unrelated internals under one underscore name. Seven components, no internal edges."

[[finding]]
id      = "split:."
verdict = "accept"                 # split: accept | reject
name    = "live"
members = ["_spinners.py", "filesize.py", "live.py", "progress.py", "spinner.py", "status.py"]
why     = "progress imports live, spinner and filesize; status imports live and spinner."
```

**`members`, never `rank`.** `Container.splits` sorts by `(bits, members)`, so adding one file
re-prices every candidate and can reorder them — `rank = 1` would silently start accepting a
different cut. `check` re-derives the candidates and requires some candidate's member set to equal
`members` exactly; if none does, the finding is **drifted** and that is an error.

**Illegal in a config, rejected by validation:** `verdict = "unclear"` (an abstention is not a
decision) and `verdict = "convention"` (a convention means *"I should have written a glob here"* —
validation points at `[[freeze]]`). Empirically sound: under the fully configured corpus, **zero of
the six surviving `merge` findings are conventions**; all 91 were absorbed by two globs.

### The three finding sets, and which is which

Measured on the current corpus:

| set | what | count |
| --- | --- | --- |
| **S0** | config-free | 117 (98 merge, 19 split) |
| **S1** | excludes applied, freeze not | 105 |
| **S2** | fully configured | 19 (6 merge, 13 split) |

Two non-obvious facts that decide the design:

- **S0 is not a superset.** `merge:locale/eo/_lib` exists in S1 and not S0: config-free it *earns*
  +1.07 bits; excluding `**/test.ts` moves it to −3.68 and makes it a finding. **Exclude creates
  findings as well as destroying them.**
- **Freeze is provably a pure row filter.** `containers()` computes everything from the whole graph
  and then skips frozen directories, so `S2 ⊆ S1` exactly and surviving rows are bit-identical.

**The completeness invariant is defined over S1**: every S1 finding is either *accounted* (has a
`[[finding]]`), *frozen* (in S1 \ S2, i.e. covered by a freeze glob), or **unaccounted** — and
unaccounted is the CI gate. S0 is wrong because it is not complete and would cost date-fns 97
permanent entries; S2 is wrong because it cannot tell "I froze this deliberately" from "it never
existed", and an unchecked declaration is a comment.

### `report/check.py` — the gate that replaces `baseline.json`

Runs S1 once, derives S2 by filtering, classifies. **`--audit` additionally runs S0** (+3 s; the
whole corpus is 13.3 s config-free vs 10.1 s excluded, so cost decides nothing here) to separate the
two causes of a stale entry: still present in S0 → *hidden by an exclude glob*; absent from S0 too →
*genuinely fixed*. `--audit` also reports, per exclude rule, findings removed **and created** — the
`locale/eo/_lib` case, which is otherwise invisible.

Stale is a **warning** by default: a gate that fails when you fix something teaches people to stop
fixing things. `--strict` promotes it, and corpus CI runs `--strict --audit`.

The thing a baseline structurally could not express, and the strongest argument for the redesign:
**a baseline cannot tell "accepted because it's a false positive" from "accepted because I haven't
fixed it yet."** The config can, because the verdict says which. `check` reports both — *acknowledged
defects* (`junk_drawer` + `accept`, real findings not yet acted on, ratchetable down) and
*dismissals* (`taxonomy` + `reject`, the tool being wrong).

Exit codes: `0` ok · `1` unaccounted (the ratchet fired) · `2` config invalid · `3` could not run.

**No TOML writing.** `tomllib` is read-only and that is the right constraint: the config's value is
its hand-written `why` prose, and a tool that rewrites it eats comments. `check --emit-missing`
prints paste-ready `[[finding]]` blocks with `verdict = "TODO"` and the evidence as `#` comments to
**stdout**. A ~30-line writer over `str | int | bool | list[str]`, which can never corrupt a file
because it never opens one for writing.

### Flag collapse

`--exclude`, `--freeze`, `--splice-barrels`, `--reroot` are declared three separate times across
`report/run.py`, `report/calibrate.py` and `report/adjudicate.py`. They **disappear** — they become
config fields. Each CLI keeps `--config PATH`, `--no-config` (needed for S0 runs) and `--c` (a
policy dial that `calibrate.py` sweeps, so it cannot be config-only). 15 option declarations → 9.

### Migration and deletions

`report/labels.json`'s 117 entries → 7 config files: strip the repo prefix from the key,
`label` → `verdict`, `why` → `why`. The 91 `convention` rows and the 4 one-function rows are
**discarded**, replaced by one exclude and one freeze glob. rich's accept re-keys from `rank` to
`members`. Every one of the 19 configured findings already has a label, so nothing is invented.

The single `unclear` (`merge:_lib/test`) is probably moot rather than a hard decision: its 142
external entries are all `test.ts` files, so under `**/test.ts` it flips from −113.3 to +10.7 and
stops being a finding — which is itself evidence that `unclear` was a symptom of a missing exclude.

**Deleted:** `report/baseline.json`, `report/labels.json`, `report/verdicts/`, and
`compare_baseline`/`write_baseline` + the `--baseline`/`--update-baseline` flags.

New: `report/config.py` (`RepoConfig`, `load_config`, `validate`, `prepare`, `freeze_globs`),
`report/check.py`, `corpus/configs/*.toml` × 7, `tests/test_config.py`, `tests/test_check.py`.
CI's `adjudicate build --baseline` step becomes `python -m report.check --strict --audit`.

---

## PR D — the eval, with the config as ground truth

**Run config-free (S0, 117 findings); score against what the config implies.** Expected verdict for
an S0 finding, derived exactly and mechanically:

| condition | expected |
| --- | --- |
| has a `[[finding]]` entry | that verdict |
| absent from S1 (an exclude glob killed it) | `convention` |
| in S1 \ S2 (a freeze glob killed it) | `convention` |
| none of the above | a config bug — `check` would have failed; reported separately |

So the adjudication vocabulary is deliberately **larger** than the config vocabulary: `junk_drawer |
taxonomy | convention | unclear` observed, `junk_drawer | taxonomy` writable, and the glob *implies*
`convention` without ever spelling it.

### Three-tier scoring

Replaces precision/recall as the headline:

| outcome | condition |
| --- | --- |
| **correct** | model verdict == expected |
| **unclear** | model said `unclear` — an abstention, better than being wrong |
| **wrong** | anything else |

Ranked by `(wrong ascending, correct descending)`. Precision/recall on `junk_drawer` stay as a
secondary view so the deterministic signal remains comparable.

### Through the skill, not a bare prompt

The question is whether *the model plus the skill* reproduces the config, so add a `skill` backend
alongside `cli`: it invokes an agent with the skill loaded and hands it the finding id plus its
packet, rather than inlining the criteria in the prompt. This also makes the skill's own text a
measured artifact.

**Known constraint:** `claude -p` could not authenticate in this environment (the stored OAuth token
is stale), which is why PR 7's recorded run went through subagents. PR D should re-run through the
CLI once a login is available; until then the harness must report which transport produced a number.

Files: `report/adjudicate.py` (score against config, 3-tier, `skill` backend, drop the label path),
`.claude/skills/dep-structure-review/SKILL.md` (merge/split vocabulary, `unclear` allowed and
explicitly preferred over guessing), `FINDINGS.md`.

**Acceptance:** every S0 finding has a mechanically derived expected verdict; the reported triple
sums to 117; the skill and packet paths score separately.

---

## Deferred: PRs E and F

Not started. Specified so the reassessment has something to price.

**PR E — run against an arbitrary repo.** Blockers found: no `[build-system]` and no
`[project.scripts]` (the project is not installable); `RepoEntry.checkout_path` is hard-coded to
`corpus/checkouts/<name>`; `Graph.commit` must match `^[0-9a-f]{7,40}$`, so a dirty or non-git
directory cannot construct a `Graph`; nothing discovers `roots`; `load_graphs` enumerates the
manifest rather than the graphs directory; `extractors/ts/` needs an `npm ci` that nothing
automates and CI never runs. Also drop `matplotlib` — declared, zero imports. `extract.mjs` already
takes an arbitrary path, and `model/` has no corpus coupling at all, so the seam is exactly
`report/run.py::load_graphs`. This also unblocks PR 6 (meridian2).

**PR F — the plugin.** Skills *are* usable across repos: `~/.claude/skills/` is user-scoped,
`.claude/skills/` is project-scoped, and a **plugin** is the distributable form
(`.claude-plugin/plugin.json` + `skills/<name>/SKILL.md`, listed in a `marketplace.json`, installed
via `claude plugin marketplace add` / `claude plugin install`). Plugins cannot declare a Python
dependency, so the skill must call `dep-structure` on PATH and the plugin documents
`uv tool install`. The same `SKILL.md` loads in the Agent SDK unchanged.

---

## Verification

Per PR, in order:

- **A** — `uv run pytest tests/test_equivalence.py`; the six `FINDINGS.md` values reproduce exactly.
- **B** — `uv run pytest tests/test_corpus_metrics.py` unchanged (proves the reorder is id-only);
  build ids for tanstack-router with and without `--exclude 'packages/react-router/**'` and diff —
  must be identical.
- **C** — `uv run python -m report.check --strict --audit` exits 0 on all seven repos; delete a
  `[[finding]]` and confirm exit 1 with the id named; set `verdict = "unclear"` and confirm exit 2;
  confirm `--audit` reports `merge:locale/eo/_lib` as created-by-exclude and absorbed by the freeze
  glob.
- **D** — `report.adjudicate score` against the seven configs; the triple sums to 117 and every
  finding has a derived expectation; re-run through `claude -p` if a login is available.
- **Throughout** — `uv run pytest` and `uv run ty check` green; report artifacts regenerated on
  Windows via `main()` from Python, not glob flags through the shell (Click expands them).

## Risks

- **Ground truth is still the author's.** The configs are written by the same person who reads the
  evidence. The mitigation is that a config is a *product artifact* that must stand on its own as
  the file a maintainer would commit — not a label file that exists only to be scored against. Say
  so in `FINDINGS.md` rather than implying independence.
- **Split rejections key on nothing** (accepted, per your call). A `reject` silences that
  directory's split question until someone edits the config, so a genuinely new proposal inside a
  rejected directory is missed. Documented as a known hole; the fix, if it is ever worth it, is
  folding a candidate-set digest into the id.
- **Deleting the recorded verdicts** (your call) makes `FINDINGS.md`'s numbers a dated experiment
  record rather than something a test can pin. A scoring-code change can therefore move the
  published numbers silently. `FINDINGS.md` must carry the model id, date and transport for every
  quoted figure.
- **PR B moves every finding id.** It must land before the configs are written, or they are written
  twice.
