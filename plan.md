# Dependency-Structure Linter — Design & Prototyping Plan

Derived from a design conversation. This document is self-contained: it states the model,
the reasoning behind it, the alternatives already rejected, and a phased plan whose early
steps can falsify the premise cheaply.

**Goal:** derive file and directory structure from the dependency graph, rather than
asserting a hand-written structure. Naming is a later, easy problem (LLMs are good at it).

**Why a separate repo:** the corpus checkouts and their extracted dependency graphs should be
cached, and the extractors are reusable across projects. The application repo being analyzed
(`meridian2`) is a _row in the corpus_, not the host.

---

## Hypothesis to falsify

> Well-engineered repositories already place files where this rule would place them.

If true, the tool enforces something real and has an adoption argument. If false, the rule is
an aesthetic preference and that is worth learning before building a placement engine.

Phase 0 tests this with no placement engine, no dominators, and no symbol resolution.

---

## The model

### Containment tree

One tree holds everything: directories contain directories, directories contain files, files
contain symbols. The same rule applies at every level — this is the fractality requirement.

A container's **face** is the address by which outsiders may reach into it:

| Container | Face                                                                                 |
| --------- | ------------------------------------------------------------------------------------ |
| directory | `index.ts` / `__init__.py` / `mod.rs` / the Go package itself                        |
| file      | its export list — exported symbols sit _at_ the face, non-exported ones are interior |

Anything at the face is reachable from outside at no extra cost. Anything behind it is interior.

### Cost function

For a dependency edge `u → v`:

> **cost(u → v) = the number of containers strictly between `LCA(u, v)` and `v`**
> — i.e. the number of faces you must cross to name `v` from `u`.

Ascent is always free. All cost is in the descent.

| Edge                                                | Faces crossed      | Cost |
| --------------------------------------------------- | ------------------ | ---- |
| `a/x` → `a/y` (sibling file)                        | —                  | 0    |
| `a/x` → `shared.ts` at root (pure ascent)           | —                  | 0    |
| `a/x` → `a/sub/index` (own child's face)            | `sub`              | 1    |
| `b/x` → `a/index` (sibling's face)                  | `a`                | 1    |
| `a/x` → `a/sub/deep/y` (into own subtree, deep)     | `sub`, `deep`      | 2    |
| `b/x` → `a/sub/deep/y` (into a stranger's interior) | `a`, `sub`, `deep` | 3    |

Ownership never enters the definition — only geometry does. Reaching into your own grandchild
is cheaper than reaching into a stranger's grandchild solely because the LCA is closer, which
is the correct reason. Nothing is categorically forbidden; cost plus the escape hatches below
do the enforcing.

### Objective

```
minimize  Σ cost(e)  over all dependency edges e
subject to:
  - max/min lines per file
  - max/min files per directory
  - SCC members must share a container
  - AST containment constraints (a method cannot leave its class, etc.)
tiebreak: among equal-cost solutions, prefer the deeper placement
```

Depth is **not** a separate objective. It falls out: a file nothing imports has cost 0 wherever
it sits, so the tiebreak buries it as deep as the size bounds allow (free encapsulation). A
widely-shared file pays for every face between it and its users, so it floats up to where they
reach it by pure ascent. Private sinks, shared floats, one number.

The size bounds are the only repulsive force. Without them, minimizing cost collapses
everything into a single container at cost 0.

### Why this yields caller-LCA

Take `util` imported by `a/x` and `b/y`:

- at the root — both reach it by pure ascent → **cost 0**
- inside `a/` — `a/x` is a sibling (0), but `b/y` must cross `a`'s face → **cost 1**

Minimizing cost picks the root, which is `LCA(a/x, b/y)`. Generalizing: **caller-LCA is the
deepest zero-cost placement.** It is a consequence of the cost function, not an assumption, and
the depth tiebreak never fights it — anything deeper than the LCA necessarily costs something.

### Fractality

Requirement: running the algorithm on a subtree must produce, for that subtree, the same result
as running it on the whole tree.

This holds, and the boundary condition reduces to **one bit per node: is it referenced from
outside the subtree?** Model each externally-referenced node as having one virtual importer at
the subtree's root. Every LCA inside the subtree is then unchanged, every cost is unchanged, and
the local result is exactly the global one restricted to that subtree.

Consequences worth designing around:

- **Incremental checking.** Edit inside `a/sub/` and only `a/sub/` needs re-running, unless its
  public face changed.
- **Local hysteresis.** A proposed move stays confined to one subtree unless it alters a face.
- **Parallelism.** Subtrees are independent given their face bits.

### Escape hatches (needed for real adoption)

1. **Declared global faces.** Let the user mark directories as globally visible (`shared/`,
   `lib/`, the `@/` alias roots). Edges into them cost 0 regardless of geometry. Do not forbid
   these — **count** them. "This repo declares 3 global namespaces" vs. 30 is itself a quality
   signal, and it turns an unbounded escape hatch into a number that can be driven down.
2. **Ratchet, not threshold.** Never require zero. Record the current total cost and require it
   not to increase. This is how teams actually adopt `strictNullChecks` and coverage gates, and
   it makes the tool useful on day one against a repo that violates it everywhere.
3. **Advisory by default.** Report a score and a small number of high-confidence moves. Do not
   rewrite the tree. See "churn" under Rejected alternatives.

---

## Rejected alternatives — do not re-derive these

**Dominance / immediate dominators (`idom`) as the placement rule.** Appealing because it is
canonical and needs no fixpoint, and it coincides with caller-LCA in few-entrypoint repos. Three
independent problems:

- It over-hoists. Any symbol reachable from two independent entrypoints has `idom = root`. In a
  library where every public export is an entrypoint, a private helper shared by two exports
  hoists out of its own module. The wider the entrypoint set, the flatter the result — so it
  says least exactly where you most need an answer.
- It answers a _visibility_ question ("what gatekeeps access to this?"), not a placement one.
  `idom` may name a node that is not even a caller.
- **It is not fractal.** It can decide a node belongs outside the subtree entirely, which a
  subtree-local run cannot see, because dominance is defined by paths from global entrypoints.

Dominance is still the right tool for the _visibility_ question — how private a thing can be,
which scope it can be hidden inside. Keep it for that; do not use it to place.

**A separate lateral / clustering objective.** Introduced to answer "what goes with what."
Unnecessary: co-location is a consequence of minimizing Σcost, not a second goal.

**Distance ≤ 1 (only parent, sibling, or child).** Forbids `../../shared/utils`, which is a pure
ascent and the most common sane pattern in real repos. Satisfiable only via per-level re-export
relay chains (boilerplate, hides the true dependency) or by flattening. No real module system
restricts distance — Go, Node, and Rust all permit unlimited ascent and restrict only descent
into another subtree's interior.

**Discounting type-only edges.** Deliberately rejected for simplicity: types are weighted the
same as any other import. _But_ report the SCC size distribution both with and without type
edges as a diagnostic — type-only cycles are common and harmless, and since SCC condensation
collapses cycles into single nodes, an inflated SCC can swallow a chunk of the hierarchy. If a
repo's structure collapses, this tells you whether it is real coupling or type recursion.

**Recomputing the layout from scratch (formatter model).** The killer practical failure. Adding
one function can flip a clustering decision and move dozens of declarations, destroying blame
and conflicting with every open branch. Any real version computes a _diff from the current
layout_ with hysteresis, and scales the acceptance threshold to blast radius:

| Tier | Change                              | Import paths affected                |
| ---- | ----------------------------------- | ------------------------------------ |
| 0    | reorder within a file               | none                                 |
| 1    | move symbol between files, same dir | cross-file callers only (free in Go) |
| 2    | move file between dirs              | all callers                          |
| 3    | create/split/delete a directory     | all callers + mental model           |

Tier 0 is safe to apply automatically and is independently useful. Tier 3 should need to beat
the status quo by a landslide.

---

## Phases

### Phase 0 — measurement only (target: one afternoon)

No placement engine, no dominators, no SCC, no symbol resolution. Just the import graph and the
existing file paths.

Per repo, compute over the **actual** layout:

1. **Cost histogram.** Fraction of edges at cost 0, 1, 2, 3+.
2. **Cross-face entries.** Number of distinct directories entered from outside — the direct
   measure of how many barrels the rule would demand.
3. **Depth vs. fan-in correlation.** Spearman between a file's directory depth and its number of
   distinct importers. Tests "shared goes shallow" head-on. Should be strongly negative.
4. **Total cost**, normalized by edge count, for cross-repo comparison.

**Decision point.** If well-engineered repos are overwhelmingly cost-0/1 and the depth/fan-in
correlation is strongly negative, the rule is descriptive and worth enforcing. If they are heavy
on cost 2+, the rule is aesthetic — which is worth knowing before building anything else.

### Phase 1 — extractors

Do not write parsers. Three existing tools emit the graph; normalize all of them into one schema
on day one so every algorithm is written once:

```json
{
  "id": "src/calendar/agenda.ts",
  "kind": "file",
  "imports": ["src/model/index.ts"],
  "type_only": []
}
```

| Language   | Tool                                    | Notes                                   |
| ---------- | --------------------------------------- | --------------------------------------- |
| Go         | `go list -json ./...`                   | complete, correct, zero parsing         |
| Python     | stdlib `ast` (~40 lines), or `grimp`    | grimp is the engine under import-linter |
| TypeScript | `dependency-cruiser --output-type json` | handles tsconfig path resolution        |

Cache both the corpus checkouts (pinned commits) and the extracted JSON. Re-extraction should
never be on the inner loop.

**Barrel handling (TypeScript, mandatory).** Detect pure re-export files and splice them out —
rewire each edge to its real target. Otherwise `a → @/feature → b` records the distance through
the barrel and the barrel becomes an artificial hub with enormous fan-in. `meridian2` is
barrel-heavy by design, so this is not hypothetical. Report at least one repo both with and
without splicing so you know how much it moved.

**Go is a useful control.** You cannot import a file in Go, only a package, so every Go import
lands on a face by construction. Costs above 1 are impossible except by crossing `internal/`.
That makes Go's cost-1 fraction a pure measure of how much sibling coupling well-regarded Go
code actually tolerates — and `internal/` is a hand-labelled answer key for the visibility rule.

### Phase 2 — graph algorithms

Write the prototype in Python: `networkx` already provides `strongly_connected_components`,
`immediate_dominators`, and LCA. Steps 2 and 3 become library calls rather than an
implementation of Lengauer–Tarjan. The extractors are separate processes emitting JSON, so the
host language does not constrain which ecosystems can be analyzed.

1. SCC condensation (Tarjan) → DAG. **Report the SCC size distribution.** Small SCCs mean the
   file-level import graph is adequate. One SCC swallowing a large fraction of the repo means
   file granularity is too coarse and symbol-level resolution is required. This is the empirical
   answer to "do we need the call graph?" — not a decision to make up front.
2. Cost evaluation over the actual layout (already done in phase 0, now on condensed nodes).
3. Dominators, for the _visibility_ report only — which files could be made private, and to what
   scope. Not for placement.

### Phase 3 — derived layout and agreement

Produce a layout from the model and compare it to the real one.

- **Adjusted Rand index** between the derived directory partition and the actual one.
- **Permutation baseline** — the important part. Keep each repo's real directory tree shape and
  randomly permute which file sits in which slot. This preserves size, depth distribution, and
  tree shape while destroying the correspondence, so any measured signal is attributable to
  placement rather than to the repo being large or deep. A few hundred permutations gives an
  effect size, not just a number. Without it, an ARI of 0.4 is uninterpretable.
- **Free secondary baseline:** the same repo at an older commit. If maintainers improved
  structure over time, the metric should move in the right direction — directional evidence with
  nothing to label.

Do **not** try to source "badly engineered" repos for contrast. Confounded by size, age,
language, and domain, and the labelling is subjective.

### Later (only if phases 0–3 justify it)

- Eviction: when a container overflows, which node leaves? Use a lexicographic ladder with no
  tunable parameters, not a weighted score: (1) never split an SCC; (2) prefer a node nothing
  else in the container depends on; (3) prefer one already at the face (creates no new API
  surface); (4) prefer the largest; (5) deterministic tie-break by name. The evicted set must be
  _connected_ in the dependency graph, or you get a junk drawer rather than a module.
- Hysteresis and the accepted/rejected-move lockfile (also pins generated names, which otherwise
  drift every run).
- Symbol-level granularity, if the phase-2 SCC distribution says it is needed.
- **Naming as a validator, not a decoration.** Ask an LLM to name each extracted cluster; if the
  best it can do is `utils`, `helpers`, or `misc`, reject the cut and try the next candidate.
  Unnameable means incoherent. This turns the fuzziest step into a quality gate on the thing
  graph metrics cannot evaluate.

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
| **meridian2**                | TS     | the one repo where the intended structure is known          |

`date-fns` is the important TS pick — it is the lodash shape in a TS-native codebase, so it
probes the flattening case directly. `meridian2` is the calibration point: its `CLAUDE.md`
states the intended invariants explicitly, so disagreements are interpretable rather than
merely numeric.

---

## Suggested repo layout

```
extractors/       one per language, each emits the normalized JSON schema
corpus/           pinned commits + cached extracted graphs (gitignored payload, checked-in manifest)
model/            cost function, LCA, SCC, placement — language-agnostic, operates on JSON
report/           metrics, permutation baselines, per-repo tables
```

Phase 0 does not need this structure — it is one script. Build the structure when phase 1
justifies it.

---

## Open questions

- **Public-face detection per language.** Needed for the fractality bit and for barrel handling.
  Go is trivial (exported identifiers); TS needs re-export analysis; Python `__init__.py` is
  convention-dependent.
- **Monorepos:** analyze per-package or whole? Package boundaries are a strong signal of intended
  structure and might serve as additional ground truth.
- **Are `min` constraints needed at all,** or only `max` lines/files? A minimum may be doing no
  work once the cost function is in place.
- **Do symbols matter?** Answered empirically by the phase-2 SCC size distribution, not by
  argument.
- **Adoption posture.** The tool most likely to be used is an advisory metric plus a handful of
  high-confidence moves (the `knip` / `dependency-cruiser` shape), not a formatter. The strongest
  niches are agent-written code (no aesthetic ownership, sprawls badly), greenfield, and monorepo
  package splits (rare, high-stakes, genuinely uncertain). A fresh motivation worth testing:
  file boundaries are retrieval chunks for coding agents, so minimizing cross-file coupling
  directly reduces how much irrelevant code an agent loads — measurable in a way aesthetics
  never were.
