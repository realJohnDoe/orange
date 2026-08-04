# Findings

What running the thing taught us. The model, the reasoning behind it and the PR queue are in
[plan.md](plan.md); per-repo numbers and per-repo lessons are in [REPOS.md](REPOS.md). This file is
the middle layer: results that are about the *method* rather than about any one repo, including the
ones that overturned an earlier conclusion.

**Two conventions.** Findings that were later corrected are kept with the correction attached
rather than deleted, because in every case so far the reason a wrong conclusion looked right is
more useful than the conclusion. And every number is quoted with its variant — raw extraction,
spliced, or spliced-and-re-rooted — because identically-named columns differ by up to 3× between
them (see the warning at the top of [REPOS.md](REPOS.md)).

---

## Where the hypothesis stands

> Well-engineered repositories already place files where this rule would place them.

**Partly, and the interesting part is where the model has nothing to say.**

Under the **integer cost**, all four TypeScript repos place files far better than chance, on both
the mean-cost and cohesion axes at once. That was the Phase 0 headline.

Under the **bit cost** — the actual objective — the picture is three-way rather than pass/fail.
Of the directories these repos have built, 26–53% earn addressing bits, 2–13% actively cost them,
and *the rest are exactly neutral*: the dependency graph has no opinion on them in either
direction. So the hypothesis is neither confirmed nor falsified so much as bounded. The graph can
identify a small set of genuinely bad boundaries; it cannot derive a tree, because most boundaries
real repos draw are taxonomic and the graph is blind to taxonomy.

The file-level picture is weaker still — 40–91% of files sit somewhere the objective would move
them — but two of the three causes turned out to be artifacts we fixed (root-parking, self-edges)
and the third is a known pathology of the objective rather than a fact about the repos. See
"Phase 0 results" and "Open risks".

---

## What we've learned

**The Python corpus is degenerate — and PR 4c made it exact.** After re-rooting, the three Python
repos have **0, 1 and 2 directories between them**: requests has no tree at all (19 files, all
siblings, mean integer cost exactly 0.000), rich has `_unicode_data`, flask has `json/` and
`sansio/`. There is nothing here for a metric *about an existing tree* to measure.

**But the tool is not silent on them, and finding that out was a bug fix.** `containers()` skipped
the repo root, on the reasoning that the root cannot be dissolved and exists in every candidate
layout. True — and it meant the *only* structural question a flat repo has, "should the root gain a
subdirectory", was never asked. The root is now in the census with `dissolve_bits = None` and the
verdict `root`, priced for splits only. requests turns out to have 3 paying candidates and rich 3
(at any `C > 0`; 2 and 3 at the default `C = 8`), including a −60-bit proposal to pull `live.py`,
`progress.py`, `spinner.py`, `status.py`, `_spinners.py` and `filesize.py` into one directory — a
coherent cluster in a repo this document had written off. Whether it is *good* advice is PR 7's
question, but "no opinion" was an artifact of
the census, not a result. The cause is not Python but *small library* Python: a package is both the unit of
distribution and the unit of import, so subdividing one is a breaking API change, and `__init__.py`
re-export makes flat cheap. The corpus was selected for entrypoint count and call-graph depth; the
axis that turned out to matter is directory-tree depth and branching, which is screenable from
`git ls-files` before extracting anything. See [REPOS.md](REPOS.md).

**The meridian2 correction.** `meridian2` is the repo the tool is meant to *optimize*, not ground
truth to calibrate the extractor or cost model against — "the tool disagrees with meridian2" is a
finding about meridian2, not evidence the extractor is wrong. Calibration comes from repos with an
independent reputation for being well-structured (zod, date-fns, vite, TanStack Router). This is
why the TS extractor and the meridian2 PR swapped places: reference repos first, optimization
subject last.

**Three real TS extractor hazards were found and fixed** (none were the grimp-style hazards
anticipated; all were TS/tooling-specific):

1. dependency-cruiser's `typescript` peer dependency resolved to v7.0.2 by default, outside the
   `>=2.0.0 <7.0.0` range dependency-cruiser 18.1.1 actually supports — this silently disabled all
   `.ts` parsing (zero modules found, no error). Fixed by pinning `typescript` to `^5.9.0`.
2. `cruise()`'s file/dir array is joined with `baseDir` unconditionally (`path.join` doesn't
   special-case an absolute second argument), so passing already-absolute root paths
   double-prefixed the checkout directory. Fixed by passing roots relative to `baseDir`.
3. vite's test suite has a type-only import into its own never-built `dist/node/module-runner`,
   several directories above the analyzed root. dependency-cruiser reports this as resolved
   (`couldNotResolve: false`) even though the file doesn't exist in a shallow checkout, so it
   reached `extract.py`'s in-scope-only `..`-escape invariant and crashed extraction. Fixed by
   checking in-scope membership (via a lenient slash-only normalization) *before* ever calling the
   strict normalizer — the strict `..`/absolute checks now only fire on paths already confirmed to
   be real analyzed files.

**The cross-workspace alias problem mostly evaporated.** tanstack-router has the exact shape PR 6
was written to solve for meridian2 — two roots (`router-core`, `react-router`) from two separate
packages in one workspace, one depending on the other by package name. It cruised cleanly with
**zero new code**: the generic per-root "alias a package's own `package.json` name to its root"
mechanism built for zod's self-references (`zod/v4`, `zod/v3`, …) resolved all 59
`react-router → router-core` edges correctly. The handful of subpaths behind conditional `exports`
(`@tanstack/router-core/isServer`) correctly failed to resolve rather than silently resolving
wrong — the safe failure mode. This meaningfully de-risks PR 6.

**Validation numbers, for reference.** These are the *raw extraction*: import statements as the
extractor saw them, before `metrics.edges()` deduplicates and before any transform. They are here
to show the extractor works, and they will not match [REPOS.md](REPOS.md), which reports the
spliced and re-rooted variant — zod is 608 statements here and 2085 edges there, because splicing a
barrel fans one edge out into everything behind it.

| repo | nodes | import statements | type-only | barrels | unresolved | node count matches `git ls-files`? |
| --- | --- | --- | --- | --- | --- | --- |
| zod | 286 | 608 | 165 | 4 | 22.8% | yes (286/286, excl. `.md`) |
| date-fns | 1495 | 4452 | 1019 | 4 | 11.3% | yes (1495 = 1494 `.ts` + 1 `.js`) |
| vite | 396 | 1085 | 383 | 8 | 27.8% | yes (396 = 400 tracked − 4 `node_modules` test fixtures, correctly excluded by `doNotFollow`) |
| tanstack-router | 116 | 476 | 238 | 3 | 15.6% | yes |

**The barrel-splice delta was validated ad hoc**, ahead of `model/graph.py::splice_barrels`
existing. A throwaway cycle-guarded splice, run against zod and date-fns (integer cost histograms):

| repo | unspliced | spliced |
| --- | --- | --- |
| zod | 56% / 43% / 1% / 0% | 13% / 86% / 0% / 0% |
| date-fns | 30% / 37% / 32% / 1% | 33% / 32% / 34% / 1% |

zod's huge shift confirms the theory: many edges only looked cheap because they stopped at a
barrel's face, and splicing exposes the real descent cost to what's behind it. date-fns barely
moves — its internal code mostly bypasses its own barrel already. The "if the delta is small,
that's a bug" check has a real answer now: date-fns's small delta is a repo-convention fact, not a
bug, confirmed by zod's large delta on the identical mechanism. **This is the acceptance test for
the real `splice_barrels`**: it should reproduce these two numbers exactly.

**The directory-cohesion metric earned its place as a diagnostic.** Also throwaway, but it found a
genuine, non-benign signal that cost alone cannot: zod's `v4/core/zsf.ts` shares zero edges with
the other 18 files in `core/`, unlike every other split in the corpus, which is the same benign
pattern as `rich/_unicode_data` (independent test files or per-locale data). vite additionally
surfaced 3 "genuine" splits — directories where the odd-one-out is a second real *cluster* of ≥2
files, not a lone singleton.

**Two known confounds in every number quoted above** — present in the raw corpus, and now
correctable with `report/run.py --exclude` (PR 4b; see "Confound correction" under Phase 0 results
below for the corrected zod/vite numbers):

- zod is 59% test files (170 of 286, under `*/tests/*`). Nearly every zod cohesion "split" is a
  test directory, which is structurally guaranteed to look incohesive (tests depend on their
  subject, never on each other) and is not informative either way. **The Phase 0 permutation
  numbers for zod are therefore partly measuring test placement.**
- date-fns has 937 of its 1196 directories holding exactly one file (median: 1). The
  `addDays/index.ts`-per-function convention makes directories into filename prefixes, not real
  containers, but the cost function still charges a face to cross each one — date-fns's cost
  numbers are partly measuring this convention, not coupling. (Under the bit cost this is not a
  confound but a *finding*: `log2(1) = 0`, so those directories carry no addressing information
  and cost `C` each.)

**Edges are per import *statement*, and must be deduplicated before measuring.** Found while
reproducing the recorded numbers in real code: a file that imports the same module twice — once
as `import type`, once for a value — appears twice in `imports`, and `type_only` records only the
type-only occurrence. That is a fact about the source text, not two dependencies. zod carries 7
such duplicates, tanstack-router 71 (17% of its edges, nearly all `react-router → router-core`).
Deduplicating is what makes the shipped code land on the recorded means exactly (0.44 / 1.04 /
0.39 / 0.28) rather than 1–7% above them, so the ad-hoc scripts must have done the same. It is
done in `metrics.edges()` rather than at extraction, per extract-once: the cached graphs stay a
faithful record of what the extractor saw. An edge counts as type-only only when *every*
statement behind it is, since one value import is a real value dependency.

**The first bit-cost table lived here and has been removed.** It reported bits/edge, the entropy
floor, the compression ratio and the container count per repo, unspliced and unfiltered, and it
described itself as "a baseline to compare against once 4b removes the confounds, not yet evidence
of anything". 4b and 4c both landed, so the current per-repo numbers are in
[REPOS.md](REPOS.md) — and keeping the old ones here was actively harmful, because they carry the
same column names as REPOS.md's under a different variant: zod's compression ratio is 2.76 raw and
1.39 spliced-and-re-rooted, its edge count 601 against 2085. Two tables that disagree by 2–3× on
identically-named columns are a trap, not a record.

What that table was for, which still holds: date-fns had both the worst compression ratio and 1296
containers against 1495 files, so `C · |containers|` was 10368 bits of a 44074-bit objective. That
is the shape the model predicted, and 4c's named check confirmed it directly.

**The compression ratio's floor is above 1.0 in practice.** `H(v|u)` conditions on the importer
and so charges nothing for locating it, while the tree is one shared code that must address
importers and targets alike — a directory holding an importer and its `d` targets charges
`log2(d+1)` against a floor of `log2(d)`. So 1.0 is an asymptote, not a reachable score. The gap
shrinks with repo size and cancels out of cross-repo comparison, which is what the number is for.

---

---

## Phase 0 results

A permutation baseline (shuffle which file sits in which slot, holding tree shape/depth/size
fixed, ~200 iterations) run against every extracted repo, on the **integer** cost:

| repo | mean cost, real vs. shuffled | cohesion split-rate, real vs. shuffled | verdict |
| --- | --- | --- | --- |
| zod | 0.44 vs 1.54 | 50% vs 87% | far better than random |
| date-fns | 1.04 vs 2.04 | 21% vs 99% | far better than random |
| vite | 0.39 vs 2.44 | 59% vs 98% | far better than random |
| tanstack-router | 0.28 vs 1.28 | 50% vs 86% | far better than random |
| flask / requests / rich | — | — | too few multi-child directories to be informative (1–3 each) |

All four TypeScript repos place files dramatically better than chance, on both axes at once — the
hypothesis holds everywhere it's actually testable. The Python corpus never got to weigh in: it's
too shallow, exactly as predicted and confirmed empirically before any TS data existed. Go remains
untested (no modern toolchain available yet).

**Confound correction (PR 4b), real numbers only.** `report/run.py` now exists, so the two known
confounds above can actually be removed rather than just named. Re-running with
`--exclude '**/*.test.ts' --exclude '**/tests/**' --exclude '**/__tests__/**' --exclude
'**/test_*.py'`, unspliced, to stay comparable with the table above:

| repo | nodes, all vs. no-tests | mean cost, all vs. no-tests | split-rate, all vs. no-tests |
| --- | --- | --- | --- |
| zod | 286 vs. 116 | 0.44 vs. 0.53 | 50% vs. 33% |
| vite | 396 vs. 141 | 0.39 vs. 0.40 | 59% vs. 71% |
| date-fns | 1495 vs. 1495 | 1.04 vs. 1.04 | 21% vs. 21% |
| tanstack-router | 116 vs. 116 | 0.28 vs. 0.28 | 50% vs. 50% |

date-fns and tanstack-router are unchanged — neither has files matching these globs inside its
analyzed root, so they were never carrying the test confound to begin with. zod and vite are not:
dropping zod's 170 test files raises its mean cost (tests colocated with their subject were mostly
free, cost-0 edges, and pulled the average down) while *lowering* its cohesion split-rate from 50%
to 33% — confirming the predicted mechanism exactly ("nearly every zod cohesion split is a test
directory"). Once test placement stops being counted as a directory split, zod's real code is
*more* cohesive than the raw number suggested, not less. vite moves the same two ways for the same
reason, more mildly.

**What this does not do: re-run the "vs. shuffled" comparison.** No shuffled numbers exist for the
excluded-test graphs, so this correction updates the *real* half of the table only. That comparison
is now **not going to be made**: PR 4d is dropped, because 4c's absolute tests turned out to be far
more informative than the margin over a shuffle, and the permutation baseline measures the integer
cost rather than the objective. The shuffled column above is a historical record.

**Caveat that motivates PR 4c:** random is a weak opponent. Beating a shuffle says the layout is
not arbitrary; it does not say the layout is *good*. The demanding version is local optimality —
for each file, does moving it to any other existing directory reduce total cost? — and that is
also the artifact the shipped tool emits.

**Local optimality (PR 4c), and it is a much worse result than the permutation baseline.** Spliced
and re-rooted, at any `C > 0` (the number is `C`-independent — see "PR 4c — as built"):

| repo | locally optimal, all files | with tests excluded |
| --- | --- | --- |
| zod | 60% | 11% |
| vite | 44% | 26% |
| date-fns | 27% | 27% |
| tanstack-router | 9% | 9% |
| flask / requests / rich | 52% / 100% / 71% | unchanged |

Against a shuffle these four repos looked decisively non-arbitrary on both axes. Against the
objective itself, **a large share of their files sit in a directory the bit cost would move them
out of** — and zod, the best of them on the raw corpus, drops to 11% once its test files stop
propping the number up.

Two artifacts were found in this number and both are now corrected in the table above; a third is
not correctable and limits what the number can mean.

1. **Root-parking (fixed by `reroot`).** The analyzed subtree sits under a single-child chain
   (`packages/zod/src`), leaving the root with branching 1 and therefore nearly free. 176 of vite's
   247 movers and 659 of date-fns's wanted the repo root before the fix; 9 and 12 after. vite's
   score moved 36% → 44%, date-fns's 12% → 27%.
2. **Self-edges** in the objective but not in the deltas — see the PR 4c section.
3. **Large-directory eviction pressure, which is inherent.** Evicting *any* member of a
   `k`-child directory shaves `log2(k) → log2(k−1)` off every edge addressing its siblings, so a
   large directory is never locally optimal and every one of its members individually wants out.
   That is why 15 individual locale files want out of zod's `v4/locales/`. Some of the remaining
   movers are the model finding real structure — its top picks are `core/errors.ts`, `core/util.ts`
   and `core/checks.ts`, exactly zod's most-shared modules, so "shared things float up" works — and
   some are that pressure. File-level local optimality cannot separate them, which is the sharpest
   argument yet for the symbol level, and the reason the per-directory `costs` verdict is a better
   advisory output than the per-file mover list.

---

---

## Calibrating `C`

PR 4c's headline experiment: `C` is the objective's only free parameter, the bits a container must
save to justify existing. The answer took three attempts, and the two failures are instructive.

**The sweep cannot calibrate `C`, for a structural reason.** Destinations are existing
directories, so a single-file move can only ever *empty* containers, never create one. Its delta is
`delta_edges − C · containers_removed` with `containers_removed ≥ 0`, so every candidate's delta is
non-increasing in `C`, and so is the minimum over candidates. Once a file wants to move it wants to
move at every larger `C`: the locally-optimal fraction is monotone non-increasing with its maximum
pinned at `C = 0`, and no interior peak can exist. Measured, the curve is not merely monotone but
saturated — across `C` from 0.125 to 128, a factor of 1000, **it does not move at all** for any of
the four repos (vite alone shifts, from 0.425 to 0.363, and only between `C = 0` and `C > 0`).
Reading that as "the structure term is inert" would have been a false negative about the model.

**The answer: `C` is bounded from both sides, and the bounds disagree across repos by 160×.**
zod does not reach its peak stability until `C ≥ 8.4`; vite has left its by `C ≈ 0.2`. There is no
value inside every repo's plateau, so `report/calibrate.py` returns `SPREAD` — the second
outcome [plan.md](plan.md) anticipated. **`C` is a per-repo knob, not a constant of
well-structured code.** Mean stability across the four peaks at the bottom of the grid, so if one
number must be picked it is a small one, and the spread is the sensitivity.

> **Superseded finding, kept because the reasoning matters.** The first version of this experiment
> concluded `ONE-SIDED` — that the split side never binds and `C` is bounded only from above. That
> was an artifact of testing only the zero-cut partition into connected components. A component
> partition offers nothing when a directory is internally connected, which is the majority case, so
> the biggest and most obvious split candidates were never priced at all: vite's `node` (30
> children, 585 internal edges, **one** component) is the clearest missing-subdirectory case in the
> corpus and no candidate was ever generated for it. The lesson is that a null result from a
> restricted search space is a fact about the search space. `ONE-SIDED` remains a verdict the code
> can return, since it is a real outcome — it just is not this corpus's.

**The `C`-free result underneath it, and the reason `C` cannot be identified: most directory
boundaries are addressing-*neutral*.** Every directory falls into one of three classes, and the
distinction is the whole finding — an earlier draft of this section collapsed the middle one into
the third and reported "half to three quarters of directories do not earn a bit," which was wrong
and much more alarming than the data:

| repo | containers | earns > 0 | **neutral (= 0)** | **costs (< 0)** | p25 / median / p75 bits earned |
| --- | --- | --- | --- | --- | --- |
| zod | 16 | 10 | 4 | **2** (13%) | 92 / 362 / 1973 |
| vite | 129 | 62 | 64 | **3** (2%) | 1.3 / 3.5 / 10.6 |
| tanstack-router | 9 | 4 | 4 | **1** (11%) | 15 / 33 / 75 |
| date-fns | 1292 | 345 | 855 | **92** (7%) | 13 / 14 / 42 |

- **earns** — dissolving would make addressing more expensive. It is buying encapsulation.
- **neutral** — dissolving changes the edge term by *exactly* zero. Almost always a pass-through:
  the directory has one child, or is its parent's only child, so it partitions nothing and `log2`
  telescopes. Verified: **all 855** of date-fns's neutral containers are one or the other, as are
  all of tanstack-router's and 55 of vite's 64.
- **costs** — dissolving would make addressing strictly cheaper. No `C` saves it. **2–13%.**

That middle column is most of why `C` is so hard to pin: **the population it governs sits exactly
at zero, so for those directories only `C`'s sign matters and its magnitude never enters.** For a
neutral container, keep-versus-dissolve is a comparison of `C` against 0, and any `C > 0` says
dissolve. The sweep is left identifying `C` from the minority that are not neutral, which is why
the per-repo bounds are so far apart.

The medians among the earning directories span two orders of magnitude (3.5 bits in vite against
362 in zod), which is the same story from the other side. But the headline is the neutral majority:
**the dependency graph has no opinion on most directory boundaries, in either direction.** That is
an information ceiling, not a calibration problem, and no amount of tuning moves it.

---

## What the tool can actually find

**The date-fns check passes.** Under the bit cost, **854 of date-fns's 1292 containers** have
exactly one child, carry exactly 0 bits of addressing between them, and consume **16.7%** of its
entire objective as pure `C` overhead. zod and vite do not look like that: 19% and 40%
single-child, 0.2% and 6.2% of the objective.

Two reasons this is not the "937 of 1196" quoted in "What we've learned": that figure counts
directories holding one *file*, while this counts one *child* of either kind, which is the quantity
`log2` actually sees; and it is the raw graph, while this is the spliced, re-rooted default. Both
are correct for what they measure — see the variant warning in [REPOS.md](REPOS.md).

**The `costs` list is the shippable artifact, and it is small — but it is not clean.**
`report/run.py` now emits `containers.csv`, every directory priced for its own existence, ordered
so the `costs` verdicts are the first rows. Across all four reference repos it is 98 rows, and 92
of those are date-fns's `_lib` convention. On a normal repo it is 1–3 candidates, which is the
right shape for a linter. Read them:

| directory | children | components | internal | external | bits | is it a finding? |
| --- | --- | --- | --- | --- | --- | --- |
| vite `shared` | 10 | 4 | 7 | 91 | −92 | **yes** — four unrelated clusters in a directory named `shared` |
| zod `v4/locales` | 52 | 50 | 2 | 1256 | −799 | no — a legitimate taxonomy |
| date-fns `locale/_lib` | 4 | 4 | 0 | 346 | −650 | no — same |
| vite `node/__tests__/fixtures` | 18 | 18 | 0 | 3 | −10 | no — should have been `--exclude`d |
| tanstack `router-core/src/ssr/serializer` | 4 | 1 | 3 | 9 | −2.4 | marginal |

**The third finding type: which files belong in a subdirectory.** `report/run.py` also emits
`splits.csv` — every subdirectory a directory could pay to gain, ranked, and *which children move
into it*. This is the most legible output the tool produces, because it names files rather than
scoring a directory. **All paying candidates are emitted rather than one winner**: both sides of a
cut are usually real proposals, and which one a maintainer would accept is a naming judgement, so
the consumer that does the naming should do the picking. **39 candidates across 19 directories**;
the two largest:

| directory | proposal | Δ bits |
| --- | --- | --- |
| zod `v4` | move 4 of 5 down — `classic/`, `core/`, `locales/`, `mini/` | −661 |
| vite `node` | move 15 of 30 down — `baseEnvironment.ts`, `build.ts`, `config.ts`, `constants.ts`, `environment.ts`, `__tests__/`, … | −381 |
| tanstack `react-router/src` | move 23 of 46 down — `Asset.tsx`, `ClientOnly.tsx`, `HeadContent.tsx`, `Match.tsx`, `RouterProvider.tsx`, … | −51 |

The vite and tanstack ones are cases **no earlier version of this machinery could produce**: each
directory is a single connected component, so the component-partition search proposed nothing for
either and reported them as unsplittable.

Whether the cuts are *good* is a separate question this project cannot yet answer. The tanstack
proposal is component-heavy — 20 of the 23 moved files are `.tsx` — but 15 more `.tsx` files stay
behind, so it is a dependency cluster that skews toward components rather than a
components/non-components separation. That is the same gap as the junk-drawer/taxonomy one: the
graph produces a defensible cut and cannot say whether it is a cut anyone would name.

---

## Junk drawer or taxonomy

**Nothing in the *cost* numbers separates the first row from the second.** Both are "many
components, high external traffic, no internal cohesion". But there is a second graph signal that
does separate them, and it was hiding in plain sight.

**Structural equivalence, not cohesion.** `directory_cohesion` asks whether a directory's children
link to *each other*. That question is blind by construction to files that never touch but do the
same job. The other question — do they have the same *neighbours*? — is answerable from the same
graph, and it separates the two cases cleanly. Median pairwise Jaccard of children's
out-neighbourhoods:

| directory | out-Jaccard | in-Jaccard | reading |
| --- | --- | --- | --- |
| zod `v4/locales` | **1.000** | 1.000 | 52 files importing an identical set — parallel siblings |
| rich `_unicode_data` | **1.000** | 0.000 | 23 data tables, identical imports — parallel siblings |
| vite `node/plugins` | 0.250 | 0.100 | partly parallel, as a plugin directory should be |
| vite `shared` | **0.000** | 0.015 | disjoint neighbourhoods — junk drawer |
| zod `v3/helpers` | **0.000** | 0.333 | junk drawer |
| date-fns `_lib` | **0.000** | 0.000 | junk drawer |

Two things make this more than a curiosity. First, it resolves the `zsf.ts` / `_unicode_data`
ambiguity that plan.md has carried since Phase 0 as needing *symbol-level* data — `_unicode_data`
scores 1.000 and is plainly a data table, at file granularity, today. Second, **the graph
independently reproduces the naming heuristic**: the three directories scoring 0.000 are named
`shared`, `helpers` and `_lib`, which are exactly the words naming-as-validator says to reject on.
Two independent signals agreeing is much stronger evidence than either alone.

Caveat that keeps this honest: it is 7 directories labelled by eye. Across the full `costs` set the
separation is suggestive rather than proven, and `earns` directories score low too (median 0.000),
so this is a discriminator *within* the `costs` list, not a standalone metric. Building the
labelled evaluation set it needs is the main argument for enlarging the corpus. See "Open
questions".

---

## Bugs and artifacts found by measuring

**Bug found and fixed: self-edges.** `rich/box.py` and `rich/live.py` import themselves. Under the
bit cost that charged `log2(branching)` to address a file from inside itself — a selection nobody
makes — and worse, it made the objective inconsistent with the move deltas, which have to reprice
exactly the edges the objective counts. `metrics.edges()` now drops them. Only rich is affected (2
of 421 edges); the only published number that moves is its entropy floor, 2.92 → 2.91.

**Re-rooting, a measurement artifact found while checking the above.** Node ids are
checkout-relative, so zod's 286 files all carry `packages/zod/src/`. Those levels each have one
child and carry 0 bits — but they leave the *root* with branching 1, which makes it nearly-free
parking, and local optimality duly recommended hoisting every widely-shared file into it. Before
`model/graph.py::reroot`, 176 of vite's 247 movers and 659 of date-fns's wanted the repo root;
after, 9 and 12. `reroot` strips the longest common directory prefix — provably the maximal
single-child chain, so integer and bit costs are both unchanged and only the container count and
root branching move — and it is on by default in both CLIs.

---

## Open questions

- ~~**Is `C` a constant of well-structured code, or a per-repo knob?**~~ **Answered: a knob.**
  Both sides of the bracket bind, and they disagree across repos by 160× — zod needs `C ≥ 8.4`,
  vite has left its plateau by `C ≈ 0.2`. See "Calibrating C" above. The follow-on question is
  sharper and open: **is the structure term deciding anything worth deciding?** Most directories
  are exactly neutral in the edge term, so for them `C`'s sign is the whole of its influence and
  its magnitude never enters.
- **Public-face detection per language.** Needed for the fractality bit and for barrel handling. Go
  is trivial (exported identifiers); TS needs re-export analysis; Python `__init__.py` is
  convention-dependent.
- **Monorepos:** analyze per-package or whole? Partially answered — tanstack-router's two packages
  cruised cleanly with zero new code via the per-root package-name alias. Still open: path-alias
  conventions like meridian2's `@/*` → `./src/*` involve no package name and remain unsolved.
- **Do symbols matter?** See "Symbol level". The file-level ceiling is already visible: `zsf.ts` and
  `rich/_unicode_data` produce the *identical* file-level signature.
- **Can a junk drawer be told from a taxonomy? This is the question the tool depends on, and there
  are now two candidate answers rather than none.** PR 4c's `costs` list is the right *size* for a
  linter — 1–3 candidates on a normal repo — but mixes real findings (vite's `shared`) with
  legitimate taxonomies (zod's `v4/locales`). Two signals separate them on the handful of cases we
  can label by eye, and they agree with each other: **structural equivalence** (children's
  neighbourhood Jaccard: 1.000 for locale tables, 0.000 for junk drawers) and **naming** (the
  low-scoring directories are literally named `shared`, `helpers`, `_lib`). Test structural
  equivalence first — it is free, deterministic, and already computable from the extracted graphs —
  and keep naming as the confirmation. Needs a labelled evaluation set bigger than 7 directories,
  which is the main reason to enlarge the corpus.
- **Adoption posture.** The tool most likely to be used is an advisory metric plus a handful of
  high-confidence moves (the `knip` / `dependency-cruiser` shape), not a formatter. The strongest
  niches are agent-written code (no aesthetic ownership, sprawls badly), greenfield, and monorepo
  package splits (rare, high-stakes, genuinely uncertain). A fresh motivation worth testing: file
  boundaries are retrieval chunks for coding agents, so minimizing cross-file coupling directly
  reduces how much irrelevant code an agent loads — measurable in a way aesthetics never were.

---

---

## Open risks

- **No Go control.** TS evidence is strong — four repos, permutation z-scores decisively against
  chance on both axes — but no repo tests the "integer cost > 1 is nearly impossible by
  construction" Go case, which was meant to be the answer key for the visibility rule specifically.
  Get a modern Go toolchain, or accept that the verdict rests on TS alone.
- ~~**The Phase 0 permutation ("vs. shuffled") numbers are still uncorrected.**~~ **Retired rather
  than fixed.** The claim it guarded — "far better than random" — was never re-established for the
  corrected corpus and no longer matters: 4c showed that beating a shuffle says very little next to
  the absolute tests, and the permutation baseline runs on the *integer* cost, which is not the
  objective. PR 4d is dropped rather than deferred. The uncorrected shuffled column stays in "Phase
  0 results" as a historical record and should not be quoted as current evidence.
- **The bit cost now has numbers. They are mixed, and the file-level half is weak.** `C` does not
  calibrate, and 40–91% of files sit somewhere the objective would move them. But the
  *directory*-level verdict is not the indictment an earlier draft of this section claimed: only
  2–13% of directories actively cost addressing bits, and the large middle is provably neutral
  rather than bad. Two readings of the weak file-level number are live and Phase 0 cannot choose
  between them: (a) the objective has a real pathology — evicting any member of a large directory
  always saves a little for its siblings, so no large directory is ever locally optimal; (b) file
  granularity is too coarse and the symbol level would show locality the file graph cannot. The
  `FINDINGS.md` go/no-go has to weigh these, and it should not be written as if the bit cost had
  been validated as a placement rule.
- **The graph is silent on taxonomy, and that is most of the tree.** The neutral majority is an
  information ceiling: on most directory boundaries the dependency graph has no opinion in either
  direction. Worse, the boundaries it *does* have an opinion about include legitimate taxonomies —
  the largest `costs` verdict in the corpus is zod's `v4/locales`, which is correct by the model
  and wrong as advice. Nothing in the numbers distinguishes it from vite's `shared`, which is a
  real junk drawer. Naming-as-validator is therefore not a later refinement but the component that
  decides whether the tool's output is usable, and it is entirely unbuilt and untested.
- ~~**File-level cohesion can't distinguish "junk drawer" from "parallel siblings sharing an
  interface."**~~ **Probably solvable at file level after all** — see structural equivalence under
  "PR 4c — as built". Cohesion asks whether children link to each other; the discriminator asks
  whether they have the same neighbours, and `_unicode_data` scores 1.000 against `shared`'s 0.000.
  Validated on 7 hand-labelled directories, so still a risk, but no longer one that requires the
  symbol level to address.
- **tsconfig path-alias resolution (`@/*`) is unsolved** and blocks PR 6.
- **`is_barrel_ts` is lexical**, not type-aware: it can miss a barrel with a side effect and
  over-flag a file whose statements happen to all be re-exports. Counted, not assumed away. Its
  known blind spot (multi-line `export { ... } from`) didn't fire on zod or date-fns (verified by
  recon), but hasn't been tested against a repo that actually uses that style.
