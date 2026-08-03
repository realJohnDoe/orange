# Phase 0, steps 4–6 — PR plan

Execution plan for the rest of Phase 0. [plan.md](../plan.md) is the design doc (the model, the
cost function, the rejected alternatives) and stays authoritative on *what* we're measuring; this
file covers *how the remaining work is split into PRs*, and records what steps 1–3 already
settled so a fresh session doesn't re-derive it.

**Steps 1–3 are done and merged.** What follows is steps 4–6, resequenced (see below).

---

## What already exists

| Module | State |
| --- | --- |
| `model/paths.py` | `dirs()`, `lca()`, `cost()`. Cost semantics pinned against plan.md's table in `tests/test_paths.py`. |
| `extractors/schema.py` | `Graph` / `Node` / `Stats`, gzip JSON read/write, self-validating. |
| `extractors/classify.py` | `is_face(path, lang)`, `is_barrel_py(source)`, `is_barrel_ts(source)`. |
| `extractors/python/` | grimp-based extractor + isolated `grimp_worker.py`. |
| `corpus/sync.py` | Pinned shallow checkouts, `--lang` filter. |
| `corpus/graphs/` | `flask`, `requests`, `rich` — checked in, deterministic. |
| `model/graph.py`, `model/metrics.py`, `report/` | **Do not exist yet.** This plan builds them. |

### Schema, as actually built

Narrower than plan.md's sketch — several fields were cut as unused or recomputable:

```python
Node(id, kind, is_barrel, imports, type_only)      # no loc, no is_face, no is_test/is_generated
Graph(repo, lang, commit, extractor, roots, nodes, stats)   # no generated_at
Stats(unresolved_imports, external_imports_dropped)          # no ambiguous
```

- `is_face` is **a function**, `classify.is_face(node.id, graph.lang)` — never a stored field.
- There is **no** `DEFAULT_TEST_EXCLUDES` / `matches_any` any more. Test and generated-file
  exclusion is an analysis-time `--exclude` flag, built in PR 4b. Nothing consumes it before then.
- Graphs are **byte-for-byte deterministic** (gzip `mtime=0`, sorted keys). Re-extracting an
  unchanged repo must produce no git diff; if it does, something regressed.
- Corpus is filtered by `--lang`, not by stage. Go has no manifest entries at all (needs Go ≥1.24;
  deferred to Phase 1).

### Conventions

CLIs use **typer**. CI gates are **`uv run pytest`** and **`uv run ty check`** — both must pass.
Python 3.13. Run modules with `python -m` (e.g. `uv run python -m extractors.python.extract`), not
by path, or the project root won't be on `sys.path`.

---

## What step 3 learned (this drives the resequencing)

Extraction results:

| Repo | Nodes | Edges | Type-only | Barrels | Cost 0 / 1 | Depths |
| --- | --- | --- | --- | --- | --- | --- |
| flask | 24 | 95 | 24 | 1 | 85% / 15% | 2–3 |
| requests | 19 | 73 | 12 | 0 | **100% / 0%** | **all 2** |
| rich | 100 | 421 | 12 | 0 | ~100% / ~0% | 1–2 |

**The Python corpus is degenerate for our purposes.** requests has literally zero depth variance,
so metric 3 (depth vs. fan-in correlation) has nothing to correlate against; rich is barely better.
flask only has structure because of `json/` and `sansio/`. This is the shallowness plan.md
predicted, confirmed with numbers.

Consequences, already acted on:

- **TypeScript is the first place with real depth**, which is why the sequencing below moves the
  TS extractor ahead of the report layer — there's no point building histograms and scatter plots
  against all-zero data.
- Two grimp hazards were found in step 3, both of which produced *plausible-looking wrong graphs*
  rather than errors: `sys.modules` shadowing the corpus checkout, and PEP 420 namespace packages
  being silently skipped. **Assume the TS extractor has its own analogues** and budget for hunting
  them (see PR 5a).

**Correction to the original framing (made after step 3, before 5a started):** this doc and
plan.md previously treated `meridian2` as "the calibration point" — ground truth to validate the
extractor and the cost model against. That was backwards. `meridian2` is the repo the tool is
meant to *optimize*; its current layout is the thing under judgment, so it cannot simultaneously
be the standard the judge is calibrated against. "The tool disagrees with meridian2" is a finding
about meridian2, not evidence the extractor is wrong. Calibration has to come from repos with an
independent reputation for being well-structured — which is what the rest of the TS corpus
(zod, date-fns, vite, TanStack Router) is for. See `plan.md`'s corpus table for the corrected
framing.

---

## Resequenced PR order

plan.md's original order was 4 (metrics + report) → 5 (TS) → 6 (stage 2). Step 4 is split so the
presentation layer is written *after* there's data worth presenting. **Resequenced a second time**
after the meridian2 correction above: reference repos (zod, date-fns) come before the optimization
subject (meridian2), so 5a's target and 6's target swap.

| PR | Scope | Model | Depends on |
| --- | --- | --- | --- |
| **4a** | `model/graph.py` + `model/metrics.py` + fixtures | Opus 5 | — |
| **5a** | TS extractor → zod + date-fns | Sonnet 5 | — |
| **4b** | `report/run.py` + `report/figures.py` + permutation test | Opus 5 | 4a, 5a |
| **6** | meridian2 + workspace alias generalization | Opus 5, plan mode | 4b |

4a and 5a are independent and can be done in either order, or in parallel. **PR 5b (the old
"meridian2 spot-check") is dropped as a Phase 0 gate** — see below.

---

## PR 4a — cost model and metrics

**`model/graph.py`** — transforms over a loaded `Graph`, all pure, all returning new `Graph`s:

- `filter_nodes(graph, exclude)` — drop nodes whose `id` matches any glob in `exclude`, **and**
  drop every edge pointing at them. Uses `PurePosixPath.full_match` (3.13+, supports `**`).
- `splice_barrels(graph)` — rewire each edge through `is_barrel` nodes to its real target,
  transitively, then drop the barrel nodes. **Must be cycle-guarded** (barrel cycles exist in the
  wild) and **idempotent**. An edge that is type-only anywhere along the spliced chain stays
  type-only.
- `value_edges_only(graph)` — drop `type_only` edges, for the type-vs-value diagnostic.

**`model/metrics.py`** — one function per metric, each taking a `Graph`, returning a dict:

1. **Cost histogram** — fraction of edges at cost 0 / 1 / 2 / 3+, via `model.paths.cost`. Also
   computed separately over type-only vs value edges.
2. **Cross-face entries** — for each edge with `cost ≥ 1`, let `k = len(common_prefix(dirs(u), dirs(v)))`:
   - *gateway directory* = `dirs(v)[:k+1]` — the first directory crossed; the barrel the rule
     would demand. Report the distinct count.
   - *penetrated directories* = every directory on the descent path. Distinct count.
   - *face-hit fraction* — of gateway entries, how many land exactly on that directory's face
     (`classify.is_face`) vs. reach past it into the interior. This is the number that says
     whether a repo already behaves the way the rule wants.
   - all three normalized by total directory count.
3. **Depth vs. fan-in** — Spearman ρ between `len(dirs(id))` and count of distinct importers.
   Reported twice: over all files, and over files with fan-in ≥ 1. Implement ranking with average
   ties; **cross-check against `scipy.stats.spearmanr`** in tests (scipy is dev-only — shipped code
   stays numpy-only). Permutation p-value is **deferred to 4b**.
4. **Total cost** — `Σcost / |E|`, plus median and p90 edge cost.

Plus **depth histogram** — not a metric but a *gate*: if a repo's depths are nearly all one value,
ρ is measuring noise and must be reported as uninformative rather than as evidence. requests will
trip this; that's the point.

**Tests** (`tests/fixtures/`) — small synthetic `Graph`s built in code with metrics computed **by
hand in the test file**: a known cost histogram, a known gateway set, a known ρ. Cover the barrel
splice (including a cycle and an idempotency check) and the exclude filter as units. This is where
correctness comes from — *not* from the corpus, which is why degenerate corpus data doesn't block
this PR.

No CLI in this PR.

---

## PR 5a — TypeScript extractor (zod + date-fns)

The hardest piece. Use **dependency-cruiser as a library** (`import { cruise } from 'dependency-cruiser'`),
not the CLI — the programmatic API takes `tsConfig`, `doNotFollow`, and
`enhancedResolveOptions.alias` directly.

**Scope: zod + date-fns, not meridian2.** Per the correction above, reference repos come first.
Both are also single-root packages (`packages/zod/src`, `pkgs/core/src`), so this PR avoids the
cross-workspace alias problem meridian2's `worker/` boundary introduces — that's saved for PR 6,
once the extractor itself is trusted. Both are barrel-heavy, so the barrel hazard (see below) gets
exercised immediately rather than deferred.

**Split of responsibilities** (mirrors how the Python extractor works, sharpened): the `.mjs` is a
dumb adapter — it emits exactly what dependency-cruiser saw, no filtering, no normalization, no
counting. All policy (root filtering, id normalization, edge dropping, stat counting, `is_barrel`)
lives in Python as a pure `build_graph(payload, entry) -> Graph`, testable from a synthetic dict
with no node in the loop.

- `extractors/ts/extract.mjs` — Node script, emits raw JSON: node ids, imports, and which imports
  are `type-only` (`dep.dependencyTypes.includes('type-only')`).
- `extractors/ts/extract.py` — typer CLI that shells out to the `.mjs`, computes `is_barrel` via
  the **existing** `classify.is_barrel_ts` (Python side reads the sources), and writes the `Graph`.

**zod + date-fns specifics (verified by recon against the synced checkouts, not guessed):**

- **No `tsConfig` is passed to `cruise` at all.** Neither repo's tsconfig resolves standalone:
  zod's needs `customConditions: ["@zod/source"]`, which depends on its own `exports` map — dead
  without `node_modules`; date-fns's `extends` an uninstalled package and has no `include`. Neither
  uses `compilerOptions.paths`, so this isn't a loss.
- **Two `enhancedResolveOptions` are mandatory instead:**
  - `extensionAlias: { '.js': ['.ts', '.tsx', '.js'] }` — zod (`moduleResolution: NodeNext`)
    writes 465 relative specifiers as `./foo.js` pointing at `foo.ts`. Without this the zod graph
    is hollow. date-fns doesn't need it (writes literal `.ts` specifiers) but it's harmless there.
  - A **package self-reference alias**, derived generically (read each root's nearest
    `package.json`, alias its `name` to that root): zod has 165 self-referential imports
    (`zod/v4`, `zod/v3`, `zod/mini`, etc.) that resolve via plain directory-index resolution.
    Without it, 165 internal edges are silently miscounted as external.
- **`is_barrel_ts`'s multi-line blind spot doesn't bite here** — no `index.ts` under either root
  uses a multi-line `export { ... } from`. date-fns's generated `src/index.ts` is a textbook
  single-line barrel and should be flagged; if it isn't, that's a real regression, not a corpus
  quirk.
- **zod is 59% test files** (170 of 286, under `*/tests/*`) — any zod metric read without
  report-time `--exclude` (PR 4b) is dominated by tests. date-fns has zero.
- **No `pnpm install`.** Unresolved external imports are dropped and counted into
  `Stats.unresolved_imports` — the one Stats field the TS extractor actually fills.

**Expected hazards** — step 3 found two silent-wrong-answer bugs in the Python path; look hard for
the TS equivalents before trusting the first clean run:

- Whether a hollow graph (everything unresolved) still *looks* plausible — check the unresolved
  ratio, don't just eyeball node count. **zod is the one to distrust**: it needs both resolver
  options above, and either one missing produces a graph that still has a normal-looking node
  count. date-fns needs neither and is the control.
- Node-count-vs-`git ls-files` sanity check (expect ~286 for zod, ~1494 for date-fns, filtered to
  the extension allowlist). Note: despite this doc previously claiming the Python extractor has
  this check in code, it does not — that was a manual step-3 verification, never committed. PR 5a
  adds it for real.
- Confirm no `zod/...` specifier lands in `external_imports_dropped` — that's the alias missing,
  not a genuinely external import.

Ship when both repos extract with an acceptable unresolved ratio, node counts matching disk, and
the zod self-reference / date-fns barrel checks above pass.

**Deferred to once `model/graph.py::splice_barrels` exists (PR 4a):** both repos are heavily
barrel-based, so splicing should move their cost numbers *a lot*. plan.md requires reporting at
least one repo both spliced and unspliced. **If the delta is small, that's a bug** — suspect
`is_barrel_ts` or `splice_barrels`, not the repos. This was previously framed as "PR 6"'s content;
it now belongs here, since zod/date-fns extraction is 5a's job.

---

## PR 4b — report and figures

**`report/run.py`** — typer CLI. Per the decision taken while planning: **one invocation per
variant**, with an `--output` directory, rather than an internal variant sweep.

```
uv run python -m report.run --output report/out/all
uv run python -m report.run --output report/out/no-tests \
    --exclude '**/*.test.ts' --exclude '**/__tests__/**' --exclude '**/test_*.py' ...
```

- `--exclude PATTERN` — repeatable glob, the single exclusion mechanism (this is the flag the
  whole `DEFAULT_TEST_EXCLUDES` removal was making room for).
- `--output DIR` — where this run's artifacts land.
- `--splice-barrels / --no-splice-barrels`, plus `--lang` / `--repo` filters.
- Emits `summary.md`, `summary.csv`, per-repo metric JSON, and `worst-edges.csv` (top-N highest-cost
  edges with source, target, cost, gateway dir, whether the entry hit a face) — the
  outlier-interrogation tool and the first place to look when a number seems wrong.
- Repos whose unresolved ratio exceeds a threshold get **flagged in the table**, not silently
  averaged in.

Document the canonical invocations (a `justfile`, a make target, or a README block) so
"with tests / without tests" is reproducible rather than remembered.

**`report/figures.py`** — matplotlib, SVG, checked in, fixed figure size and consistent axes so
re-runs produce minimal diffs: stacked cost histogram across repos, depth-vs-fan-in scatter per
repo, depth histogram per repo (making "ρ is uninformative here" visible rather than argued).

**Permutation p-value** lands here too (deferred from 4a): shuffle the depth vector ~1000× and
count how often |ρ| is matched. Cheap, makes ρ interpretable, and it's a first cut at the
permutation machinery Phase 3 needs anyway.

---

## PR 6 — meridian2

meridian2 is the optimization *subject*, not ground truth (see the correction at the top of this
doc), and it needs the workspace alias map (`worker/` is a separate pnpm package) generalized
beyond a single-root package — the thing 5a's zod/date-fns scope deliberately deferred. Also
carries `tsconfig.app.json`'s `@/*` → `./src/*` mapping, and `src/routeTree.gen.ts` (generated,
enormous fan-in — **do not special-case it in the extractor**, exclude at report time via
`--exclude` instead, per the extract-once principle).

**What replaces the old "5b spot-check":** pulling meridian2's highest-cost edges and reading them
against its `CLAUDE.md` invariants is still worth doing, but it is no longer an *extractor
validation* step — that job belongs to 5a's hazard checks against zod/date-fns, which have no
circularity problem. A disagreement between the tool and meridian2 is now a candidate *finding
about meridian2* (a placement recommendation), to be judged once the placement engine exists (see
plan.md's "Later" phase) — not a bug signal about the cost model.

Note `date-fns` has restructured since plan.md was written: it's a pnpm monorepo now, and its
manifest entry points at `pkgs/core/src` — noted here since PR 6 is the first place the workspace
alias work is generalized, and `date-fns` (extracted in 5a) is not itself a monorepo case worth
revisiting.

---

## After PR 6

Not covered in detail here: **vite + TanStack Router**, which reuse the workspace alias map PR 6
generalizes for meridian2's `worker/` boundary, then **`FINDINGS.md`** and the Phase 1 go/no-go.

That last one is a reading exercise, not a coding one — and it should be done with a human in the
loop, since it has to weigh whether the missing Go control changes the verdict.

---

## Open risks

- **The verdict may rest on too little.** Go is deferred, and Python is near-flat. If TS comes back
  ambiguous, the honest options are to run Go after all (needs a modern toolchain) or to widen the
  TS corpus — not to over-read three shallow Python repos.
- **Barrel splicing is unvalidated at scale.** One barrel exists across all of Python (flask's
  `__init__.py`). The transform is effectively untested against real data until it runs against
  the barrel-heavy zod/date-fns graphs 5a produces (see the deferred note in PR 5a).
- **`is_barrel_ts` is lexical**, not type-aware: it can miss a barrel with a side effect and
  over-flag a file whose statements happen to all be re-exports. Counted, not assumed away.
