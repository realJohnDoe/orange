# The corpus, repo by repo

Companion to [plan.md](plan.md), which holds the model, the reasoning and the PR queue. This file
holds what each repo actually taught us. Regenerate the numbers with:

```bash
uv run python -m report.run --output report/out/all
```

**Every number below is the spliced, re-rooted variant** (`report/out/all`) unless it says
otherwise — barrels spliced to their real targets, and the shared checkout prefix
(`packages/zod/src`, `src/requests`, …) stripped so the root is where the code is. plan.md's older
tables are unspliced and un-re-rooted, so they will not match; the variant matters more than any
single figure here.

## At a glance

| repo | lang | files | edges | dirs | earns / neutral / costs | wants a subdir | locally optimal | int. cost mean | bits/edge | ratio | depth range |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| zod | ts | 282 | 2085 | 16 | 10 / 4 / **2** | 4 | 60% | 0.87 | 6.96 | 1.39 | 0–4 |
| date-fns | ts | 1491 | 4102 | 1292 | 345 / 855 / **92** | 4 | 27% | 1.04 | 7.49 | 3.34 | 0–4 |
| vite | ts | 388 | 987 | 129 | 62 / 64 / **3** | 4 | 44% | 0.39 | 5.60 | 1.93 | 1–8 |
| tanstack-router | ts | 113 | 396 | 9 | 4 / 4 / **1** | 1 | 9% | 0.29 | 5.61 | 1.99 | 2–4 |
| flask | py | 23 | 91 | 2 | 2 / 0 / 0 | 0 | 52% | 0.15 | 4.32 | 1.75 | 0–1 |
| rich | py | 100 | 419 | 1 | 1 / 0 / 0 | 0 | 71% | 0.00 | 6.29 | 2.16 | 0–1 |
| requests | py | 19 | 73 | **0** | 0 / 0 / 0 | 0 | 100% | 0.00 | 4.25 | 1.60 | 0 |

`wants a subdir` counts directories from `splits.csv` that would pay to move some of their children
down into a new subdirectory at `C = 8`. `earns / neutral / costs` is the per-directory verdict from
`containers.csv`: does deleting this
directory and moving its children up make addressing more expensive, change nothing, or make it
cheaper? See plan.md, "PR 4c — as built". `ratio` is bits spent over the conditional-entropy floor;
1.0 is an unreachable asymptote, and it is the only figure here that is comparable across repos.

---

## The TypeScript four

### zod — the concentrated core

**16 directories, 282 files, 2085 edges.** The densest graph in the corpus by a wide margin: 7.4
edges per file against vite's 2.5. Best compression ratio (1.39), which says its tree is close to
an optimal code for its own dependency graph.

- **A real core exists and the model finds it.** The top movers are `core/errors.ts`,
  `core/util.ts`, `core/checks.ts` — exactly the modules everything else imports. "Shared things
  float up" works here.
- **59% test files.** The single largest confound in the corpus. Excluding them takes local
  optimality from 60% to 11%, because the tests were colocated with their subjects and contributed
  cheap cost-0 edges that flattered the average. Any zod number quoted without a test filter is
  measuring test placement.
- **`v4/locales` is the corpus's largest `costs` verdict (−799 bits) and is completely
  legitimate** — 52 locale files, 50 components, 1256 external entries, 2 internal edges. This one
  directory is the reason plan.md now treats taxonomy-vs-junk-drawer as the blocking question.
- Barrel splicing moves zod more than any other repo (56/43/1/0 → 13/86/0/0): its internal code
  really does route through its own barrels, unlike date-fns.

### date-fns — the extreme, and the sharpest test of the cost model

**1292 directories for 1491 files.** The one-function-per-directory convention (`addDays/index.ts`)
makes directories into filename prefixes.

- **854 of its 1292 directories have exactly one child, carry exactly 0 bits between them, and
  consume 16.7% of the entire objective as pure `C` overhead.** This is plan.md's named
  falsification check and it passes cleanly: zod and vite look nothing like this.
- **Worst compression ratio in the corpus by far (3.34 against 1.39–1.99).** It spends 3.3× the
  bits its dependency graph requires. The convention is legible to humans and expensive to address.
- 92 of the corpus's 98 `costs` verdicts are date-fns's, and nearly all are its `_lib` convention
  rather than 92 independent findings. Any corpus-wide count of findings is really a count of
  date-fns.
- **Highest face-hit rate (46%)** — nearly half of all directory entries land on an `index.ts`.
  Its convention at least keeps traffic at the face.

### vite — the deepest tree and the one genuine finding

**129 directories, depth 1 to 8** — the only repo in the corpus with real depth variance
(modal share 22%; everything else is 35–100%).

- **`shared/` is the corpus's one unambiguous junk drawer**: 10 children, 4 disconnected
  components, 7 internal edges against 91 external entries, −92 bits. A directory named `shared`
  holding four unrelated clusters, pulled at from everywhere. This is the finding the whole tool
  exists to produce.
- **Best-behaved `costs` list**: 3 rows, of which 2 are test fixtures that should have been
  `--exclude`d. So on a normal repo the tool emits ~1 candidate.
- **`node` is the corpus's clearest missing subdirectory**: 30 children, 585 internal edges, and
  exactly *one* connected component — so the cohesion diagnostic and the original
  component-partition split search both had nothing to say about it. The spectral cut proposes
  moving 15 of the 30 down, worth 373 bits, the largest split finding in the corpus.
- 62 earns / 64 neutral — the cleanest illustration that a real tree splits roughly half and half
  between directories doing addressing work and directories doing none.
- Highest unresolved-import ratio (29.8%), from monorepo-internal imports the extractor can't see
  without `node_modules`. Under the 50% flag threshold but the worst in the corpus.

### tanstack-router — two packages, and the worst local optimality

**9 directories, 113 files, two roots** (`router-core`, `react-router`).

- **De-risked PR 6 for free.** Two packages in one workspace, one importing the other by package
  name, cruised with zero new code — the per-root package-name alias built for zod's
  self-references resolved all 59 cross-package edges.
- **17% of its raw edges were duplicate import statements** (71 of 476), nearly all
  `react-router → router-core` imported once as a type and once as a value. This is what forced
  edge deduplication in `metrics.edges()`.
- **Lowest local optimality in the corpus (9%)** and it is not a test-file artifact — it has no
  test files inside its analyzed roots. With only 9 directories and 113 files, nearly every file
  has a cheaper home available somewhere. Small repos have few places to hide.
- Zero face-hits: nothing enters a directory through an `index.ts`. Its packages expose their
  surface differently from zod's.
- **A flat 46-file directory that the split search wants halved.** `react-router/src` holds 46
  children in one connected component, and the proposal moves 23 of them down (−43 bits). The
  moved side is component-heavy — 20 of the 23 are `.tsx` — but **15 more `.tsx` files stay
  behind**, so this is a dependency cluster that happens to skew toward components, not a
  components/non-components separation. Whether a maintainer would recognise the cut is exactly
  the kind of thing this project cannot yet tell you, and is the reason naming-as-validator
  matters for splits as well as for junk drawers.

---

## The Python three, and why they say nothing

This is the crispest way to put it. After stripping the checkout prefix, the three Python repos
have **0, 1 and 2 directories between them**:

| repo | directories after re-rooting | files at depth 0 |
| --- | --- | --- |
| requests | **0** | 19 of 19 |
| rich | 1 (`_unicode_data`) | 77 of 100 |
| flask | 2 (`json/`, `sansio/`) | 17 of 23 |

Every metric in this project is a statement about a *tree*. There is no tree. requests' mean
integer cost is exactly 0.000 because every import is between siblings, which costs 0 by
definition; its local optimality is 100% because there is nowhere else to put anything. These are
not weak signals, they are the absence of a signal, and `depth_histogram`'s `informative` gate
exists to say so out loud (requests trips it at modal share 1.00).

**Why they are flat is not a fact about Python.** Three things compound:

1. **The package is both the unit of distribution and the unit of import.** Splitting
   `requests/adapters.py` into `requests/transport/adapters.py` changes the public import path, so
   subdividing a Python package is a breaking API change. In TS, moving a file behind a barrel is
   invisible to consumers. Python therefore pays a real cost for nesting that TS does not.
2. **`__init__.py` re-export makes flat cheap.** The idiom is a flat module namespace with the
   public surface hoisted into `__init__.py`, which is exactly the "declared global face" escape
   hatch, applied to the whole package.
3. **They are small libraries.** 19, 24 and 100 files. A 100-file flat package is ordinary Python;
   a 100-file flat directory in TS would be unusual.

**The real lesson is about corpus design, not about Python.** These three were chosen to span
*entrypoint count and call-graph depth* — the axes that mattered before the cost model existed. The
axis that turned out to matter is **directory-tree depth and branching**, and nobody selected for
it. rich has 100 files and 1 directory; vite has 388 files and 129 directories. That difference is
the whole ballgame, and it is visible before any analysis runs.

So Python is untested here, not disqualified. Testing it needs repos big enough that packages
actually nest — django, sqlalchemy, airflow, home-assistant — and the grimp extractor already
works, so it is cheap.

---

## What the corpus taught us about corpora

- **Screen candidates on tree shape before extracting.** Directory count, depth range and modal
  depth share are computable from `git ls-files` alone. Anything with a modal depth share above
  ~0.7 or fewer than ~10 directories cannot inform this project, whatever its reputation.
- **One repo can dominate a corpus-wide count.** 92 of 98 `costs` verdicts are date-fns's, and
  nearly all of those are one convention. Report per-repo, never pooled.
- **Test files are the largest single confound and their effect is not uniform.** zod loses 59% of
  its files to `--exclude`; date-fns and tanstack-router lose none. Always report both variants.
- **The interesting repos are the ones with depth variance.** vite is the only repo here whose
  depth histogram spans more than four levels, and it is the only one that produced a clean
  finding.
