# Phase 0, steps 4–6 — PR plan

Execution plan for the rest of Phase 0. [plan.md](../plan.md) is the design doc (the model, the
cost function, the rejected alternatives, the Phase 0 results) and stays authoritative on *what*
we're measuring; this file covers *how the remaining work is split into PRs*, and records what's
already settled so a fresh session doesn't re-derive it.

**Steps 1–3 and PR 5a are done and merged/pushed.** What follows is PR 4a, 4b, and 6 — the
remaining work.

---

## What already exists

| Module | State |
| --- | --- |
| `model/paths.py` | `dirs()`, `lca()`, `cost()`. Cost semantics pinned against plan.md's table in `tests/test_paths.py`. |
| `extractors/schema.py` | `Graph` / `Node` / `Stats`, gzip JSON read/write, self-validating. |
| `extractors/classify.py` | `is_face(path, lang)`, `is_barrel_py(source)`, `is_barrel_ts(source)`. |
| `extractors/python/` | grimp-based extractor + isolated `grimp_worker.py`. |
| `extractors/ts/` | dependency-cruiser-based extractor (`extract.mjs` + `extract.py`). **Done.** |
| `corpus/sync.py` | Pinned shallow checkouts, `--lang` filter. |
| `corpus/graphs/` | `flask`, `requests`, `rich`, `zod`, `date-fns`, `vite`, `tanstack-router` — checked in, deterministic. `meridian2` not yet extracted (PR 6). |
| `model/graph.py`, `model/metrics.py`, `report/` | **Do not exist yet.** PR 4a/4b build these. |

### Schema, as actually built

Narrower than plan.md's sketch — several fields were cut as unused or recomputable:

```python
Node(id, kind, is_barrel, imports, type_only)      # no loc, no is_face, no is_test/is_generated
Graph(repo, lang, commit, extractor, roots, nodes, stats)   # no generated_at
Stats(unresolved_imports, external_imports_dropped)          # no ambiguous
```

- `is_face` is **a function**, `classify.is_face(node.id, graph.lang)` — never a stored field.
- Test/generated-file exclusion is an analysis-time `--exclude` flag, built in PR 4b. Nothing
  consumes it before then.
- Graphs are **byte-for-byte deterministic** (gzip `mtime=0`, sorted keys) — verified for all
  seven checked-in graphs by re-extracting and diffing.
- Corpus is filtered by `--lang`, not by stage. Go has no manifest entries at all (needs a modern
  toolchain; deferred to Phase 1).

### Conventions

CLIs use **typer**. CI gates are **`uv run pytest`** and **`uv run ty check`** — both must pass.
Python 3.13. Run modules with `python -m` (e.g. `uv run python -m extractors.ts.extract`), not by
path, or the project root won't be on `sys.path`. Node deps for the TS extractor live in
`extractors/ts/package.json` + committed `package-lock.json`, installed via `npm ci` (or `npm
install` for a fresh checkout) — not `npx`, for the same pin-everything reason as corpus commits.

---

## What we've learned

**The Python corpus is degenerate.** requests has zero depth variance, rich is barely better,
flask only has structure from `json/`/`sansio/`. Confirms what plan.md predicted before any TS
data existed — see plan.md's Phase 0 results table for the numbers that replaced this as evidence.

**The meridian2 correction.** `meridian2` is the repo the tool is meant to *optimize*, not ground
truth to calibrate the extractor or cost model against — "the tool disagrees with meridian2" is a
finding about meridian2, not evidence the extractor is wrong. Calibration comes from repos with an
independent reputation for being well-structured (zod, date-fns, vite, TanStack Router). This is
why 5a's target and the old PR 6's target swapped: reference repos first, optimization subject
last. See plan.md's corpus table for the corrected framing.

**PR 5a shipped further than originally scoped** — zod, date-fns, vite, *and* tanstack-router are
all extracted, not just the two originally planned. Three real hazards were found and fixed along
the way (none were the grimp-style hazards anticipated; all were TS/tooling-specific):

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
   be real analyzed files, where they're a meaningful invariant rather than a false positive on a
   legitimate external reference.

**The cross-workspace alias problem mostly evaporated.** tanstack-router has the exact shape PR 6
was written to solve for meridian2 — two roots (`router-core`, `react-router`) from two separate
packages in one workspace, one depending on the other by package name. It cruised cleanly with
**zero new code**: the generic per-root "alias a package's own `package.json` name to its root"
mechanism built for zod's self-references (`zod/v4`, `zod/v3`, ...) resolved all 59
`react-router → router-core` edges correctly. The handful of subpaths behind conditional
`exports` (`@tanstack/router-core/isServer`) correctly failed to resolve rather than silently
resolving wrong — the safe failure mode. This meaningfully de-risks PR 6; see below.

**Validation numbers, for reference:**

| repo | nodes | edges | type-only | barrels | unresolved | node count matches `git ls-files`? |
| --- | --- | --- | --- | --- | --- | --- |
| zod | 286 | 608 | 165 | 4 | 22.8% | yes (286/286, excl. `.md`) |
| date-fns | 1495 | 4452 | 1019 | 4 | 11.3% | yes (1495 = 1494 `.ts` + 1 `.js`) |
| vite | 396 | 1085 | 383 | 8 | 27.8% | yes (396 = 400 tracked − 4 `node_modules` test fixtures, correctly excluded by `doNotFollow`) |
| tanstack-router | 116 | 476 | 238 | 3 | 15.6% | yes |

**The permutation baseline (originally scoped for Phase 3) was pulled forward informally** to
interpret these numbers rather than just reporting them raw — see plan.md's Phase 0 results table.
All four TS repos place files far better than random on both cost and cohesion simultaneously.
This was done with a throwaway script, not `report/`'s real permutation machinery (PR 4b) — but
the result is real and belongs in the design doc, which is where it now lives.

**The barrel-splice delta was validated ad hoc**, ahead of `model/graph.py::splice_barrels`
existing (PR 4a doesn't exist yet). A throwaway cycle-guarded splice, run against zod and
date-fns:

| repo | cost histogram, unspliced | cost histogram, spliced |
| --- | --- | --- |
| zod | 56% / 43% / 1% / 0% | 13% / 86% / 0% / 0% |
| date-fns | 30% / 37% / 32% / 1% | 33% / 32% / 34% / 1% |

zod's huge shift confirms the theory: many edges only looked cheap because they stopped at a
barrel's face, and splicing exposes the real descent cost to what's behind it. date-fns barely
moves — its internal code mostly bypasses its own barrel already. plan.md's "if the delta is
small, that's a bug" check has a real answer now: date-fns's small delta is not a bug, it's a
repo-convention fact, confirmed by zod's large delta on the identical mechanism. **This is the
acceptance test for the real `splice_barrels`**: it should reproduce these two numbers exactly.

**The directory-cohesion metric earned its place.** Also throwaway (not in `model/metrics.py`
yet), but it found a genuine, non-benign signal that cost alone cannot: zod's `v4/core/zsf.ts`
shares zero edges with the other 18 files in `core/`, unlike every other split in the corpus,
which is the same benign pattern as `rich/_unicode_data` (independent test files or per-locale
data). vite additionally surfaced 3 "genuine" splits — directories where the odd-one-out is a
second real *cluster* of ≥2 files, not a lone singleton — which is the more interesting case to
dig into next, if that thread gets picked back up.

**Two known confounds in every number quoted above** — not yet corrected because `--exclude`
doesn't exist yet (PR 4b):

- zod is 59% test files (170 of 286, under `*/tests/*`). Nearly every zod cohesion "split" is a
  test directory, which is structurally guaranteed to look incohesive (tests depend on their
  subject, never on each other) and is not informative either way.
- date-fns has 937 of its 1196 directories holding exactly one file (median: 1). The
  `addDays/index.ts`-per-function convention makes directories into filename prefixes, not real
  containers, but the cost function still charges a face to cross each one — date-fns's cost
  numbers are partly measuring this convention, not coupling.

---

## Resequenced PR order

| PR | Scope | Model | Status |
| --- | --- | --- | --- |
| **4a** | `model/graph.py` + `model/metrics.py` + fixtures, including the cohesion metric | Opus 5 | not started |
| **5a** | TS extractor → zod, date-fns, vite, tanstack-router | Sonnet 5 | **done** |
| **4b** | `report/run.py` + `report/figures.py` + permutation test | Opus 5 | not started, depends on 4a |
| **6** | meridian2 | Opus 5, plan mode | not started, depends on 4b; substantially de-risked (see below) |

---

## PR 4a — cost model and metrics

**`model/graph.py`** — transforms over a loaded `Graph`, all pure, all returning new `Graph`s:

- `filter_nodes(graph, exclude)` — drop nodes whose `id` matches any glob in `exclude`, **and**
  drop every edge pointing at them. Uses `PurePosixPath.full_match` (3.13+, supports `**`).
- `splice_barrels(graph)` — rewire each edge through `is_barrel` nodes to its real target,
  transitively, then drop the barrel nodes. **Must be cycle-guarded** (barrel cycles exist in the
  wild) and **idempotent**. An edge that is type-only anywhere along the spliced chain stays
  type-only. **Acceptance check:** run against the checked-in zod and date-fns graphs; must
  reproduce the unspliced/spliced cost histograms in "What we've learned" above (56/43/1/0 →
  13/86/0/0 for zod; near-unchanged for date-fns).
- `value_edges_only(graph)` — drop `type_only` edges, for the type-vs-value diagnostic.

**`model/metrics.py`** — one function per metric, each taking a `Graph`, returning a dict:

1. **Cost histogram** — fraction of edges at cost 0 / 1 / 2 / 3+, via `model.paths.cost`. Also
   computed separately over type-only vs value edges.
2. **Cross-face entries** — for each edge with `cost ≥ 1`, let `k = len(common_prefix(dirs(u), dirs(v)))`:
   - *gateway directory* = `dirs(v)[:k+1]` — the first directory crossed; the barrel the rule
     would demand. Report the distinct count.
   - *penetrated directories* = every directory on the descent path. Distinct count.
   - *face-hit fraction* — of gateway entries, how many land exactly on that directory's face
     (`classify.is_face`) vs. reach past it into the interior.
   - all three normalized by total directory count.
3. **Depth vs. fan-in** — Spearman ρ between `len(dirs(id))` and count of distinct importers.
   Reported twice: over all files, and over files with fan-in ≥ 1. Implement ranking with average
   ties; **cross-check against `scipy.stats.spearmanr`** in tests (scipy is dev-only — shipped code
   stays numpy-only). Permutation p-value is **deferred to 4b**.
4. **Total cost** — `Σcost / |E|`, plus median and p90 edge cost.
5. **Directory cohesion** — validated ad hoc against all seven repos (see "What we've learned"),
   now ready to port into real code. For every directory with ≥2 direct children (files or
   immediate subdirectories, contracted to one node each), build the induced undirected subgraph
   over those children from the edge set; report whether it's connected, and if not, the
   component sizes. Union-find over contracted children, same algorithm as the throwaway script.
   Report both the raw split count and a "genuine" count (≥2 components of size ≥2, filtering out
   the common case of one real cluster plus isolated leaves).

Plus **depth histogram** — not a metric but a *gate*: if a repo's depths are nearly all one value,
ρ is measuring noise and must be reported as uninformative rather than as evidence. requests will
trip this; that's the point.

**Tests** (`tests/fixtures/`) — small synthetic `Graph`s built in code with metrics computed **by
hand in the test file**: a known cost histogram, a known gateway set, a known ρ, a known cohesion
split (include a case with an isolated leaf that should *not* count as "genuine" per the ≥2-of-≥2
rule, and one that should). Cover the barrel splice (including a cycle and an idempotency check)
and the exclude filter as units.

No CLI in this PR.

---

## PR 4b — report and figures

**`report/run.py`** — typer CLI. **One invocation per variant**, with an `--output` directory,
rather than an internal variant sweep.

```
uv run python -m report.run --output report/out/all
uv run python -m report.run --output report/out/no-tests \
    --exclude '**/*.test.ts' --exclude '**/__tests__/**' --exclude '**/test_*.py' ...
```

- `--exclude PATTERN` — repeatable glob, the single exclusion mechanism. **Not optional polish**:
  zod's 59%-test contamination and date-fns's single-file-directory convention both distort every
  number quoted in "What we've learned" above, and this flag is the only fix.
- `--output DIR` — where this run's artifacts land.
- `--splice-barrels / --no-splice-barrels`, plus `--lang` / `--repo` filters.
- Emits `summary.md`, `summary.csv`, per-repo metric JSON, and `worst-edges.csv` (top-N highest-cost
  edges with source, target, cost, gateway dir, whether the entry hit a face).
- Repos whose unresolved ratio exceeds a threshold get **flagged in the table**, not silently
  averaged in.

**`report/figures.py`** — matplotlib, SVG, checked in, fixed figure size and consistent axes:
stacked cost histogram across repos, depth-vs-fan-in scatter per repo, depth histogram per repo.

**Permutation test** — port the throwaway `baseline.py` logic (shuffle-the-layout, ~200+
iterations, z-score against the shuffled distribution) into real code. It already produced the
headline result in plan.md's Phase 0 section; this PR just makes it reproducible and extends it to
depth-vs-fan-in ρ (shuffle the depth vector, count how often `|ρ|` is matched).

---

## PR 6 — meridian2

meridian2 is the optimization *subject*, not ground truth (see the correction above). What was
previously one undifferentiated "workspace alias generalization" problem is now two, and they're
in very different states:

1. **Cross-package self-reference (`worker/` as a separate pnpm package) — likely already
   solved.** tanstack-router has the identical shape (two roots, two packages, one importing the
   other by package name) and required zero new code — the existing per-root package-name alias
   handled it. **Verify this holds for meridian2's specific `worker/` package before assuming it's
   free**, but budget for this being close to a non-issue.
2. **`tsconfig.app.json`'s `@/*` → `./src/*` mapping — genuinely unsolved.** Neither zod nor
   date-fns needed `compilerOptions.paths` (confirmed by recon), so this path has had zero design
   or implementation work. Two options: honor `tsConfig` properly (dependency-cruiser's `cruise()`
   third argument accepts it, but neither corpus repo has exercised that path yet), or add a
   manifest-driven alias reader mirroring the package-name-alias reader already in `extract.mjs`.
   Decide during this PR's recon, the same way the zod/date-fns resolver options were decided
   empirically rather than guessed.

Also carries: `src/routeTree.gen.ts` (generated, enormous fan-in) — **do not special-case it in
the extractor**, exclude at report time via `--exclude` (PR 4b), per the extract-once principle.

**On the old "5b spot-check":** pulling meridian2's highest-cost edges and reading them against
its `CLAUDE.md` invariants is still worth doing, but it is not an *extractor validation* step —
that job belongs to the zod/date-fns/vite/tanstack-router hazard checks already done, which have
no circularity problem. A disagreement between the tool and meridian2 is a candidate *finding
about meridian2* (a placement recommendation), to be judged once the placement engine exists (see
plan.md's "Later" phase) — not a bug signal about the cost model.

---

## After PR 6

vite and TanStack Router are already extracted (PR 5a ran ahead of the original schedule), so the
only remaining item is **`FINDINGS.md`** and the Phase 1 go/no-go.

That's a reading exercise, not a coding one — and it should be done with a human in the loop, since
it has to weigh whether the missing Go control changes the verdict.

---

## Open risks

- **No Go control.** TS evidence is now strong — four repos, permutation z-scores decisively
  against chance on both cost and cohesion (see plan.md). Python remains degenerate but is no
  longer load-bearing; TS alone carries the hypothesis. The honest gap is that no repo tests the
  "cost > 1 is nearly impossible by construction" Go case, which was meant to be the answer key for
  the visibility rule specifically, not the placement one. Get a modern Go toolchain, or accept the
  verdict rests on TS alone.
- **File-level cohesion can't distinguish "junk drawer" from "parallel siblings sharing an
  interface."** zod's `zsf.ts` (real placement candidate) and `rich/_unicode_data`'s per-version
  tables (fully benign) produce the identical file-level signature — N mutually disconnected
  files, each reached only from outside. Needs symbol-level data or the "naming as validator" idea
  in plan.md's Later section; not solvable with what Phase 0 has.
- **tsconfig path-alias resolution (`@/*`) is unsolved** and blocks PR 6 — see above.
- **`is_barrel_ts` is lexical**, not type-aware: it can miss a barrel with a side effect and
  over-flag a file whose statements happen to all be re-exports. Counted, not assumed away. Its
  known blind spot (multi-line `export { ... } from`) didn't fire on zod or date-fns (verified by
  recon), but hasn't been tested against a repo that actually uses that style.
