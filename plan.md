# Dependency-Structure Linter — Design & Plan

Single source of truth: the model, the reasoning behind it, the alternatives already rejected,
what has been built and measured so far, and the prioritized PR queue. Supersedes the former
`plans/steps-4-6.md`, which is folded in below.

**Three files, three jobs.** This one is the design and the plan: the model, why it is that model,
what has been built, and what to build next. [FINDINGS.md](FINDINGS.md) is what running it taught
us — results, verdicts and the conclusions that were later overturned. [REPOS.md](REPOS.md) is the
per-repo reference: the numbers for each corpus repo and what each one contributed.

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

**Status after PR 4c: partly, and the interesting part is where the model has nothing to say.**
See [FINDINGS.md](FINDINGS.md), "Where the hypothesis stands".

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


### What the objective says about one directory

`C` arbitrates a question about containers, and it is two-sided: too large and a directory would
rather dissolve into its parent, too small and it would rather gain a subdirectory.
`model/placement.py::containers` prices both exactly, in the objective's own units, and together
they define the interval of `C` over which a given directory is stable:

- `dissolve_bits` — the edge bits a directory's existence saves. It survives while `C ≤` that.
- `split_bits` — the edge-bit change from moving the best subset of its children down into a new
  subdirectory. Candidates are each connected component and each side of the Fiedler-vector cut
  taken at zero and at its median; each is priced exactly and the cheapest wins. One new container
  appears, so it pays while `C < −split_bits`.

  **It is an extraction, not a partition, and the difference is load-bearing.** The remainder stays
  in the directory rather than moving into a group of its own, so exactly one container is created
  and the structure term costs `C` once rather than `C·m`. Pricing a split as an `m`-way partition —
  which the first version of this did — overcharges every split by up to `C·(m−1)`. It is also the
  operation people mean by "these files belong in a subdirectory": the answer names a subset, not a
  repartitioning of everything.

  **No shape constraints, because the arithmetic does not need any.** A one-child subset prices at
  *exactly* 0.0 — the parent swaps a file child for a directory child so its branching is unchanged,
  and the new directory's `log2(1)` is zero — verified across all 2546 single-child extractions in
  the corpus. It is therefore `+C` and never pays, without being excluded. And a *majority* subset
  is a real proposal, not a degenerate one: burying date-fns's 382 cold `fp` children so its 16 hot
  ones sit at the top is the same tree as promoting the 16, and which phrasing is better advice is
  not something a bit count knows. Both sides of every cut are kept and ranked.

  (An earlier version imposed both constraints, justified by failure modes — a singleton peeled off
  `fp`, a 382-of-398 box — that belonged to the *partition* formulation, where the remainder was
  also boxed. Neither can occur under extraction. Carrying a justification across a change of
  operation is its own lesson.)

Both are checked against a rebuilt graph for every container in flask, rich, zod, vite and
tanstack-router, including deliberately arbitrary subsets nothing would propose, as is every move
delta — the incremental arithmetic is the module's whole reason to exist, so it is pinned
differentially rather than by hand-derived constants.

What the corpus says when these are priced is in [FINDINGS.md](FINDINGS.md), "Calibrating `C`".

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

## PR queue

Priority order. Each PR is independently mergeable and leaves CI green.

| # | PR | Why now | Model | Status |
| --- | --- | --- | --- | --- |
| 1 | **7** — the adjudication pilot | The tool's usability rests entirely on it and nothing else queued tests it. The candidate set already exists | Opus 5 | not started |
| 2 | **8** — per-repo configs and a `C` sensitivity table | Turns `C` from a failed calibration into a documented dial, and builds the labelled set PR 7 needs more of | Sonnet 5 | not started, needs 7 |
| 3 | **9** — corpus expansion, screened on tree shape | More findings to adjudicate, and the direct test of whether flat Python is a size effect or a language effect | Sonnet 5 | not started, needs 8 |
| — | **6** — meridian2 | Still the point of the exercise, but it is an *application* of the tool and reads as noise until PR 7 says whether the findings are trustworthy | Opus 5, plan mode | deferred until 7 |
| — | **4a** — `model/graph.py` + `model/metrics.py` + `bit_cost` | — | Opus 5 | **done** |
| — | **4b** — `report/run.py` with `--exclude` | — | Sonnet 5 | **done** |
| — | **4c** — `--freeze`, local optimality, container verdicts, split search | — | Opus 5 | **done** |
| — | **5a** — TS extractor → zod, date-fns, vite, tanstack-router | — | Sonnet 5 | **done** |
| — | ~~**4d** — figures + permutation port~~ | **Dropped.** The permutation baseline compared the *integer* cost against a shuffle, and 4c superseded both halves: the integer cost is not the objective, and "better than random" turned out to say almost nothing next to the absolute tests. Porting it would re-validate a result we no longer consider informative. Figures can be drawn when there is a finding worth drawing | — | **dropped** |

### PR 7 — the adjudication pilot

**The question:** can an LLM tell a finding from a false positive? Every open risk in this document
now routes through that, and no amount of further measurement answers it.

The three findings all fail the same way and need the same judgement call:

| finding | the failure | what adjudication has to decide |
| --- | --- | --- |
| `costs` — this directory shouldn't exist | zod's `v4/locales` is the corpus's largest verdict and is a legitimate taxonomy | junk drawer or category? |
| `splits` — these files belong in a subdirectory | both sides of a cut are offered and at most one is sensible | which candidate, if any? |
| `movers` — this file is in the wrong directory | large-directory eviction pressure makes every member of a big directory want out | real, or an artifact of the objective? |

**Why it is cheap:** the candidate set exists. `containers.csv` (98 `costs` rows), `splits.csv` (39
ranked proposals) and `movers.csv` are already generated, and the naming pass has to run over these
same groups regardless, so the adjudicator and the namer are one component.

**Two signals to compare, not one.** plan.md's original idea was naming alone. PR 4c found a second,
free, deterministic one — structural equivalence, where a taxonomy's members have near-identical
neighbourhoods (`v4/locales` and `_unicode_data` both score 1.000) and a junk drawer's are disjoint
(`shared`, `helpers`, `_lib` all score 0.000). Run both and compare. If the cheap signal reproduces
the LLM's judgement, ship the cheap one and keep the LLM for naming.

**Acceptance:** a hand-labelled verdict for every `costs` row and every `splits` candidate in the
corpus, the two signals' agreement with it, and an honest precision/recall. If neither separates
them, the tool has no usable output and `FINDINGS.md` should say no.

### PR 8 — per-repo configs, and `C` as a dial

**`--exclude` and `--freeze` are per-repo facts and belong in the repo.** Move them out of ad-hoc
command lines into `corpus/manifest.toml`, so a run is reproducible and the config is reviewable.
This is the `eslint-config-*` shape, and it is also the honest way to record what each repo needed.

**The size of the config is itself a finding.** plan.md's escape-hatch principle — "do not forbid
these, *count* them; 3 global namespaces vs 30 is a quality signal" — applies directly. A repo
needing fifteen freeze patterns has that much convention-governed structure.

**`C` gets a sensitivity table, not a fitted value.** 4c settled that `C` is a per-repo knob with a
160× spread, so fitting it per repo is worse than useless — it is circular. Choosing the `C` that
makes a repo look already-optimal guarantees the tool says nothing about it. `C` is a policy dial
like `max-line-length`: the useful artifact is "at `C = 1` the tool reports these findings, at
`C = 8` these", so a user picks how opinionated they want it.

### PR 9 — corpus expansion, screened on tree shape

**Screen before extracting.** Directory count, depth range and modal depth share come from
`git ls-files` alone. Require ≥20 directories, modal depth share <0.6, depth range ≥4. The current
Python three fail all three tests and contributed nothing; they were chosen for entrypoint count and
call-graph depth, which turned out not to be the axis that matters.

**Include large Python** — django, sqlalchemy, airflow, home-assistant. grimp already works, so it is
nearly free, and it is the direct test of whether flat Python is a size effect or a language effect.
The current evidence cannot separate those.

**Purpose is the evaluation set, not the aggregate percentages.** 1446 directories are already
priced; the earns/neutral/costs split is not what is short. What is short is *labelled* examples —
roughly two `costs` candidates per normal repo, so ten repos roughly triples PR 7's evidence.

Go last, because ten more repos without an adjudicator is 200 more findings nobody can judge.

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
- **Deviation 1: edges are deduplicated** in `metrics.edges()`. See [FINDINGS.md](FINDINGS.md) — this is
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
  date-fns's single-file-directory convention distort every number in [FINDINGS.md](FINDINGS.md) and in
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

**Deviation: the calibration measure changed, and the file-level sweep is reported but not used.**
The spec's measure (local optimality vs `C`) is still computed and still emitted — it is the
artifact the shipped tool wants — but it appears in `calibration.md` under "for the record" with
the monotonicity argument, because it identifies nothing. Container stability is what the sweep
actually varies. This is a correction to the experiment, not a widening of it: the same objective,
the same corpus, the same question, asked of the object `C` is actually about.

**Also shipped, both found while checking the above.** `model/graph.py::reroot` strips the
directory prefix every file shares, because leaving it in left the *root* with branching 1 and made
it nearly-free parking for any widely-shared file. And `metrics.edges()` now drops self-edges,
which the bit cost had been charging `log2(branching)` to address a file from inside itself. Both
stories, and what they did to the numbers, are in [FINDINGS.md](FINDINGS.md).

**The results — what `C` turned out to be, what the three findings look like, and the two
conclusions this PR published and then had to withdraw — are in [FINDINGS.md](FINDINGS.md).**

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

**The selection axis was wrong, and that is PR 4c's cheapest lesson.** Every metric here is a
statement about a *tree*, so what matters is directory-tree depth and branching — which nobody
screened for. rich has 100 files and 1 directory; vite has 388 files and 129. The three Python
picks contributed nothing for that reason, not because of anything about Python. PR 9 screens on
tree shape instead (≥20 directories, modal depth share <0.6, depth range ≥4), all computable from
`git ls-files` before extracting anything. See [REPOS.md](REPOS.md).

**The selection axis was wrong, and that is PR 4c's cheapest lesson.** Every metric this project
computes is a statement about a *tree*, so what matters is directory-tree depth and branching —
which nobody screened for. rich has 100 files and 1 directory; vite has 388 files and 129. The
three Python picks contributed nothing for that reason, not because of anything about Python. PR 9
screens on tree shape instead (≥20 directories, modal depth share <0.6, depth range ≥4), all of it
computable from `git ls-files` before extracting anything. What each repo actually taught us is in
[REPOS.md](REPOS.md).

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
blocking question; see [FINDINGS.md](FINDINGS.md), "Open questions".** Ask an LLM to name each
extracted cluster; if the best
it can do is `utils`, `helpers`, or `misc`, reject the cut and try the next candidate. Unnameable
means incoherent. This turns the fuzziest step into a quality gate on the thing graph metrics
cannot evaluate — and it is the only lever available for the "grouping mutually-unrelated things"
blind spot.

---


---

## Where the rest lives

- **[FINDINGS.md](FINDINGS.md)** — what running it taught us: Phase 0 results, the `C` calibration,
  the three findings the tool can produce, the junk-drawer/taxonomy discriminator, open questions
  and open risks.
- **[REPOS.md](REPOS.md)** — per-repo numbers and what each corpus repo contributed.
