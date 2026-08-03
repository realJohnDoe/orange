"""Extract a normalized import graph from the TypeScript corpus repos.

Uses dependency-cruiser (the programmatic API, not the CLI) via extract.mjs, a
Node subprocess. extract.mjs is a dumb adapter: it emits exactly what
dependency-cruiser saw -- resolved targets, type-only flags, couldNotResolve --
with no filtering, normalization, or counting. All policy lives here, in
build_graph(), which is a pure function of (payload, entry, checkout) and is
therefore testable from a synthetic dict with no node in the loop.

No `pnpm install` for any corpus repo: only internal edges matter, so an
unresolved external import is dropped and counted into
Stats.unresolved_imports rather than chased down. That's the first Stats field
this extractor fills that the Python extractor never needed (grimp always
resolves-or-classifies-external; dependency-cruiser without node_modules
genuinely fails to resolve some specifiers).

Two enhanced-resolve options are load-bearing (see plan.md / plans/steps-4-6.md
for how these were discovered against zod specifically, but both are applied
generically, not as a zod special case):
  - extensionAlias: NodeNext-style TS writes `./foo.js` pointing at `foo.ts`.
  - a package self-reference alias, derived per root from the nearest
    package.json's "name" field: a package importing its own name (`zod/v4`)
    needs to resolve back into its own src/ without node_modules.
Both live in extract.mjs since they're resolver config, not policy.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Annotated

import typer

from corpus.sync import RepoEntry, load_manifest
from extractors.classify import is_barrel_ts
from extractors.schema import Graph, Node, Stats, write

GRAPHS_DIR = Path(__file__).resolve().parents[2] / "corpus" / "graphs"
WORKER = Path(__file__).resolve().parent / "extract.mjs"

# .d.ts / .d.mts / .d.cts must be listed even though they end in .ts / .mts / .cts
# too -- str.endswith() matches whichever suffix is checked, and all six are
# genuine source files, so listing them is just being explicit, not overriding
# the shorter suffixes.
_ALLOWED_EXTENSIONS = (
    ".d.ts", ".d.mts", ".d.cts",
    ".ts", ".tsx", ".mts", ".cts",
    ".js", ".jsx", ".mjs", ".cjs",
)


def _slashify(path: str) -> str:
    """Forward-slash form of a dependency-cruiser path, no invariants enforced.

    Safe to call on *any* string the worker emits, including ones that will
    turn out to be out of scope -- see _norm for why that distinction matters.
    """
    return path.replace("\\", "/")


def _norm(path: str) -> str:
    """Repo-root-relative POSIX path, with invariants enforced.

    Call this only on a path already confirmed in-scope (i.e. about to become
    a real Node). A path can legitimately contain `..` or be nonsensical
    relative to baseDir when it's *out* of scope -- e.g. vite's own test
    suite has a type-only import reaching into `../../../../../dist/...`, its
    own never-built output. dependency-cruiser resolves that syntactically
    (couldNotResolve is false) even though no such file exists in a shallow
    checkout. That's an external reference to be dropped and counted, not a
    bug -- so the strict checks only apply once something is known in-scope.

    dependency-cruiser already emits forward slashes on win32 in practice
    (verified against the real zod/date-fns checkouts), so the slash
    conversion here is a cheap invariant rather than a load-bearing one.
    Deliberately not `PurePath`/`Path`: that class is OS-dispatched
    (PureWindowsPath on Windows, PurePosixPath on Linux), so `.as_posix()` on
    a backslash string is a no-op on the ubuntu-latest CI runner -- the same
    trap model/paths.py's docstring calls out for using PurePosixPath
    explicitly.
    """
    posix = _slashify(path)
    if PurePosixPath(posix).is_absolute():
        raise ValueError(f"expected a repo-relative path, got {path!r}")
    if ".." in PurePosixPath(posix).parts:
        raise ValueError(f"in-scope path escapes its root: {path!r}")
    return posix


def _has_allowed_extension(path: str) -> bool:
    return path.endswith(_ALLOWED_EXTENSIONS)


def _run_worker(checkout: Path, roots: tuple[str, ...]) -> dict:
    result = subprocess.run(
        ["node", str(WORKER), str(checkout), *roots],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def build_graph(payload: dict, entry: RepoEntry, checkout: Path) -> Graph:
    def in_scope(node_id: str) -> bool:
        slashed = _slashify(node_id)
        return _has_allowed_extension(slashed) and any(
            slashed == root or slashed.startswith(f"{root}/") for root in entry.roots
        )

    kept_ids = {_norm(mod["source"]) for mod in payload["modules"] if in_scope(mod["source"])}

    unresolved = 0
    external_dropped = 0
    nodes: list[Node] = []
    for mod in payload["modules"]:
        source = _slashify(mod["source"])
        if source not in kept_ids:
            continue

        imports: list[str] = []
        type_only: list[str] = []
        for dep in mod["dependencies"]:
            if dep["couldNotResolve"]:
                unresolved += 1
                continue
            if not in_scope(dep["resolved"]):
                external_dropped += 1
                continue
            target = _norm(dep["resolved"])
            if target not in kept_ids:
                external_dropped += 1
                continue
            imports.append(target)
            if dep["typeOnly"]:
                type_only.append(target)

        nodes.append(
            Node(
                id=source,
                kind="file",
                is_barrel=is_barrel_ts((checkout / source).read_text(encoding="utf-8")),
                imports=tuple(sorted(imports)),
                type_only=tuple(sorted(type_only)),
            )
        )

    return Graph(
        repo=entry.name,
        lang="ts",
        commit=entry.commit,
        extractor="dependency-cruiser",
        roots=entry.roots,
        nodes=tuple(sorted(nodes, key=lambda n: n.id)),
        stats=Stats(unresolved_imports=unresolved, external_imports_dropped=external_dropped),
    )


def extract(entry: RepoEntry) -> Graph:
    checkout = entry.checkout_path
    if not checkout.is_dir():
        raise FileNotFoundError(f"{entry.name}: not synced -- run corpus/sync.py first")
    payload = _run_worker(checkout, entry.roots)
    return build_graph(payload, entry, checkout)


def main(
    repo: Annotated[
        str | None, typer.Option(help="only extract this repo (default: all TypeScript repos)")
    ] = None,
) -> None:
    entries = [e for e in load_manifest() if e.lang == "ts"]
    if repo is not None:
        entries = [e for e in entries if e.name == repo]
        if not entries:
            raise typer.BadParameter(f"no TypeScript repo named {repo!r} in the manifest")

    for entry in entries:
        graph = extract(entry)
        out = GRAPHS_DIR / f"{entry.name}.json.gz"
        write(graph, out)
        edges = sum(len(n.imports) for n in graph.nodes)
        type_only = sum(len(n.type_only) for n in graph.nodes)
        barrels = sum(n.is_barrel for n in graph.nodes)
        total_seen = edges + graph.stats.unresolved_imports
        unresolved_ratio = graph.stats.unresolved_imports / total_seen if total_seen else 0.0
        print(
            f"{entry.name:16} {len(graph.nodes):5} nodes {edges:5} edges "
            f"({type_only} type-only, {barrels} barrels, "
            f"{unresolved_ratio:.1%} unresolved) -> {out.name}"
        )


if __name__ == "__main__":
    typer.run(main)
