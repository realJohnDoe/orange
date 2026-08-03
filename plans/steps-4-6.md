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

- **meridian2 is the first repo with real depth**, which is why the sequencing below moves the TS
  extractor ahead of the report layer — there's no point building histograms and scatter plots
  against all-zero data.
- Two grimp hazards were found in step 3, both of which produced *plausible-looking wrong graphs*
  rather than errors: `sys.modules` shadowing the corpus checkout, and PEP 420 namespace packages
  being silently skipped. **Assume the TS extractor has its own analogues** and budget for hunting
  them (see PR 5a).

---

## Resequenced PR order

plan.md's original order was 4 (metrics + report) → 5 (TS) → 6 (stage 2). Step 4 is split so the
presentation layer is written *after* there's data worth presenting:

| PR | Scope | Model | Depends on |
| --- | --- | --- | --- |
| **4a** | `model/graph.py` + `model/metrics.py` + fixtures | Opus 5 | — |
| **5a** | TS extractor → meridian2 | Opus 5, plan mode | — |
| **5b** | meridian2 spot-check against its `CLAUDE.md` | Opus 5 | 5a |
| **4b** | `report/run.py` + `report/figures.py` + permutation test | Opus 5 | 4a, 5a |
| **6** | zod + date-fns (barrel-splice delta) | Sonnet 5 | 4b |

4a and 5a are independent and can be done in either order, or in parallel.

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

## PR 5a — TypeScript extractor

The hardest piece. Use **dependency-cruiser as a library** (`import { cruise } from 'dependency-cruiser'`),
not the CLI — the programmatic API takes `tsConfig`, `doNotFollow`, and
`enhancedResolveOptions.alias` directly.

**Split of responsibilities** (mirrors how the Python extractor works):

- `extractors/ts/extract.mjs` — Node script, emits raw JSON: node ids, imports, and which imports
  are `type-only` (`dep.dependencyTypes.includes('type-only')`).
- `extractors/ts/extract.py` — typer CLI that shells out to the `.mjs`, computes `is_barrel` via
  the **existing** `classify.is_barrel_ts` (Python side reads the sources), and writes the `Graph`.

**meridian2 specifics** (verified in step 0):

- `tsconfig.app.json` maps `@/*` → `./src/*`; roots are `["src", "worker/src"]`.
- `worker/` is a separate pnpm workspace package — needs the workspace alias map, or cross-package
  edges resolve to nothing and the graph is quietly hollow.
- `src/routeTree.gen.ts` is generated with enormous fan-in. **Do not special-case it in the
  extractor** — it gets excluded at report time via `--exclude`, per the extract-once principle.
- **No `pnpm install`.** Only internal edges matter; unresolved external imports are dropped and
  counted into `Stats.unresolved_imports`. That ratio is the trigger for installing a specific
  repo later, and the one Stats field the TS extractor actually fills.

**Expected hazards** — step 3 found two silent-wrong-answer bugs in the Python path; look hard for
the TS equivalents before trusting the first clean run:

- `.ts` / `.tsx` / `.d.ts` / `.mts` extension handling and index resolution.
- Whether a hollow graph (everything unresolved) still *looks* plausible — check the unresolved
  ratio, don't just eyeball node count.
- Node-count-vs-`git ls-files` sanity check, as the Python extractor has.

Ship when meridian2 extracts with an acceptable unresolved ratio and node set matching disk.

---

## PR 5b — meridian2 spot-check

meridian2 is the calibration point: its `CLAUDE.md` states the placement rule in prose
("a file moves into a subdirectory only when every caller already lives in that subdirectory"),
which is caller-LCA, plus a directory-scope table.

Pull the ten highest-cost edges and read them against that table. Either outcome is informative:

- genuine violations → the metric measures what we think it does;
- artifacts (alias resolution, the `worker/` boundary, generated files) → an extractor bug caught
  **before** it contaminates zod, date-fns, vite, and TanStack Router.

Budget for this forcing a rewrite of part of 5a. That's why it's a separate PR.

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

## PR 6 — zod and date-fns

Manifest entries already exist and are pinned. Mostly a re-run on working machinery — hence
Sonnet 5.

The real content is the **barrel-splice delta**: both repos are heavily barrel-based, so splicing
should move their numbers *a lot*. plan.md requires reporting at least one repo both ways. **If the
delta is small, that's a bug**, not a finding — suspect `is_barrel_ts` or `splice_barrels` and
escalate to Opus 5.

Note `date-fns` has restructured since plan.md was written: it's a pnpm monorepo now, and the
manifest points at `pkgs/core/src`.

---

## After step 6

Not covered in detail here: **vite + TanStack Router** (needs the workspace alias map generalized
beyond meridian2's single `worker/` package), then **`FINDINGS.md`** and the Phase 1 go/no-go.

That last one is a reading exercise, not a coding one — and it should be done with a human in the
loop, since it has to weigh whether the missing Go control changes the verdict.

---

## Open risks

- **The verdict may rest on too little.** Go is deferred, and Python is near-flat. If TS comes back
  ambiguous, the honest options are to run Go after all (needs a modern toolchain) or to widen the
  TS corpus — not to over-read three shallow Python repos.
- **Barrel splicing is unvalidated at scale.** One barrel exists across all of Python (flask's
  `__init__.py`). The transform is effectively untested against real data until PR 6.
- **`is_barrel_ts` is lexical**, not type-aware: it can miss a barrel with a side effect and
  over-flag a file whose statements happen to all be re-exports. Counted, not assumed away.
