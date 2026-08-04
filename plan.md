# Dependency-Structure Linter — Design & Plan

Single source of truth: the model, the reasoning behind it, the alternatives already rejected,
what has been built and measured so far, and the prioritized PR queue. Supersedes the former
`plans/steps-4-6.md`, which is folded in below.

**Goal:** derive file and directory structure from the dependency graph, rather than asserting a
hand-written structure. Naming is a later, easy problem (LLMs are good at it).

**Why a separate repo:** the corpus checkouts and their extracted dependency graphs should be
cached, and the extractors are reusable across projects. The application repo being analyzed
(`meridian2`) is a _row in the corpus_, not the host.

---

## Hypothesis to falsify

> Well-engineered repositories already place files where this rule would place them.

If true, the tool enforces something real and has an adoption argument. If false, the rule is an
aesthetic preference and that is worth learning before building a placement engine.

Phase 0 tests this with no placement engine, no dominators, and no symbol resolution.

**Status after PR 4c: partly, and the interesting part is where it has nothing to say.** Under the
integer cost all four TypeScript repos place files far better than chance. Under the bit cost — the
actual objective — 2–13% of their directories actively cost addressing bits, 26–53% earn it, and
**the rest are exactly neutral**: the dependency graph has no opinion on them in either direction.
The hypothesis is neither confirmed nor falsified so much as bounded. The graph can identify a
small set of genuinely bad boundaries; it cannot derive a tree, because most boundaries real repos
draw are taxonomic and the graph is blind to taxonomy. See "Phase 0 results" and "Open risks".

---

## The model

### Containment tree

One tree holds everything: directories contain directories, directories contain files, files
contain symbols. The same rule applies at every level — this is the fractality requirement.

A container's **face** is the address by which outsiders may reach into it:

| Container | Face                                                                                |
| --------- | ----------------------------------------------------------------------------------- |
| directory | `index.ts` / `__init__.py` / `mod.rs` / the Go package itself                        |
| file      | its export list — exported symbols sit _at_ the face, non-exported ones are interior |

Anything at the face is reachable from outside at no extra cost. Anything behind it is interior.

### Cost function

For a dependency edge `u → v`, walk the containment path from `LCA(u, v)` down to `v`. Ascent
contributes nothing; every step of the descent is a **selection** among a container's children,
and you pay for each one — including the final selection of `v` itself:

```
cost(u → v)  =  Σ  log2( |children(c)| )    over c = c₀, c₁, … c_{d-1}

where c₀ = LCA(u, v),  c_d = v,  and each cᵢ₊₁ ∈ children(cᵢ)
```

`|children(c)|` counts direct children — files and immediate subdirectories alike, each as one
node. At symbol level the same formula runs one level deeper, with a file's exported symbols as
its children.

| Edge                                                | Selections from the LCA  | Integer cost | Bit cost                                |
| --------------------------------------------------- | ------------------------ | ------------ | --------------------------------------- |
| `a/x` → `a/y` (sibling file)                        | `y`                      | 0            | `lg a`                                  |
| `a/x` → `shared.ts` at root (pure ascent)           | `shared`                 | 0            | `lg root`                               |
| `a/x` → `a/sub/index` (own child's face)            | `sub`, `index`           | 1            | `lg a + lg sub`                         |
| `b/x` → `a/index` (sibling's face)                  | `a`, `index`             | 1            | `lg root + lg a`                        |
| `a/x` → `a/sub/deep/y` (into own subtree, deep)     | `sub`, `deep`, `y`       | 2            | `lg a + lg sub + lg deep`               |
| `b/x` → `a/sub/deep/y` (into a stranger's interior) | `a`, `sub`, `deep`, `y`  | 3            | `lg root + lg a + lg sub + lg deep`     |

The **integer cost** (faces crossed, ignoring the final file selection) is what `model/paths.py`
computes today and what every Phase 0 number below is expressed in. It is the bit cost under the
assumption that every container has branching factor 2 and picking a file among its siblings is
free: `bits = (integer + 1) · log2 b` under uniform branching `b`. The row ordering is identical,
so the Phase 0 results are a coarsening of the bit cost, not something it contradicts.

Keep both. The integer cost is the **reporting** view — a 0/1/2/3+ histogram is legible and a
float bit-count is not. The bit cost is the **objective**.

Ownership never enters the definition — only geometry does. Reaching into your own grandchild is
cheaper than reaching into a stranger's grandchild solely because the LCA is closer, which is the
correct reason. Nothing is categorically forbidden; cost plus the escape hatches below do the
enforcing.

### Objective

```
minimize   Σ  w_e · cost(e)   +   C · |containers(T)|
          e∈E

subject to:
  - SCC members must share a container
  - AST containment constraints (a method cannot leave its class, etc.)
  - frozen subtrees (declared conventions — see escape hatches)

tiebreak: among equal-cost solutions, prefer the deeper placement
```

`w_e` is 1 at file level and the reference count at symbol level.

`containers(T)` is the set of interior nodes of the containment tree — at file granularity every
directory except the root (the root exists in every candidate layout, so counting it adds a
constant that cancels out of every comparison); at symbol granularity every directory **and every
file**, since a file holding symbols is a container. That is the mechanism behind "the same `C`
decides whether to split a directory and whether to split a file": at symbol level they are the
same kind of object in the same sum.

`C` is the **only** parameter, measured in bits so it is dimensionally coherent with the edge
term: the addressing a container must save to justify existing. It is the MDL structure term
`L(structure)` to the edge term's `L(data | structure)` — the bit cost charges for *traversing* a
container, and nothing else charges for its *existence*. A constant per container is deliberately
the leading-order approximation; a more faithful `L(structure)` would encode the tree's shape and
each directory's name, but that is `O(|containers|)` anyway up to constants, and PR 4c's whole bet
is that one number is empirically stable. Adding structure to `C` before testing that would be
premature. It is calibrated against the reference corpus, not chosen (PR 4c).

There are no min/max size bounds. `C` replaces `min files per directory`; the `log2(branching)`
term replaces `max files per directory`. This is the change that makes the objective self-
regulating rather than externally clamped — see "Why the size bounds had to go" below.

Depth is **not** a separate objective. It falls out: a file nothing imports contributes nothing
wherever it sits, so the tiebreak buries it as deep as `C` allows (free encapsulation). A widely
shared file pays for every selection between it and its users, so it floats up to where they reach
it by pure ascent. Private sinks, shared floats, one number.

### Why the size bounds had to go

The previous objective was `minimize Σcost` subject to min/max lines-per-file and
files-per-directory, and it carried the admission that *the size bounds are the only repulsive
force*. Without them, minimizing cost collapses everything into one container at cost 0. Four
things are wrong with that:

1. **The bounds are unit-free.** Why 20 files per directory and not 7? Nothing in the model
   answers, so the tool's headline recommendation would rest on a number pulled from taste.
2. **They don't scale.** The same bound governs a 200-file repo and a 20,000-file one.
3. **They are invisible at symbol level.** Two symbols in the same file share an LCA at the file,
   so the integer cost between them is 0 — a 300-symbol file and a 3-symbol file score
   identically, and nothing in `Σcost` ever pressures a file to split. The file-splitting
   question, which is the one that matters most for the eventual tool, is simply not in the
   metric. A `max lines per file` bound papers over this with a proxy for the thing the model
   should be measuring directly.
4. **They are the wrong shape.** A hard constraint says "21 files is a violation and 20 is fine."
   The real statement is "this directory is not earning its size," which is continuous.

The bit cost fixes all four with one mechanism, and its properties are worth stating explicitly:

- **Collapse is no longer free.** A flat root of 1000 files charges `log2(1000) ≈ 10` bits on
  every edge. Under the integer cost it charges 0.
- **Split-neutrality.** *Pure regrouping* means repartitioning a container's children without
  adding or removing leaves — 100 files in `D` become 10 subdirectories of 10. Because
  `log2(100) = log2(10 × 10) = log2(10) + log2(10)`, this is exactly free for two of the three
  edge classes, and strictly cheaper for the third:

  | Edge | flat `D` | after a balanced `m`-way split | Δ |
  | --- | --- | --- | --- |
  | from outside `D` into `v` | `log2(k)` | `log2(m) + log2(k/m)` | **0** |
  | `u → v`, landing in *different* groups | `log2(k)` | `log2(m) + log2(k/m)` | **0** |
  | `u → v`, landing in the *same* group | `log2(k)` | `log2(k/m)` | **−log2(m)** |

  So the entire value of splitting a directory is the edges the partition does *not* cut. Nesting
  never pays for its own sake — only when it aligns with locality. This is the "no free lunch"
  guarantee that keeps the objective measuring structure instead of rewarding depth.
- **The split rule.** Writing `W` for the edges whose endpoints stay together, the table above
  collapses to a single decision:

  ```
  saving = W · log2(m)      cost = C · m      split iff  W · log2(m) > C · m
  ```

  `W` is maximized by the partition cutting the fewest edges, so the objective performs graph
  partitioning without ever being told to — which is why a separate clustering objective is
  rejected below. It also localizes where locality enters: under a **random** partition each edge
  survives with probability ≈ `1/m`, so `W ≈ E/m` and the saving is `(E/m)·log2(m)`, peaking near
  `0.5·E` at `m` = 2–4 and decaying after; under a **locality-aligned** partition `W ≈ E` and the
  saving is `E·log2(m)`, which keeps growing. Same formula, same repo: an arbitrary split justifies
  at most one shallow level, a real modular decomposition justifies a deep tree.
- **`C` is not optional.** No row of that table is ever negative, so the edge term alone recurses
  to a binary tree. Even a clique wants to split: 10 mutually-importing files cost 299 bits flat
  and 259 bits as 5+5, because the 40 within-group ordered pairs each save exactly one bit. `C` is
  the only thing standing between the objective and an infinitely deep tree — and that example
  also fixes the scale, since the split pays iff `40 > 2C`. Rule of thumb from `m = 2`: a directory
  must keep `W > 2C` internal edges uncut to justify splitting in two.
- **Corollary: absent locality, flatten.** A tree induces a distribution over leaves,
  `q(v) = Π 1/branching`, and expected address length is the cross-entropy
  `H(p, q) = H(p) + D_KL(p ‖ q) ≥ H(p)`. Under uniform access, `log2(N)` is therefore a **floor**
  that any *balanced* tree achieves and no tree beats — unbalanced ones are strictly worse
  (splitting 100 into 90 + 10 costs a file in the big group `log2(2) + log2(90)` = 7.49 bits, up
  from 6.64). Since strict improvement is impossible and ties are the best case, `C · |containers|`
  is pure overhead and the optimum is flat. The entire payoff of hierarchy comes from `p(v | u)`
  being concentrated. The tool's default advice on a repo whose dependencies carry no locality is
  *flatten it*, which is a sharper and more falsifiable thesis than the old one.
- **It is a compression ratio, so it is scale-free.** `Σ w_e · cost(e)` is the cross-entropy of
  the dependency graph under the code induced by the directory tree, and its floor is the
  conditional entropy `H(v | u)`. Reporting `used / floor` gives an absolute, cross-repo-
  comparable number — "this tree spends 1.7× the bits the graph requires" — which mean-cost-per-
  edge cannot, since it scales with repo size.
- **Fractality survives.** Both terms decompose over containers, and external importers still
  enter as a single virtual importer at the subtree root. Same one bit per node as before.
- **It works unchanged at symbol level.** A file with 40 exports charges `log2(40)` on every
  reference into it, and the same `C` decides "split this directory" and "split this file" in the
  same units. This is the fractality requirement finally cashing out rather than being asserted.
  Private symbols are not addressable at all: an edge into one is a violation, reported rather
  than costed.

This objective is in the hierarchical map-equation family. Infomap is worth reading for its
*optimizer*; the objective above is a better fit for this geometry (free ascent, paid descent)
than the map equation's random-walk formulation.

### Fractality

Requirement: running the algorithm on a subtree must produce, for that subtree, the same result as
running it on the whole tree.

This holds, and the boundary condition reduces to **one bit per node: is it referenced from
outside the subtree?** Model each externally-referenced node as having one virtual importer at the
subtree's root. Every LCA inside the subtree is then unchanged, every cost is unchanged, and the
local result is exactly the global one restricted to that subtree.

Consequences worth designing around:

- **Incremental checking.** Edit inside `a/sub/` and only `a/sub/` needs re-running, unless its
  public face changed. Caveat under the bit cost: an edge's cost now depends on the branching
  factor of every container it descends through, so adding a file to `a/sub/` re-prices every edge
  entering `a/sub/`, not just edges touching the new file. Still local, just not per-edge-local —
  `model/paths.py::cost` stops being pure path arithmetic and needs the tree's branching data.
- **Local hysteresis.** A proposed move stays confined to one subtree unless it alters a face.
- **Parallelism.** Subtrees are independent given their face bits.

### Escape hatches (needed for real adoption)

1. **Declared global faces.** Let the user mark directories as globally visible (`shared/`,
   `lib/`, the `@/` alias roots). Edges into them cost 0 regardless of geometry. Do not forbid
   these — **count** them. "This repo declares 3 global namespaces" vs. 30 is itself a quality
   signal, and it turns an unbounded escape hatch into a number that can be driven down.
2. **Frozen subtrees.** A `--freeze` list (PR 4c) of paths whose layout is convention-governed rather than
   dependency-governed: file-based routing, `migrations/`, `__tests__/`, per-locale data tables.
   The model has nothing true to say about these (see "Where the model will be wrong") and must be
   able to be told so. This is load-bearing, not politeness — without it the tool's first
   recommendation on a Next.js repo is to destroy the router.
3. **Ratchet, not threshold.** Never require zero. Record the current total cost and require it
   not to increase. This is how teams actually adopt `strictNullChecks` and coverage gates, and it
   makes the tool useful on day one against a repo that violates it everywhere.
4. **Advisory by default.** Report a score and a small number of high-confidence moves. Do not
   rewrite the tree. See "churn" under Rejected alternatives.

### Where the model will be wrong

Stated up front so the results are read correctly. It should place the **coupled core** of a repo
well and the **convention-governed periphery** badly:

- **Convention-driven layouts.** date-fns's one-function-per-directory, file-based routing,
  `__tests__/`, framework-imposed `migrations/`. All get flattened. Handled by `--freeze` and
  `--exclude`, not by the model.
- **Grouping mutually-unrelated things.** A `shared/` full of independent utilities has zero
  internal edges, so the model scatters its contents to the root as siblings. This is the mirror
  image of the `zsf.ts` finding: the graph is silent on grouping unrelated things, in both
  directions. Naming-as-validator is the only available lever.
- **Tests.** Structurally disconnected leaves; they bury themselves next to their subject at
  maximum depth. That is what Go does and what colocated `.test.ts` does, so it is not obviously
  wrong — but it means `--exclude` is doing real work in every number reported.

A cheap falsification test, available with the current corpus: the bit cost should score
date-fns's 937 single-file directories as pure `C` overhead carrying zero addressing information,
and should not say the same of zod or vite. If that does not come out, the model is wrong.

---

## Rejected alternatives — do not re-derive these

**Connected-component repulsion.** Proposed as the replacement for the size bounds:
`cost(D) = max(0, #{components of D's child subgraph with ≥ m members} − 1)`, summed over
directories, giving a force that pushes files into subdirectories by connectivity. The instinct is
right and is what the bit cost implements — but this formula cannot carry it:

- **It does not fire on the case it exists to prevent.** Put all 1000 files in the root. Real
  dependency graphs are connected (a giant component plus isolates), so the count is 1 and the
  score is 0. The collapse optimum is perfect under this metric, so it cannot replace `max files
  per directory`.
- **It only reduces under the intended split with two extra stipulations** — clamp at zero, and
  count a contracted subdirectory as size 1. Under the natural reading (size = files underneath)
  the count is *invariant* under exactly the split it is meant to reward.
- **It is scale-free in the wrong direction.** A 6-file directory and a 600-file one are charged
  identically, which is why it needs `min_files_per_directory` to suppress noise — a threshold
  reintroduced to replace thresholds.

It is a step-function approximation of modularity, and it behaves like one: across seven repos it
found roughly four real hits. **Keep it as a diagnostic** (that is what Phase 0 validated it as),
not as an objective term.

**Dominance / immediate dominators (`idom`) as the placement rule.** Appealing because it is
canonical and needs no fixpoint, and it coincides with caller-LCA in few-entrypoint repos. Three
independent problems:

- It over-hoists. Any symbol reachable from two independent entrypoints has `idom = root`. In a
  library where every public export is an entrypoint, a private helper shared by two exports
  hoists out of its own module. The wider the entrypoint set, the flatter the result — so it says
  least exactly where you most need an answer.
- It answers a _visibility_ question ("what gatekeeps access to this?"), not a placement one.
  `idom` may name a node that is not even a caller.
- **It is not fractal.** It can decide a node belongs outside the subtree entirely, which a
  subtree-local run cannot see, because dominance is defined by paths from global entrypoints.

Dominance is still the right tool for the _visibility_ question — how private a thing can be,
which scope it can be hidden inside. Keep it for that; do not use it to place.

**A separate lateral / clustering objective.** Introduced to answer "what goes with what."
Unnecessary: co-location is a consequence of minimizing the objective, not a second goal.

**Distance ≤ 1 (only parent, sibling, or child).** Forbids `../../shared/utils`, which is a pure
ascent and the most common sane pattern in real repos. Satisfiable only via per-level re-export
relay chains (boilerplate, hides the true dependency) or by flattening. No real module system
restricts distance — Go, Node, and Rust all permit unlimited ascent and restrict only descent into
another subtree's interior.

**Discounting type-only edges.** Deliberately rejected for simplicity: types are weighted the same
as any other import. _But_ report the SCC size distribution both with and without type edges as a
diagnostic — type-only cycles are common and harmless, and since SCC condensation collapses cycles
into single nodes, an inflated SCC can swallow a chunk of the hierarchy. If a repo's structure
collapses, this tells you whether it is real coupling or type recursion.

**Recomputing the layout from scratch (formatter model).** The killer practical failure. Adding
one function can flip a clustering decision and move dozens of declarations, destroying blame and
conflicting with every open branch. Any real version computes a _diff from the current layout_
with hysteresis, and scales the acceptance threshold to blast radius:

| Tier | Change                              | Import paths affected                |
| ---- | ----------------------------------- | ------------------------------------ |
| 0    | reorder within a file               | none                                 |
| 1    | move symbol between files, same dir | cross-file callers only (free in Go) |
| 2    | move file between dirs              | all callers                          |
| 3    | create/split/delete a directory     | all callers + mental model           |

Tier 0 is safe to apply automatically and is independently useful. Tier 3 should need to beat the
status quo by a landslide.

---

## What exists

| Module | State |
| --- | --- |
| `model/paths.py` | `dirs()`, `lca()`, `cost()` (integer), `child_of()`, `branching()`, `bit_cost()`. Both cost columns of the table above are pinned in `tests/test_paths.py`, as is split-neutrality. |
| `extractors/schema.py` | `Graph` / `Node` / `Stats`, gzip JSON read/write, self-validating. |
| `extractors/classify.py` | `is_face(path, lang)`, `is_barrel_py(source)`, `is_barrel_ts(source)`. |
| `extractors/python/` | grimp-based extractor + isolated `grimp_worker.py`. |
| `extractors/ts/` | dependency-cruiser-based extractor (`extract.mjs` + `extract.py`). |
| `corpus/sync.py` | Pinned shallow checkouts, `--lang` filter. |
| `corpus/graphs/` | `flask`, `requests`, `rich`, `zod`, `date-fns`, `vite`, `tanstack-router` — checked in, deterministic. `meridian2` not yet extracted (PR 6). |
| `model/graph.py` | `filter_nodes()`, `reroot()`, `splice_barrels()`, `value_edges_only()` — pure transforms returning new `Graph`s. |
| `model/metrics.py` | Every PR 4a metric, one function per metric returning a dict, plus `all_metrics()`. Adds `charge_counts()` (the objective decomposed per container) and `container_information()` (the date-fns check) in 4c. `DEFAULT_C = 8.0` — still provisional after 4c, which found C is not identified. |
| `model/placement.py` | `move_frontier()`, `local_optimality()`, `sweep()` — single-file local optimality, C-independent by construction. `containers()`, `container_stability()`, `stability_sweep()` — per-directory dissolve/split pricing. `freeze_sets()`. Every delta is checked against a rebuilt graph in `tests/test_placement.py`. |
| `report/run.py` | Typer CLI, one invocation per variant. `--exclude` and `--freeze` (repeatable globs), `--splice-barrels`, `--reroot`, `--lang`, `--repo` (repeatable), `--output DIR`. Emits `summary.md`, `summary.csv`, per-repo metric JSON, `worst-edges.csv`, `movers.csv`, and `containers.csv` — every directory priced for its own existence, `costs` verdicts first. Flags repos over an unresolved-import threshold rather than averaging them in silently. |
| `report/calibrate.py` | The C sweep. Emits `calibration.csv` / `calibration.md` with the stability curve, the split-vs-dissolve breakdown, what directories earn before C, and a mechanically-derived verdict. |

### Schema, as actually built

Narrower than the original sketch — several fields were cut as unused or recomputable:

```python
Node(id, kind, is_barrel, imports, type_only)      # no loc, no is_face, no is_test/is_generated
Graph(repo, lang, commit, extractor, roots, nodes, stats)   # no generated_at
Stats(unresolved_imports, external_imports_dropped)          # no ambiguous
```

- `is_face` is **a function**, `classify.is_face(node.id, graph.lang)` — never a stored field.
- Test/generated-file exclusion is an analysis-time `--exclude` flag, built in PR 4b. Nothing
  consumes it before then.
- Graphs are **byte-for-byte deterministic** (gzip `mtime=0`, sorted keys) — verified for all seven
  checked-in graphs by re-extracting and diffing.
- Corpus is filtered by `--lang`, not by stage. Go has no manifest entries at all (needs a modern
  toolchain; deferred).

### Conventions

CLIs use **typer**. CI gates are **`uv run pytest`** and **`uv run ty check`** — both must pass.
Python 3.13. Run modules with `python -m` (e.g. `uv run python -m extractors.ts.extract`), not by
path, or the project root won't be on `sys.path`. Node deps for the TS extractor live in
`extractors/ts/package.json` + committed `package-lock.json`, installed via `npm ci` (or `npm
install` for a fresh checkout) — not `npx`, for the same pin-everything reason as corpus commits.

Repo layout, as built:

```
extractors/       one per language, each emits the normalized JSON schema
corpus/           pinned commits + checked-in extracted graphs
model/            cost function, LCA, transforms, metrics — language-agnostic, operates on JSON
report/           metrics, baselines, per-repo tables
```

---

## What we've learned

**The Python corpus is degenerate.** requests has zero depth variance, rich is barely better,
flask only has structure from `json/`/`sansio/`. This was predicted before any TS data existed and
then confirmed; the Phase 0 results table below is the evidence that replaced it.

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

**Validation numbers, for reference:**

| repo | nodes | edges | type-only | barrels | unresolved | node count matches `git ls-files`? |
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

**First bit-cost numbers, at the provisional `C = 8`, unspliced and unfiltered** — a baseline to
compare against once 4b removes the confounds, not yet evidence of anything:

| repo | bits/edge | H(v\|u) floor | compression ratio | containers |
| --- | --- | --- | --- | --- |
| zod | 4.89 | 1.77 | 2.76 | 20 |
| date-fns | 7.58 | 2.65 | 2.86 | 1296 |
| vite | 5.60 | 2.88 | 1.94 | 132 |
| tanstack-router | 5.56 | 2.79 | 1.99 | 10 |
| flask / requests / rich | 4.38 / 4.25 / 6.29 | 2.45 / 2.66 / 2.91 | 1.79 / 1.60 / 2.16 | 4 / 2 / 2 |

date-fns is both the worst ratio and 1296 containers against 1495 files — `C · |containers|` is
10368 of its 44074-bit objective. That is the predicted shape, but it is not yet the named
falsification check: that needs 4c's sweep, and the ratio is also the one number the Python
corpus's shallowness does *not* obviously disqualify it from having an opinion on.

**The compression ratio's floor is above 1.0 in practice.** `H(v|u)` conditions on the importer
and so charges nothing for locating it, while the tree is one shared code that must address
importers and targets alike — a directory holding an importer and its `d` targets charges
`log2(d+1)` against a floor of `log2(d)`. So 1.0 is an asymptote, not a reachable score. The gap
shrinks with repo size and cancels out of cross-repo comparison, which is what the number is for.

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

**What this does not yet do: re-run the "vs. shuffled" comparison.** The permutation baseline that
produced the shuffled column above is still the throwaway, unported `baseline.py` logic — porting
it into real code is PR 4d's job, not 4b's, and no shuffled numbers exist yet for the excluded-test
graphs. So this correction updates the *real* half of the table only. zod's mean cost moving toward
the shuffled baseline (0.53 vs. a shuffled 1.54) is suggestive that the margin survives, but "far
better than random" for the corrected corpus is a claim for 4d to actually verify, not one this PR
is entitled to make.

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

## PR queue

Priority order. Each PR is independently mergeable and leaves CI green.

| # | PR | Why now | Model | Status |
| --- | --- | --- | --- | --- |
| 1 | **7** — naming as a validator over 4c's `costs` list | The tool's usability now rests on it, the candidate set already exists (`containers.csv`), and it is cheap. Decides the go/no-go more directly than anything else queued | Opus 5 | not started |
| 2 | **6** — meridian2 | The optimization subject. No longer blocked on `C` being pinned — 4c found it is not identifiable, so meridian2 gets read at the same C-independent numbers as the reference corpus | Opus 5, plan mode | not started |
| 3 | **4d** — figures + permutation port | Presentation, not evidence | Sonnet 5 | not started, needs 4b |
| — | **4a** — `model/graph.py` + `model/metrics.py` + `bit_cost` | — | Opus 5 | **done** |
| — | **4b** — `report/run.py` with `--exclude` | — | Sonnet 5 | **done** |
| — | **4c** — `--freeze` + local optimality + calibration of `C` | — | Opus 5 | **done** |
| — | **5a** — TS extractor → zod, date-fns, vite, tanstack-router | — | Sonnet 5 | **done** |

Deferred and explicitly scoped: **symbol-level extraction** (see "Symbol level" below). It is the
strongest test of the cost-function change but it is an extractor project, not a metrics one, and
it should not block the file-level answer.

### PR 4a — as built

Shipped as specified. Names and placement a consumer needs, plus the two places the result differs
from the spec:

- `model/paths.py` gained `child_of()`, `branching()`, `bit_cost(u, v, branching)`. `child_of` is
  the shared contraction — `branching()` and the cohesion metric both use it, which is why it sits
  in `paths.py` rather than in `metrics.py`.
- `model/metrics.py`: `cost_histogram`, `total_bit_cost(graph, c)` (the objective, with
  `conditional_entropy` exposed separately), `cross_face_entries`, `depth_vs_fanin`,
  `integer_edge_cost` (mean/median/p90 — **not** `total_cost`, which would now be ambiguous
  against the bit cost), `depth_histogram`, `directory_cohesion`, and `all_metrics`.
- **Deviation 1: edges are deduplicated** in `metrics.edges()`. See "What we've learned" — this is
  what makes the recorded means reproduce exactly.
- **Deviation 2: compression ratio 1.0 is an asymptote,** not an attainable score. Also in "What
  we've learned"; the metric is unchanged, the interpretation is narrower than the spec claimed.

The acceptance checks are tests, not prose: `tests/test_corpus_metrics.py` pins the splice
histograms, the four Phase 0 means, and the four cohesion split-rates against the checked-in
corpus, so any future change to the edge set or the splice has to answer for them.

### PR 4b — as built

Shipped as specified, one deviation on scope:

**`report/run.py`** — typer CLI. **One invocation per variant**, with a required `--output`
directory, rather than an internal variant sweep:

```
uv run python -m report.run --output report/out/all
uv run python -m report.run --output report/out/no-tests \
    --exclude '**/*.test.ts' --exclude '**/tests/**' --exclude '**/__tests__/**' \
    --exclude '**/test_*.py'
```

(On Windows, prefer calling `report.run.main()` from Python directly, or an escape that survives
Click's win32 argument-globbing, over passing `**` patterns straight through Git Bash/cmd — Click
expands glob-looking arguments against the cwd on that platform before the CLI ever sees them.
`uv run pytest` calls `main()` directly and is unaffected; CI runs on `ubuntu-latest` and is also
unaffected.)

- `--exclude PATTERN` — repeatable glob; drops nodes from the graph entirely via
  `model.graph.filter_nodes`. **Not optional polish**: zod's 59% test contamination and
  date-fns's single-file-directory convention distort every number in "What we've learned" and in
  the Phase 0 results, and this flag is the only fix. Named `--exclude`, not `--ignore`,
  deliberately: across linters `ignore` overwhelmingly means "process it but suppress findings"
  (`eslint-disable`, `# noqa`, ruff's `per-file-ignores`) and usually takes *rule codes* rather
  than paths, while `exclude` means "don't look at it at all" (ruff, flake8, black, mypy,
  `tsconfig`). This flag deletes nodes from the graph, so `exclude` is the accurate word — and
  `--freeze` in 4c is the one that means "keep it, don't flag it," so naming this one `--ignore`
  would invert both against convention.
- `--output DIR` — where this run's artifacts land.
- `--splice-barrels / --no-splice-barrels` (default on), plus `--lang` and repeatable `--repo`
  filters, plus `--c` (default `DEFAULT_C`) and `--top-n` (default 20, worst edges per repo).
- Emits `summary.md`, `summary.csv`, one `<repo>.json` per repo (`all_metrics()` plus
  `unresolved_ratio` and `flagged`), and one combined `worst-edges.csv` (top-N per repo by bit
  cost, with source, target, integer cost, bit cost, gateway dir, and whether the entry hit a
  face — computed the same way as `cross_face_entries` so the two agree).
- Repos whose unresolved ratio exceeds `UNRESOLVED_RATIO_THRESHOLD = 0.5` get **flagged** in
  `summary.csv`/`summary.md` rather than silently averaged in. 50% is deliberately above every
  repo actually in the corpus (11–28%, see "Validation numbers") — this is a gate against
  catastrophic breakage like the mis-pinned `typescript` peer dependency (hazard 1), not a
  judgment about normal external-import noise.

**Deviation from spec: the permutation re-run is real numbers only, not "real vs. shuffled."** The
spec's acceptance line ("re-run the Phase 0 permutation numbers with tests excluded") assumed the
shuffled-baseline machinery would already exist; it doesn't — porting the throwaway
`baseline.py` logic into real code is explicitly PR 4d's job. What 4b *can* and does answer: how do
the real (non-shuffled) numbers move once `--exclude` removes the test confound? See "Confound
correction (PR 4b)" under Phase 0 results. The "vs. shuffled" comparison for the corrected corpus
is unverified until 4d ports the permutation harness — noted there as an open item, not silently
assumed to still hold.

`tests/test_report.py` covers `--exclude`/`--repo`/`--lang` filtering, the unresolved-ratio flag,
worst-edge ranking and gateway/face-hit classification (against `plan_md_tree()`, so the expected
values are the same six edges as the cost table), the CSV/Markdown writers, and an end-to-end
`main()` run against the real corpus (zod's node count actually drops under a test-exclude glob).

### PR 4c — local optimality and calibration of `C`

The new headline experiment, and the reason this outranks figures.

**`--freeze PATTERN`** — repeatable glob; keeps nodes in the graph and in the cost, but marks their
subtree as not-a-placement-candidate. It belongs here rather than in 4b because 4b is a measurement
pass over the actual layout, where nothing moves and so "can this move?" is never asked; local
optimality is its first real consumer. Distinct from `--exclude` in effect, not just intent:

- **Excluding changes the answer for files you didn't exclude.** Exclude `src/routes/**` and a util
  imported *only* by routes now looks like it has zero importers, so the depth tiebreak buries it as
  deep as possible. That recommendation is wrong — the file is widely shared. Freezing the routes
  keeps their edges live, so the util still floats to its true caller-LCA.
- **Branching is in the objective now.** A directory of 40 frozen migrations genuinely costs
  `log2(40)` to index into, and every edge descending through it pays that. Excluding them claims
  the directory has zero children, which is a different number, not a cleaner one.
- **They answer different questions.** Exclude = "not part of the system being measured" (tests,
  generated, vendored). Freeze = "real code with real dependencies, but its *location* is decided
  outside the dependency graph" (file-based routing, timestamp-ordered migrations).

meridian2 needs both and the line is sharp: `routeTree.gen.ts` is **exclude** — its enormous fan-in
is a codegen artifact, not design pressure. `src/routes/**` is **freeze** — real files, real
imports, location dictated by the router.

**Local optimality.** For each non-frozen file, evaluate moving it to every other existing
directory and record whether any move reduces the total objective. Report the fraction of files
that are locally optimal, and the list of files that want to move with their best destination and
delta. Incremental evaluation: moving one file re-prices only the edges incident to it plus the
edges descending through its old and new parents, so this is `O(N · D)` cheap evaluations, not
`O(N · D · E)`.

This is a far more demanding test than the permutation baseline — random is a weak opponent, and
a layout can beat a shuffle decisively while still having a third of its files in the wrong place.
It is also exactly the artifact the shipped tool emits, so building it here doubles as a prototype
of the advisory output.

**Calibrating `C`.** Sweep `C` and, for each value, compute local optimality across zod, date-fns,
vite, and tanstack-router. Then:

- If a single `C` maximizes local optimality across all four and the curve has a clear interior
  peak, `C` is a real constant of well-structured code. Pin it and report the value; this is the
  strongest possible outcome.
- If each repo peaks at a different `C`, report the spread. A narrow spread is still usable (pick
  the median, note the sensitivity); a wide one means `C` is repo-specific and must be a knob,
  which is a weaker but honest result.
- If local optimality is flat in `C`, the structure term isn't doing anything and the model
  reduces to the edge term alone — which would be a genuine finding against the whole "directories
  must earn their existence" framing.

Also run the date-fns prediction from "Where the model will be wrong" as a named check: the 937
single-file directories should show up as pure `C` overhead, and zod/vite should not.

### PR 4c — as built

`--freeze`, local optimality and the date-fns check shipped as specified. The calibration did not,
and could not: **the sweep above is unidentifiable, and the third outcome above is the wrong
reading of the flat curve it produces.** That is the finding of this PR.

**The sweep cannot calibrate `C`, for a structural reason.** Destinations are existing
directories, so a single-file move can only ever *empty* containers, never create one. Its delta is
`delta_edges − C · containers_removed` with `containers_removed ≥ 0`, so every candidate's delta is
non-increasing in `C`, and so is the minimum over candidates. Once a file wants to move it wants to
move at every larger `C`: the locally-optimal fraction is monotone non-increasing with its maximum
pinned at `C = 0`, and no interior peak can exist. Measured, the curve is not merely monotone but
saturated — across `C` from 0.125 to 128, a factor of 1000, **it does not move at all** for any of
the four repos (vite alone shifts, from 0.425 to 0.363, and only between `C = 0` and `C > 0`).
Reading that as "the structure term is inert" would have been a false negative about the model.

**What `C` actually arbitrates is a question about containers, and that one is two-sided.** Too
large and a directory would rather dissolve into its parent; too small and it would rather split.
`model/placement.py::containers` prices both exactly, in the objective's own units:

- `dissolve_bits` — the edge bits a directory's existence saves. It survives while `C ≤` that.
- `split_bits` — the edge-bit change from splitting it into one subdirectory per connected
  component of its child subgraph. This is the zero-cut partition, canonical and parameter-free,
  and the same cut `directory_cohesion` already reports; splitting adds `m` containers, so it pays
  while `C < −split_bits / m`. It is **one** split candidate, not a search over partitions — a
  better-balanced cut could pay more, so "stable" here means stable against this split, not
  against every conceivable one. Searching partitions is a placement-engine job.

Both are checked against a rebuilt graph for every container in flask, rich, zod and
tanstack-router (227 dissolves and splits), as is every move delta — the incremental arithmetic is
the module's whole reason to exist, so it is pinned differentially rather than by hand-derived
constants.

**The answer: `C` is not identified by this corpus, and the reason is that the split side never
binds.** Every repo's stability curve peaks at the smallest `C` tested, so the sweep bounds `C`
from above (tightest: `C ≤ 0.91`, set by vite) and not at all from below: at `C = 0.125` the number
of directories wanting to split is 0 in zod, 1 in tanstack-router, 4 in date-fns and 12 of 132 in
vite, and by `C ≈ 1.2` it is zero everywhere. Dissolution pressure dominates at every `C`. The
`ONE-SIDED` verdict `report/calibrate.py` emits is a fourth outcome the spec did not anticipate,
and it is distinguished from `SHARED` mechanically — a plateau whose left edge is the smallest `C`
tested is the sweep running out of room, not a measurement.

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

That middle column is why `C` is unidentifiable, and it is a much cleaner explanation than the
sweep's: **the population `C` governs sits exactly at zero, so only `C`'s sign ever matters and its
magnitude never enters.** For a neutral container, keep-versus-dissolve is a comparison of `C`
against 0, and any `C > 0` says dissolve. There is nothing for a sweep to find.

The medians among the earning directories still span two orders of magnitude (3.5 bits in vite
against 362 in zod), which is the evidence against `C` being a constant. But the headline is the
neutral majority: **the dependency graph has no opinion on most directory boundaries, in either
direction.** That is an information ceiling, not a calibration problem, and no amount of tuning
moves it.

**Re-rooting, a measurement artifact found while checking the above.** Node ids are
checkout-relative, so zod's 286 files all carry `packages/zod/src/`. Those levels each have one
child and carry 0 bits — but they leave the *root* with branching 1, which makes it nearly-free
parking, and local optimality duly recommended hoisting every widely-shared file into it. Before
`model/graph.py::reroot`, 176 of vite's 247 movers and 659 of date-fns's wanted the repo root;
after, 9 and 12. `reroot` strips the longest common directory prefix — provably the maximal
single-child chain, so integer and bit costs are both unchanged and only the container count and
root branching move — and it is on by default in both CLIs.

**Deviation: the calibration measure changed, and the file-level sweep is reported but not used.**
The spec's measure (local optimality vs `C`) is still computed and still emitted — it is the
artifact the shipped tool wants — but it appears in `calibration.md` under "for the record" with
the monotonicity argument, because it identifies nothing. Container stability is what the sweep
actually varies. This is a correction to the experiment, not a widening of it: the same objective,
the same corpus, the same question, asked of the object `C` is actually about.

**Bug found and fixed: self-edges.** `rich/box.py` and `rich/live.py` import themselves. Under the
bit cost that charged `log2(branching)` to address a file from inside itself — a selection nobody
makes — and worse, it made the objective inconsistent with the move deltas, which have to reprice
exactly the edges the objective counts. `metrics.edges()` now drops them. Only rich is affected (2
of 421 edges); the only published number that moves is its entropy floor, 2.92 → 2.91.

**The date-fns check passes.** Under the bit cost, 856 of date-fns's 1295 containers have exactly
one child, carry exactly 0 bits of addressing between them, and consume 16.7% of its entire
objective as pure `C` overhead. zod and vite do not look like that: 26% and 40% single-child, 0.3%
and 6.4% of the objective. (856 is not the 937 quoted below — that counted directories holding one
*file*, this counts one *child* of either kind, which is the quantity `log2` sees.)

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

**Nothing in the numbers separates the first row from the second.** Both are "many components, high
external traffic, no internal cohesion". The difference is that `locales` names a real taxonomy and
`shared` does not — which is precisely plan.md's naming-as-validator, and this is the strongest
empirical case for it the project has produced: the graph narrows four repos to ~98 candidates and
a naming pass is what would cut that to the one or two that are real. Promoted from "later phase"
to the blocking question for the tool (see "Open questions").

### PR 4d — figures and permutation port

matplotlib, SVG, checked in, fixed figure size and consistent axes: stacked cost histogram across
repos, depth-vs-fan-in scatter per repo, depth histogram per repo, and the `C` sweep curve from
4c.

Port the throwaway `baseline.py` permutation logic (shuffle-the-layout, ~200+ iterations, z-score
against the shuffled distribution) into real code, and extend it to depth-vs-fan-in ρ (shuffle the
depth vector, count how often `|ρ|` is matched). It already produced the Phase 0 headline; this
just makes it reproducible.

Last because it is presentation. 4c's absolute compression ratio and local-optimality fraction are
interpretable without a baseline, which is most of why the permutation machinery mattered.

### PR 6 — meridian2

meridian2 is the optimization *subject*, not ground truth. What was previously one undifferentiated
"workspace alias generalization" problem is now two, in very different states:

1. **Cross-package self-reference (`worker/` as a separate pnpm package) — likely already solved.**
   tanstack-router has the identical shape (two roots, two packages, one importing the other by
   package name) and required zero new code. **Verify this holds for meridian2's specific `worker/`
   package before assuming it's free**, but budget for this being close to a non-issue.
2. **`tsconfig.app.json`'s `@/*` → `./src/*` mapping — genuinely unsolved.** Neither zod nor
   date-fns needed `compilerOptions.paths` (confirmed by recon), so this path has had zero design
   or implementation work. Two options: honor `tsConfig` properly (dependency-cruiser's `cruise()`
   third argument accepts it, but no corpus repo has exercised that path), or add a manifest-driven
   alias reader mirroring the package-name-alias reader already in `extract.mjs`. Decide during
   recon, the same way the zod/date-fns resolver options were decided empirically.

Also carries: `src/routeTree.gen.ts` (generated, enormous fan-in) — **do not special-case it in the
extractor**, exclude at report time via `--exclude`, per the extract-once principle.

Pulling meridian2's highest-cost edges and reading them against its `CLAUDE.md` invariants is worth
doing, but it is not *extractor validation* — that job belongs to the reference-repo hazard checks
already done, which have no circularity problem. A disagreement between the tool and meridian2 is a
candidate *finding about meridian2*, to be judged once the placement engine exists.

### After the queue

**`FINDINGS.md`** and the go/no-go on building a placement engine. A reading exercise, not a coding
one, and it should be done with a human in the loop since it has to weigh whether the missing Go
control and the unbuilt symbol level change the verdict.

---

## Symbol level

Deferred, but scoped here because it is the sharpest test of the cost function and the thing that
determines whether the tool can answer "should this file be split."

Same formula, one level deeper: symbols are the leaves, a file's exported symbols are its children,
non-exported symbols are behind a face nobody may cross. Edges weighted by reference count, since
non-uniform access is what makes hierarchy pay at all. The move-tier ladder already distinguishes
symbol moves (tier 1, cheap) from file moves (tier 2), so the objective and the churn model line up.

What it needs: a symbol-granularity extractor. TypeScript has the tooling (`ts-morph` or the
compiler API); Python is harder. This is why it is deferred rather than sequenced — it is an
extractor project, and the file-level answer should not wait on it.

What it would settle:

- Whether the SCC size distribution at file level was hiding real structure (Phase 2's question,
  asked here early).
- Whether `C` calibrated at file level transfers to file-splitting decisions, which is the direct
  empirical test of the fractality claim.
- The `zsf.ts` / `_unicode_data` ambiguity — N mutually disconnected files, each reached only from
  outside, is the identical file-level signature for a real placement candidate and a fully benign
  data table. Symbol-level edges are the only thing that can tell them apart short of naming.

---

## Corpus

Chosen to span entrypoint count and call-graph depth. Pin commits.

| Repo                         | Lang   | Probes                                                      |
| ---------------------------- | ------ | ----------------------------------------------------------- |
| esbuild                      | Go     | few entrypoints, deep pipeline                              |
| hugo                         | Go     | few entrypoints, large                                      |
| `net/http` + `encoding/json` | Go     | many entrypoints; `internal/` as answer key                 |
| requests                     | Python | facade over a narrow core                                   |
| flask                        | Python | small, clean, in-between                                    |
| rich                         | Python | many entrypoints, moderate depth                            |
| date-fns                     | TS     | the extreme: hundreds of entrypoints, one function per file |
| zod                          | TS     | wide public API, concentrated core                          |
| vite                         | TS     | tool/monorepo, few effective entrypoints, deep              |
| TanStack Router              | TS     | monorepo, output judgeable by eye                           |
| **meridian2**                | TS     | the optimization target, not a reference repo               |

`date-fns` is the important TS pick — it is the lodash shape in a TS-native codebase, so it probes
the flattening case directly, and under the bit cost it is also the sharpest test of `C`.

**`meridian2` is the subject, not ground truth.** It is the repo we intend to restructure, so its
current layout is the thing being judged — it cannot also be the standard that calibrates the
judge. Its `CLAUDE.md` states intended invariants, which makes disagreements *interpretable*, but
"the tool disagrees with meridian2" is a finding about meridian2, not evidence the tool is wrong.

**Go is a useful control, still missing.** You cannot import a file in Go, only a package, so every
Go import lands on a face by construction. Integer costs above 1 are impossible except by crossing
`internal/`. That makes Go's cost-1 fraction a pure measure of how much sibling coupling
well-regarded Go code actually tolerates — and `internal/` is a hand-labelled answer key for the
visibility rule.

---

## Later phases (only if the queue above justifies them)

**Graph algorithms.** `networkx` provides `strongly_connected_components`, `immediate_dominators`,
and LCA, so these are library calls rather than an implementation of Lengauer–Tarjan.

1. SCC condensation → DAG. **Report the SCC size distribution.** Small SCCs mean the file-level
   import graph is adequate. One SCC swallowing a large fraction of the repo means file granularity
   is too coarse and symbol-level resolution is required. This is the empirical answer to "do we
   need the call graph?", not a decision to make up front.
2. Cost evaluation on condensed nodes.
3. Dominators, for the _visibility_ report only — which files could be made private, and to what
   scope. Not for placement.

**Derived layout and agreement.** Produce a layout from the model and compare it to the real one
via adjusted Rand index between the derived directory partition and the actual one. A free
secondary baseline: the same repo at an older commit — if maintainers improved structure over time,
the metric should move in the right direction. Do **not** try to source "badly engineered" repos for
contrast; confounded by size, age, language, and domain, and the labelling is subjective.

**Eviction.** When a container overflows, which node leaves? A lexicographic ladder with no tunable
parameters, not a weighted score: (1) never split an SCC; (2) prefer a node nothing else in the
container depends on; (3) prefer one already at the face (creates no new API surface); (4) prefer
the largest; (5) deterministic tie-break by name. The evicted set must be _connected_ in the
dependency graph, or you get a junk drawer rather than a module.

**Hysteresis and the accepted/rejected-move lockfile** (also pins generated names, which otherwise
drift every run).

**Naming as a validator, not a decoration.** ~~Later phase~~ — **PR 4c promoted this to the
blocking question; see "Open questions".** Ask an LLM to name each extracted cluster; if the best
it can do is `utils`, `helpers`, or `misc`, reject the cut and try the next candidate. Unnameable
means incoherent. This turns the fuzziest step into a quality gate on the thing graph metrics
cannot evaluate — and it is the only lever available for the "grouping mutually-unrelated things"
blind spot.

---

## Open questions

- ~~**Is `C` a constant of well-structured code, or a per-repo knob?**~~ **Answered by PR 4c:
  neither, on this corpus — it is not identified at all.** Nothing bounds `C` from below, because
  essentially no directory in any of the four repos wants to split (0, 1, 4 and 12 of them at
  `C = 0.125`, zero everywhere by `C ≈ 1.2`), so the only constraint is an upper bound of about
  0.9 set by vite. And among the directories that do earn their keep, what they earn has a median
  of 3.5 bits in vite against 362 in zod — two orders of magnitude, which is the substantive
  answer: knob, if it is anything. The follow-on question is sharper and now open: **is the
  structure term worth keeping at all?** Half to three quarters of these directories are already
  net-negative in the edge term alone, so `C` is not what is deciding their fate.
- **Public-face detection per language.** Needed for the fractality bit and for barrel handling. Go
  is trivial (exported identifiers); TS needs re-export analysis; Python `__init__.py` is
  convention-dependent.
- **Monorepos:** analyze per-package or whole? Partially answered — tanstack-router's two packages
  cruised cleanly with zero new code via the per-root package-name alias. Still open: path-alias
  conventions like meridian2's `@/*` → `./src/*` involve no package name and remain unsolved.
- **Do symbols matter?** See "Symbol level". The file-level ceiling is already visible: `zsf.ts` and
  `rich/_unicode_data` produce the *identical* file-level signature.
- **Can naming separate a junk drawer from a taxonomy? This is now the question the tool depends
  on.** PR 4c's `costs` list is the right *size* for a linter — 1–3 candidates on a normal repo —
  but not clean: vite's `shared` (four unrelated clusters, no internal cohesion) and zod's
  `v4/locales` (52 files, no internal cohesion) are indistinguishable in every number the graph
  produces, and only the first is a finding. An LLM naming pass over the `containers.csv` `costs`
  rows is a cheap, decisive experiment against a candidate set that already exists, and it should
  come before any placement engine. If naming cannot separate them, the tool has no usable output
  and the answer is no.
- **Adoption posture.** The tool most likely to be used is an advisory metric plus a handful of
  high-confidence moves (the `knip` / `dependency-cruiser` shape), not a formatter. The strongest
  niches are agent-written code (no aesthetic ownership, sprawls badly), greenfield, and monorepo
  package splits (rare, high-stakes, genuinely uncertain). A fresh motivation worth testing: file
  boundaries are retrieval chunks for coding agents, so minimizing cross-file coupling directly
  reduces how much irrelevant code an agent loads — measurable in a way aesthetics never were.

---

## Open risks

- **No Go control.** TS evidence is strong — four repos, permutation z-scores decisively against
  chance on both axes — but no repo tests the "integer cost > 1 is nearly impossible by
  construction" Go case, which was meant to be the answer key for the visibility rule specifically.
  Get a modern Go toolchain, or accept that the verdict rests on TS alone.
- **The Phase 0 permutation ("vs. shuffled") numbers are still uncorrected.** PR 4b's `--exclude`
  fixed the *real*-side numbers for zod and vite (see "Confound correction" under Phase 0 results);
  the shuffled-baseline comparison against the corrected corpus still needs PR 4d's permutation
  port before "far better than random" can be re-claimed for it.
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
- **File-level cohesion can't distinguish "junk drawer" from "parallel siblings sharing an
  interface."** Needs symbol-level data or naming-as-validator; not solvable with what Phase 0 has.
- **tsconfig path-alias resolution (`@/*`) is unsolved** and blocks PR 6.
- **`is_barrel_ts` is lexical**, not type-aware: it can miss a barrel with a side effect and
  over-flag a file whose statements happen to all be re-exports. Counted, not assumed away. Its
  known blind spot (multi-line `export { ... } from`) didn't fire on zod or date-fns (verified by
  recon), but hasn't been tested against a repo that actually uses that style.
